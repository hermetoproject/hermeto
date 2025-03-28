import logging
from pathlib import Path
from typing import List

import pytest

from . import utils

log = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                branch="npm/bundled-lockfile3",
                packages=({"path": ".", "type": "npm"},),
                check_vendor_checksums=False,
            ),
            id="npm_bundled_lockfile3",
        ),
        pytest.param(
            utils.TestParameters(
                branch="npm/yarn-registry-lockfile3",
                packages=({"path": ".", "type": "npm"},),
                check_vendor_checksums=False,
            ),
            id="npm_yarn_registry_lockfile3",
        ),
    ],
)
def test_npm_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Smoketest for npm offline install development.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                branch="npm/smoketest-lockfile2",
                packages=({"path": ".", "type": "npm"},),
                check_vendor_checksums=False,
            ),
            [],
            [],
            id="npm_smoketest_lockfile2",
        ),
        pytest.param(
            utils.TestParameters(
                branch="npm/smoketest-lockfile3",
                packages=({"path": ".", "type": "npm"},),
                check_vendor_checksums=False,
            ),
            [],
            [],
            id="npm_smoketest_lockfile3",
        ),
        pytest.param(
            utils.TestParameters(
                branch="npm/multiple-dep-versions",
                packages=({"path": ".", "type": "npm"},),
                check_vendor_checksums=False,
            ),
            [],
            [],
            id="npm_multiple_dep_versions",
        ),
        pytest.param(
            utils.TestParameters(
                branch="npm/aliased-deps",
                packages=({"path": ".", "type": "npm"},),
                check_vendor_checksums=False,
            ),
            [],
            [],
            id="npm_aliased_deps",
        ),
        pytest.param(
            utils.TestParameters(
                branch="npm/dev-optional-peer-deps",
                packages=({"path": ".", "type": "npm"},),
                check_vendor_checksums=False,
            ),
            [],
            [],
            id="npm_dev_optional_peer_deps",
        ),
        pytest.param(
            utils.TestParameters(
                branch="npm/multiple-packages",
                packages=(
                    {"path": "first_pkg", "type": "npm"},
                    {"path": "second_pkg", "type": "npm"},
                    {"path": "third_pkg", "type": "npm"},
                ),
                check_vendor_checksums=False,
            ),
            [],
            [],
            id="npm_multiple_packages",
        ),
    ],
)
def test_e2e_npm(
    test_params: utils.TestParameters,
    check_cmd: List[str],
    expected_cmd_output: str,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    End to end test for npm.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )

    utils.build_image_and_check_cmd(
        tmp_path,
        test_repo_dir,
        test_data_dir,
        test_case,
        check_cmd,
        expected_cmd_output,
        hermeto_image,
    )
