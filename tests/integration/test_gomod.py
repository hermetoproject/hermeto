# SPDX-License-Identifier: GPL-3.0-or-later

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
                branch="gomod/with-deps",
                packages=({"path": ".", "type": "gomod"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="gomod_with_deps",
        ),
        pytest.param(
            utils.TestParameters(
                branch="gomod/without-deps",
                packages=({"path": ".", "type": "gomod"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="gomod_without_deps",
        ),
        # Test case checks if vendor folder with dependencies will remain unchanged in cloned
        # source repo, deps folder in output folder should be empty.
        pytest.param(
            utils.TestParameters(
                branch="gomod/vendor-check-pass",
                packages=({"path": ".", "type": "gomod"},),
                flags=["--gomod-vendor-check"],
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="gomod_vendor_check_correct_vendor",
        ),
        # Test case checks if request will fail when source provided wrong vendor.
        pytest.param(
            utils.TestParameters(
                branch="gomod/vendor-check-fail",
                packages=({"path": ".", "type": "gomod"},),
                flags=["--gomod-vendor-check"],
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=2,
                expected_output=(
                    "PackageRejected: The content of the vendor directory is not "
                    "consistent with go.mod. Please check the logs for more details"
                ),
            ),
            id="gomod_vendor_check_wrong_vendor",
        ),
        # Test case checks if request will fail when source provided empty vendor.
        pytest.param(
            utils.TestParameters(
                branch="gomod/vendor-check-empty",
                packages=({"path": ".", "type": "gomod"},),
                flags=["--gomod-vendor-check"],
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=2,
                expected_output=(
                    "PackageRejected: The content of the vendor directory is not "
                    "consistent with go.mod. Please check the logs for more details"
                ),
            ),
            id="gomod_vendor_check_empty_vendor",
        ),
        # Test case checks if package can be replaced with local dependency
        pytest.param(
            utils.TestParameters(
                branch="gomod/local-deps",
                packages=({"path": ".", "type": "gomod"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="gomod_local_deps",
        ),
        # Test case checks if fetching dependencies will not fail if non-existent package is
        # imported. main.go imports foobar here as a dependency, but foobar was not generated
        # on the source repository with `go generate`. Hermeto should recognize here `main` as
        # a package and `foobar` as its dependency.
        pytest.param(
            utils.TestParameters(
                branch="gomod/generate-imported",
                packages=({"path": ".", "type": "gomod"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="gomod_go_generate_imported",
        ),
        # Test the handling of missing checksums. Hermeto should report them via
        # hermeto:missing_hash:in_file properties in the SBOM.
        # See also https://github.com/cachito-testing/gomod-multiple-modules/tree/missing-checksums
        pytest.param(
            utils.TestParameters(
                branch="gomod/missing-checksums",
                packages=(
                    {"path": ".", "type": "gomod"},
                    {"path": "spam-module", "type": "gomod"},
                    {"path": "eggs-module", "type": "gomod"},
                ),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="gomod_multiple_modules_missing_checksums",
        ),
        # Test case checks if hermeto can process go workspaces properly.
        pytest.param(
            utils.TestParameters(
                branch="gomod/workspaces",
                packages=({"path": "./workspace_modules/hello", "type": "gomod"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="gomod_workspaces",
        ),
    ],
)
def test_gomod_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test fetched dependencies for gomod.

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
        # Test case checks fetching retrodep dependencies, generating environment vars file,
        # building image with all prepared prerequisites and printing help message for retrodep
        # app in built image
        pytest.param(
            utils.TestParameters(
                branch="gomod/e2e-1.18",
                packages=({"path": ".", "type": "gomod"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            ["retrodep", "--help"],
            ["retrodep: help requested"],
            id="gomod_1.18_e2e_test",
        ),
        # Test case checks fetching retrodep dependencies, generating environment vars file,
        # building image with all prepared prerequisites and printing help message for retrodep
        # app in built image. The retrodep module specifies minimum go version 1.21.
        pytest.param(
            utils.TestParameters(
                branch="gomod/e2e-1.21",
                packages=({"path": ".", "type": "gomod"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            ["retrodep", "--help"],
            ["retrodep: help requested"],
            id="gomod_1.21_e2e_test",
        ),
        # Check handling of multiple Go modules in one repository. See the README in the testing
        # repository for more details.
        pytest.param(
            utils.TestParameters(
                branch="gomod/e2e-multiple-modules",
                packages=(
                    {"path": ".", "type": "gomod"},
                    {"path": "spam-module", "type": "gomod"},
                    {"path": "eggs-module", "type": "gomod"},
                ),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            [],  # check using CMD defined in Dockerfile
            [""],
            id="gomod_e2e_multiple_modules",
        ),
        # Check handling of Go modules where the go directive in go.mod is < 1.21. Go versions < 1.21 will not
        # update the go directive in go.mod, but go versions >= 1.21 will and dirty the repository
        pytest.param(
            utils.TestParameters(
                branch="gomod/e2e-1.21-dirty",
                packages=({"path": "twenty", "type": "gomod"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            [],  # check using CMD defined in Dockerfile
            [
                "The cachi2-gomod/twenty module requires minimum go version 1.20",
                "The cachi2-gomod/twentyone module requires minimum go version 1.21",
            ],
            id="gomod_1.20_e2e_dirty_go.mod",
        ),
        pytest.param(
            utils.TestParameters(
                branch="gomod/e2e-1.22-workspace-vendoring",
                packages=({"path": "hi/hiii", "type": "gomod"},),
                flags=["--gomod-vendor"],
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            [],  # check using CMD defined in Dockerfile
            [""],
            id="gomod_1.22_e2e_workspace_vendoring",
        ),
    ],
)
def test_e2e_gomod(
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
    End to end test for gomod.

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
