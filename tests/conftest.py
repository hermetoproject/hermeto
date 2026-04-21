# SPDX-License-Identifier: GPL-3.0-only
import os

import pytest

from tests.integration.utils import DEFAULT_INTEGRATION_TESTS_REPO


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register custom CLI options for Hermeto integration tests."""
    group = parser.getgroup("hermeto integration", "hermeto integration test options")
    group.addoption(
        "--hermeto-integration-tests-repo",
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
        "--hermeto-generate-test-data",
        action="store_true",
        default=os.getenv("HERMETO_TEST_GENERATE_DATA") == "1",
        help="Regenerate expected test data files (env: HERMETO_TEST_GENERATE_DATA=1)",
    )
    group.addoption(
        "--hermeto-container-engine",
        action="store",
        default=os.getenv("HERMETO_TEST_CONTAINER_ENGINE", "podman"),
        choices=("podman", "buildah"),
        help="Container engine: podman or buildah (env: HERMETO_TEST_CONTAINER_ENGINE)",
    )
    group.addoption(
        "--hermeto-local-nexus",
        action="store_true",
        default=os.getenv("HERMETO_TEST_LOCAL_NEXUS") == "1",
        help="Start local Nexus for source tests (env: HERMETO_TEST_LOCAL_NEXUS=1)",
    )
    group.addoption(
        "--hermeto-local-nexus-proxy",
        action="store_true",
        default=os.getenv("HERMETO_TEST_LOCAL_NEXUS_PROXY") == "1",
        help="Enable local Nexus proxy for registry tests (env: HERMETO_TEST_LOCAL_NEXUS_PROXY=1)",
    )
    group.addoption(
        "--hermeto-local-nexus-no-cleanup",
        action="store_true",
        default=os.getenv("HERMETO_TEST_LOCAL_NEXUS_NO_CLEANUP") == "1",
        help="Keep Nexus container running after tests (env: HERMETO_TEST_LOCAL_NEXUS_NO_CLEANUP=1)",
    )
