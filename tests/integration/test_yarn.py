import logging
from pathlib import Path

import pytest

from . import utils

log = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                branch="yarn/zero-installs",
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=2,
                expected_output="PackageRejected: Yarn zero install detected, PnP zero installs are unsupported by hermeto",
            ),
            id="yarn_zero_installs",
        ),
        pytest.param(
            utils.TestParameters(
                branch="yarn/disallowed-protocols",
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                check_vendor_checksums=False,
                check_deps_checksums=False,
                expected_exit_code=2,
                expected_output="UnsupportedFeature: Found 8 unsupported dependencies, more details in the logs.",
            ),
            id="yarn_disallowed_protocols",
        ),
        pytest.param(
            utils.TestParameters(
                branch="yarn/corepack-install",
                packages=({"path": ".", "type": "yarn"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="Processing the request using yarn@3.6.1",
            ),
            id="yarn_correct_version_installed_by_corepack",
        ),
        pytest.param(
            utils.TestParameters(
                branch="yarn/v4",
                packages=({"path": ".", "type": "yarn"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="Processing the request using yarn@4.5.2",
            ),
            id="yarn_v4",
        ),
        pytest.param(
            utils.TestParameters(
                branch="yarn/immutable-installs",
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                check_vendor_checksums=False,
                check_deps_checksums=False,
                expected_exit_code=1,
                expected_output="The lockfile would have been modified by this install, which is explicitly forbidden.",
            ),
            id="yarn_immutable_installs",
        ),
        pytest.param(
            utils.TestParameters(
                branch="yarn/incorrect-checksum",
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=1,
                expected_output="typescript@npm:5.3.3: The remote archive doesn't match the expected checksum",
            ),
            id="yarn_incorrect_checksum",
        ),
        pytest.param(
            utils.TestParameters(
                branch="yarn/missing-lockfile",
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=2,
                expected_output="Yarn lockfile 'yarn_non_existent.lock' missing, refusing to continue",
            ),
            id="yarn_no_lockfile",
        ),
    ],
)
def test_yarn_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test fetched dependencies for yarn berry.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params, check_cmd, expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                branch="yarn/e2e",
                packages=({"path": ".", "type": "yarn"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            ["yarn", "berryscary"],
            "Hello, World!",
            id="yarn_e2e_test",
        ),
        pytest.param(
            utils.TestParameters(
                branch="yarn/e2e-multiple-packages",
                packages=(
                    {"path": "first-pkg", "type": "yarn"},
                    {"path": "second-pkg", "type": "yarn"},
                ),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            ["yarn", "node", "index.js"],
            "Hello from first package!",
            id="yarn_e2e_test_multiple_packages",
        ),
    ],
)
def test_e2e_yarn(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """End to end test for yarn berry."""
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
