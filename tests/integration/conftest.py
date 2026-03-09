# SPDX-License-Identifier: GPL-3.0-only
import contextlib
import logging
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests
from filelock import FileLock
from git import Repo

from hermeto.core.utils import copy_directory
from tests.integration.proxy import TEST_NEXUS_PORT, is_local_nexus_proxy_enabled
from tests.integration.utils import DEFAULT_INTEGRATION_TESTS_REPO, TEST_SERVER_LOCALHOST
from tests.nexusserver import DEFAULT_NEXUS_HOST, initialize_nexus

from . import utils

log = logging.getLogger(__name__)

_ENV_VAR_CLI_MAP = [
    ("HERMETO_TEST_INTEGRATION_TESTS_REPO", "--hermeto-integration-tests-repo"),
    ("HERMETO_TEST_IMAGE", "--hermeto-image"),
    ("HERMETO_TEST_LOCAL_PYPISERVER", "--hermeto-local-pypiserver"),
    ("HERMETO_TEST_PYPISERVER_PORT", "--hermeto-pypiserver-port"),
    ("HERMETO_TEST_LOCAL_DNF_SERVER", "--hermeto-local-dnf-server"),
    ("HERMETO_TEST_DNFSERVER_SSL_PORT", "--hermeto-dnfserver-ssl-port"),
    ("HERMETO_TEST_GENERATE_DATA", "--hermeto-generate-test-data"),
    ("HERMETO_TEST_CONTAINER_ENGINE", "--hermeto-container-engine"),
    ("HERMETO_TEST_LOCAL_NEXUS_PROXY", "--hermeto-local-nexus-proxy"),
    ("HERMETO_TEST_LOCAL_NEXUS_NO_CLEANUP", "--hermeto-local-nexus-no-cleanup"),
]


def pytest_configure(config: pytest.Config) -> None:
    """
    - Sync CLI option values to env so existing os.getenv() code sees them.
    - Start pypiserver and dnfserver once in the master process (controller or single process).
    """

    def env_value(cli_opt: str) -> str:
        value = config.getoption(cli_opt)
        if isinstance(value, bool):
            return "1" if value else "0"
        return value

    for env_var, cli_opt in _ENV_VAR_CLI_MAP:
        os.environ[env_var] = env_value(cli_opt)

    # Start pypiserver and dnfserver once in the master process (controller or single process).
    if os.getenv("PYTEST_XDIST_WORKER", "master") == "master":
        stack = contextlib.ExitStack()
        try:
            stack.enter_context(_pypiserver_context())
            stack.enter_context(_dnfserver_context())
        except Exception:
            stack.close()
            raise
        setattr(config, "_hermeto_exit_stack", stack)


@pytest.fixture(scope="session")
def test_repo_dir(tmp_path_factory: pytest.TempPathFactory, worker_id: str) -> Path:
    """Copies the cloned integration tests repository to a temporary directory in
    the base for each worker process.

    :return: Path to the repository copy in the worker's temporary directory."""
    base = tmp_path_factory.getbasetemp()
    target = base / "integration-tests"
    # In single process mode, the test repository is already cloned in the base directory.
    # Fixtures are not executed by the master process in parallel mode. `worker_id` is `"master"`
    # in single process mode.
    if worker_id == "master":
        return target
    return copy_directory(base.parent / "integration-tests", target)


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """Path to the directory for storing unit test data."""
    return Path(__file__).parent / "test_data"


@pytest.fixture(scope="session")
def top_level_test_dir() -> Path:
    """Path to the top-level tests directory inside our repository.

    This is useful in tests which have to reference particular test data directories, e.g. the
    simple PyPI server which may contain other data that have to be mount to either the hermeto
    image during a test execution or to some other service container we may need for testing.
    """
    return Path(__file__).parents[1]


@pytest.fixture(scope="session")
def hermeto_image(tmp_path_factory: pytest.TempPathFactory, worker_id: str) -> utils.HermetoImage:
    """Build or reuse the Hermeto image once per test run when using pytest-xdist."""

    def _build_and_pull_image() -> utils.HermetoImage:
        if not env_image:
            log.info("Building local hermeto:latest image")
            # <arbitrary_path>/hermeto/tests/integration/conftest.py
            #                   [2] <- [1]  <-  [0]  <- parents
            repo_root = Path(__file__).parents[2]
            utils.build_image(repo_root, tag=image_ref)

        hermeto = utils.HermetoImage(image_ref)
        if not image_ref.startswith("localhost/"):
            hermeto.pull_image()
        return hermeto

    env_image = os.getenv("HERMETO_TEST_IMAGE")
    image_ref = env_image or "localhost/hermeto:latest"

    # `True` only in single process mode.
    # Fixtures are not executed by the master process in parallel mode.
    if worker_id == "master":
        return _build_and_pull_image()

    root_tmp_dir = tmp_path_factory.getbasetemp().parent
    fn = root_tmp_dir / "hermeto_image"
    with FileLock(str(fn) + ".lock"):
        if fn.is_file():
            hermeto = utils.HermetoImage(image_ref)
        else:
            hermeto = _build_and_pull_image()
            fn.touch()

    return hermeto


def _terminate_proc(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


@contextlib.contextmanager
def _pypiserver_context() -> Iterator[None]:
    if (
        os.getenv("CI")
        and os.getenv("GITHUB_ACTIONS")
        or os.getenv("HERMETO_TEST_LOCAL_PYPISERVER") != "1"
    ):
        yield
        return

    pypiserver_dir = Path(__file__).parent.parent / "pypiserver"

    with contextlib.ExitStack() as context:
        proc = context.enter_context(subprocess.Popen([pypiserver_dir / "start.sh"]))
        context.callback(_terminate_proc, proc)

        pypiserver_port = os.getenv("HERMETO_TEST_PYPISERVER_PORT", "8080")
        for _ in range(60):
            time.sleep(1)
            try:
                resp = requests.get(f"http://{TEST_SERVER_LOCALHOST}:{pypiserver_port}")
                resp.raise_for_status()
                log.debug(resp.text)
                break
            except requests.RequestException as e:
                log.debug(e)
        else:
            raise RuntimeError("pypiserver didn't start fast enough")

        yield


@contextlib.contextmanager
def _dnfserver_context() -> Iterator[None]:
    def _check_ssl_configuration() -> None:
        # TLS auth enforced
        resp = requests.get(
            f"https://{TEST_SERVER_LOCALHOST}:{ssl_port}",
            verify=f"{dnfserver_dir}/certificates/CA.crt",
        )
        if resp.status_code == requests.codes.ok:
            raise requests.RequestException("DNF server TLS client authentication misconfigured")

        # TLS auth passes
        resp = requests.get(
            f"https://{TEST_SERVER_LOCALHOST}:{ssl_port}",
            cert=(
                f"{dnfserver_dir}/certificates/client.crt",
                f"{dnfserver_dir}/certificates/client.key",
            ),
            verify=f"{dnfserver_dir}/certificates/CA.crt",
        )
        resp.raise_for_status()

    if (
        os.getenv("CI")
        and os.getenv("GITHUB_ACTIONS")
        or os.getenv("HERMETO_TEST_LOCAL_DNF_SERVER") != "1"
    ):
        yield
        return

    dnfserver_dir = Path(__file__).parents[1] / "dnfserver"
    ssl_port = os.getenv("HERMETO_TEST_DNFSERVER_SSL_PORT", "8443")

    with contextlib.ExitStack() as context:
        proc = context.enter_context(subprocess.Popen([dnfserver_dir / "start.sh"]))
        context.callback(_terminate_proc, proc)

        for _ in range(60):
            time.sleep(1)
            try:
                _check_ssl_configuration()
                break
            except requests.ConnectionError:
                # ConnectionResetError is often reported locally, waiting it over
                # helps.
                log.info("Failed to connect to the DNF server, retrying...")
                continue
            except requests.RequestException as e:
                raise RuntimeError(e)
        else:
            raise RuntimeError("DNF server didn't start fast enough")

        yield


def pytest_sessionstart(session: pytest.Session) -> None:
    """Clone the integration tests repository.

    This function implements a standard pytest hook. Please refer to pytest
    docs for further information.
    https://docs.pytest.org/en/stable/reference/reference.html#pytest.hookspec.pytest_sessionstart
    """
    # Skip cloning the test repository for worker processes.
    if os.getenv("PYTEST_XDIST_WORKER", "master") != "master":
        return

    test_repo_url = os.getenv(
        "HERMETO_TEST_INTEGRATION_TESTS_REPO",
        DEFAULT_INTEGRATION_TESTS_REPO,
    )
    tmp_path_factory = getattr(session.config, "_tmp_path_factory")
    base = tmp_path_factory.getbasetemp()
    repo_dir = base / "integration-tests"
    if not repo_dir.exists():
        repo_dir.mkdir(parents=True)
        Repo.clone_from(url=test_repo_url, to_path=repo_dir, depth=1, no_single_branch=True)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Stop pypiserver and dnfserver started in pytest_configure."""
    stack = getattr(config, "_hermeto_exit_stack", None)
    if stack is not None:
        stack.close()


@pytest.fixture(autouse=True, scope="session")
def local_nexusserver(top_level_test_dir: Path) -> Iterator[None]:
    if (os.getenv("CI") and os.getenv("GITHUB_ACTIONS")) or not is_local_nexus_proxy_enabled():
        yield
        return

    with contextlib.ExitStack() as context:
        container, client = initialize_nexus(host=DEFAULT_NEXUS_HOST, port=TEST_NEXUS_PORT)
        context.enter_context(client)

        # Allows inspection of running Nexus container after test completion
        if os.getenv("HERMETO_TEST_LOCAL_NEXUS_NO_CLEANUP") == "1":
            log.info("HERMETO_TEST_LOCAL_NEXUS_NO_CLEANUP=1, Nexus server will NOT be cleaned up")
        else:
            context.enter_context(container)

        log.info("Nexus server ready at %s", client.base_url)
        yield


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip tests that are incompatible with proxy mode."""
    if not is_local_nexus_proxy_enabled():
        return

    for item in items:
        if item.get_closest_marker("no_proxy_mode"):
            item.add_marker(
                pytest.mark.skip(reason="Test incompatible with local Nexus proxy mode")
            )
