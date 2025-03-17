import logging
import os
from pathlib import Path
from typing import List

import pytest

from hermeto import APP_NAME

from . import utils

log = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                branch="pip/without-deps",
                packages=({"path": ".", "type": "pip"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="pip_without_deps",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/mixed-hashes",
                packages=({"path": ".", "type": "pip"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            # Mixes hashed (URL with `cachito_hash`) and unhashed deps
            id="pip_with_deps_mixed",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/full-hashes",
                packages=({"path": ".", "type": "pip"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="pip_with_deps_full_hashes",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/multiple-packages",
                packages=(
                    {"path": "first_pkg", "type": "pip"},
                    {
                        "path": "second_pkg",
                        "type": "pip",
                        "requirements_files": ["requirements.txt", "requirements-extra.txt"],
                    },
                    {"path": "third_pkg", "type": "pip"},
                ),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="pip_multiple",
        ),
        # Test case checks that an attempt to fetch a local file will result in failure.
        pytest.param(
            utils.TestParameters(
                branch="pip/local-path",
                packages=({"path": ".", "type": "pip"},),
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=2,
                expected_output=(
                    "UnsupportedFeature: Direct references with 'file' scheme are not supported, "
                    "'file:///tmp/packages.zip'\n  "
                    f"If you need {APP_NAME} to support this feature, please contact the maintainers."
                ),
            ),
            id="pip_local_path",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/no-metadata",
                packages=(
                    {"path": ".", "type": "pip"},
                    {"path": "subpath1/subpath2", "type": "pip"},
                ),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="pip_no_metadata",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/yanked",
                packages=({"path": ".", "type": "pip"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="pip_yanked",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/no-wheels",
                packages=({"path": ".", "type": "pip", "allow_binary": "true"},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="pip_no_wheels",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/no-sdists",
                packages=({"path": ".", "type": "pip", "allow_binary": "false"},),
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=2,
                expected_output="Error: PackageRejected: No distributions found",
            ),
            id="pip_no_sdists",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/custom-index",
                packages=({"path": ".", "type": "pip", "allow_binary": True},),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            id="pip_custom_index",
            marks=pytest.mark.skipif(
                os.getenv("HERMETO_TEST_LOCAL_PYPISERVER") != "true",
                reason="HERMETO_TEST_LOCAL_PYPISERVER!=true",
            ),
        ),
    ],
)
def test_pip_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test fetched dependencies for pip.

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
        # Test case checks fetching pip dependencies, generating environment vars file,
        # building image with all prepared prerequisites and testing if pip packages are present
        # in built image
        pytest.param(
            utils.TestParameters(
                branch="pip/e2e",
                packages=(
                    {
                        "type": "pip",
                        "requirements_files": ["requirements.txt"],
                        "requirements_build_files": ["requirements-build.txt"],
                    },
                ),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            ["python3", "/app/src/test_package_cachi2/main.py"],
            ["registry.fedoraproject.org/fedora-minimal:37"],
            id="pip_e2e_test",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/e2e-wheels",
                packages=(
                    {
                        "type": "pip",
                        "requirements_files": ["requirements.txt"],
                        "requirements_build_files": [],
                        "allow_binary": "true",
                    },
                ),
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            ["python3", "/app/package/main.py"],
            ["Hello, world!"],
            id="pip_e2e_test_wheels",
        ),
    ],
)
def test_e2e_pip(
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
    End to end test for pip.

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
