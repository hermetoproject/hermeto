import contextlib
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Iterator

import pytest
import requests
from git import Repo

from tests.integration.utils import TEST_SERVER_LOCALHOST

from . import utils

log = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def test_repo_dir(tmp_path_factory: pytest.FixtureRequest) -> Path:
    test_repo_url = "https://github.com/cachito-testing/integration-tests.git"
    # https://pytest.org/en/latest/reference/reference.html#tmp-path-factory-factory-api
    repo_dir = tmp_path_factory.mktemp("integration-tests", False)  # type: ignore
    Repo.clone_from(url=test_repo_url, to_path=repo_dir, depth=1, no_single_branch=True)
    return repo_dir


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """Path to the directory for storing unit test data."""
    return Path(__file__).parent / "test_data"


@pytest.fixture(scope="session")
def top_level_test_dir() -> Path:
    """Path to the top-level tests directory inside our repository.

    This is useful in tests which have to reference particular test data directories, e.g. the
    simple PyPI server which may contain other data that have to be mount to either the cachi2
    image during a test execution or to some other service container we may need for testing.
    """
    return Path(__file__).parents[1]


@pytest.fixture(scope="session")
def cachi2_image() -> utils.Cachi2Image:
    cachi2_image_ref = os.environ.get("HERMETO_IMAGE")
    if not cachi2_image_ref:
        cachi2_image_ref = "localhost/cachi2:latest"
        log.info("Building local cachi2:latest image")
        # <arbitrary_path>/cachi2/tests/integration/conftest.py
        #                   [2] <- [1]  <-  [0]  <- parents
        cachi2_repo_root = Path(__file__).parents[2]
        utils.build_image(cachi2_repo_root, tag=cachi2_image_ref)

    cachi2 = utils.Cachi2Image(cachi2_image_ref)
    if not cachi2_image_ref.startswith("localhost/"):
        cachi2.pull_image()

    return cachi2


# autouse=True: It's nicer to see the pypiserver setup logs at the beginning of the test suite.
# Otherwise, pypiserver would start once the pip tests need it and the logs would be buried between
# test output.
@pytest.fixture(autouse=True, scope="session")
def local_pypiserver() -> Iterator[None]:
    if (
        os.getenv("CI")
        and os.getenv("GITHUB_ACTIONS")
        or os.getenv("HERMETO_TEST_LOCAL_PYPISERVER") != "true"
    ):
        yield
        return

    pypiserver_dir = Path(__file__).parent.parent / "pypiserver"

    with contextlib.ExitStack() as context:
        proc = context.enter_context(subprocess.Popen([pypiserver_dir / "start.sh"]))
        context.callback(proc.terminate)

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
            raise RuntimeError("pypiserver didn't start fast enough")

        yield


@pytest.fixture(autouse=True, scope="session")
def local_dnfserver(top_level_test_dir: Path) -> Iterator[None]:
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

    dnfserver_dir = top_level_test_dir / "dnfserver"

    with contextlib.ExitStack() as context:
        proc = context.enter_context(subprocess.Popen([dnfserver_dir / "start.sh"]))
        context.callback(proc.terminate)

        ssl_port = os.getenv("DNFSERVER_SSL_PORT", "8443")
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
