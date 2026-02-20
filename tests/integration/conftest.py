import logging
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Lock

import pytest
import requests
from git import Repo

from hermeto.core.scm import GitRepo
from tests.integration.utils import TEST_SERVER_LOCALHOST

from . import utils

log = logging.getLogger(__name__)
lock = Lock()


@pytest.fixture(scope="session")
def test_repo_dir(temp_base_path: Path) -> Path:
    repo_dir = temp_base_path / "integration-tests"
    return repo_dir


@pytest.fixture(scope="session")
def temp_base_path(worker_id: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    # https://pytest.org/en/latest/reference/reference.html#tmp-path-factory-factory-api
    worker_base = tmp_path_factory.getbasetemp()
    return worker_base if worker_id == "master" else worker_base.parent


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
def hermeto_image() -> utils.HermetoImage:
    if not (image_ref := os.environ.get("HERMETO_IMAGE")):
        image_ref = "localhost/hermeto:latest"
        log.info("Building local hermeto:latest image")
        # <arbitrary_path>/hermeto/tests/integration/conftest.py
        #                   [2] <- [1]  <-  [0]  <- parents
        repo_root = Path(__file__).parents[2]
        utils.build_image(repo_root, tag=image_ref)

    hermeto = utils.HermetoImage(image_ref)
    if not image_ref.startswith("localhost/"):
        hermeto.pull_image()

    return hermeto


def _terminate_proc(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@contextmanager
def _pypiserver_context() -> Iterator[None]:
    if (
        os.getenv("CI")
        and os.getenv("GITHUB_ACTIONS")
        or os.getenv("HERMETO_TEST_LOCAL_PYPISERVER") != "true"
    ):
        yield
        return

    pypiserver_dir = Path(__file__).parents[1] / "pypiserver"
    proc = subprocess.Popen([pypiserver_dir / "start.sh"])
    pypiserver_port = os.getenv("PYPISERVER_PORT", "8080")
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
        _terminate_proc(proc)
        raise RuntimeError("pypiserver didn't start fast enough")

    try:
        yield
    finally:
        _terminate_proc(proc)


@contextmanager
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
        or os.getenv("HERMETO_TEST_LOCAL_DNF_SERVER") != "true"
    ):
        yield
        return

    dnfserver_dir = Path(__file__).parents[1] / "dnfserver"
    ssl_port = os.getenv("DNFSERVER_SSL_PORT", "8443")

    proc = subprocess.Popen([dnfserver_dir / "start.sh"])
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
            _terminate_proc(proc)
            raise RuntimeError(e)
    else:
        _terminate_proc(proc)
        raise RuntimeError("DNF server didn't start fast enough")

    try:
        yield
    finally:
        _terminate_proc(proc)


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Remove redundant tests which don't have to run for the latest code change.

    This function implements a standard pytest hook. Please refer to pytest
    docs for further information.
    """
    # do not try to skip tests if a keyword or marker is specified
    if config.getoption("-k") or config.getoption("-m"):
        return

    skip_mark = pytest.mark.skip(reason="No changes to tested code")
    tests_to_skip = utils.determine_integration_tests_to_skip()
    for item in items:
        if utils.tested_object_name(item.path) in tests_to_skip:
            item.add_marker(skip_mark)

    worktrees_data: dict[str, str] = {
        callspec.id: callspec.params["test_params"].branch
        for item in session.items
        if (callspec := getattr(item, "callspec", None))
        and "test_params" in callspec.params
        and skip_mark not in item.own_markers
    }

    test_repo_url = os.getenv(
        "HERMETO_TEST_INTEGRATION_TESTS_REPO",
        "https://github.com/hermetoproject/integration-tests.git",
    )

    base = session.config._tmp_path_factory.getbasetemp()
    if os.getenv("PYTEST_XDIST_WORKER_COUNT", "0") != "0":
        base = base.parent
    repo_dir = base / "integration-tests"

    with lock:
        if not repo_dir.exists():
            repo = Repo.clone_from(url=test_repo_url, to_path=repo_dir)
            for name, branch in worktrees_data.items():
                repo.git.worktree("add", "--track", "-b", name, name, f"origin/{branch}")
                worktree = GitRepo(repo_dir / name)
                worktree.submodule_update(init=True, force_reset=True, recursive=True)


def pytest_configure(config: pytest.Config) -> None:
    """Start pypiserver and dnfserver once in the master process (controller or single process)."""
    worker_id = os.getenv("PYTEST_XDIST_WORKER", "master")
    if worker_id != "master":
        return

    stack = ExitStack()
    try:
        stack.enter_context(_pypiserver_context())
        stack.enter_context(_dnfserver_context())
    except Exception:
        stack.close()
        raise
    setattr(config, "_hermeto_exit_stack", stack)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Stop pypiserver and dnfserver started in pytest_configure."""
    stack = getattr(config, "_hermeto_exit_stack", None)
    if stack is not None:
        stack.close()
