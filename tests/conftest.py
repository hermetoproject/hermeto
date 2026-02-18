# SPDX-License-Identifier: GPL-3.0-only
import os

import pytest

from tests.integration.utils import DEFAULT_INTEGRATION_TESTS_REPO


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register custom CLI options for Hermeto integration tests."""
    group = parser.getgroup("hermeto integration", "hermeto integration test options")
    group.addoption(
        "--integration-tests-repo",
        action="store",
        default=os.getenv("HERMETO_TEST_INTEGRATION_TESTS_REPO", DEFAULT_INTEGRATION_TESTS_REPO),
        help="URL of the integration tests repository to clone (env: HERMETO_TEST_INTEGRATION_TESTS_REPO)",
    )
    group.addoption(
        "--hermeto-image",
        action="store",
        default=os.getenv("HERMETO_TEST_IMAGE", ""),
        help="Hermeto container image reference; build local image if not set (env: HERMETO_TEST_IMAGE)",
    )
    group.addoption(
        "--local-pypiserver",
        action="store_true",
        default=os.getenv("HERMETO_TEST_LOCAL_PYPISERVER") == "1",
        help="Start local pypiserver for pip tests (env: HERMETO_TEST_LOCAL_PYPISERVER=1)",
    )
    group.addoption(
        "--pypiserver-port",
        action="store",
        default=os.getenv("HERMETO_TEST_PYPISERVER_PORT", "8080"),
        help="Port for local pypiserver (env: HERMETO_TEST_PYPISERVER_PORT)",
    )
    group.addoption(
        "--local-dnf-server",
        action="store_true",
        default=os.getenv("HERMETO_TEST_LOCAL_DNF_SERVER") == "1",
        help="Start local DNF server for RPM tests (env: HERMETO_TEST_LOCAL_DNF_SERVER=1)",
    )
    group.addoption(
        "--dnfserver-ssl-port",
        action="store",
        default=os.getenv("HERMETO_TEST_DNFSERVER_SSL_PORT", "8443"),
        help="SSL port for local DNF server (env: HERMETO_TEST_DNFSERVER_SSL_PORT)",
    )
    group.addoption(
        "--netrc-content",
        action="store",
        default=os.getenv("HERMETO_TEST_NETRC_CONTENT", ""),
        help=".netrc content for private PyPI etc. (env: HERMETO_TEST_NETRC_CONTENT)",
    )
    group.addoption(
        "--generate-test-data",
        action="store_true",
        default=os.getenv("HERMETO_TEST_GENERATE_DATA") == "1",
        help="Regenerate expected test data files (env: HERMETO_TEST_GENERATE_DATA=1)",
    )
    group.addoption(
        "--run-all-integration",
        action="store_true",
        default=os.getenv("HERMETO_TEST_RUN_ALL_INTEGRATION_TESTS") == "1",
        help="Run all integration tests, disable skip-by-changed-files (env: HERMETO_TEST_RUN_ALL_INTEGRATION_TESTS=1)",
    )
    group.addoption(
        "--container-engine",
        action="store",
        default=os.getenv("HERMETO_TEST_CONTAINER_ENGINE", "podman").lower(),
        choices=("podman", "buildah"),
        help="Container engine: podman or buildah (env: HERMETO_TEST_CONTAINER_ENGINE)",
    )
