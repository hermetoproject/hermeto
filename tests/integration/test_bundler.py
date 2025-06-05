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
                branch="bundler/missing-gemfile",
                packages=({"path": ".", "type": "bundler"},),
                check_output=False,
                check_deps_checksums=False,
                expected_exit_code=2,
                expected_output="Gemfile and Gemfile.lock must be present in the package directory",
            ),
            id="bundler_missing_gemfile",
        ),
        pytest.param(
            utils.TestParameters(
                branch="bundler/missing-lockfile",
                packages=({"path": ".", "type": "bundler"},),
                check_output=False,
                check_deps_checksums=False,
                expected_exit_code=2,
                expected_output="Gemfile and Gemfile.lock must be present in the package directory",
            ),
            id="bundler_missing_lockfile",
        ),
        pytest.param(
            utils.TestParameters(
                branch="bundler/missing-git-revision",
                packages=({"path": ".", "type": "bundler"},),
                check_output=False,
                check_deps_checksums=False,
                expected_exit_code=1,
                expected_output="Failed to parse",
            ),
            id="bundler_missing_git_revision",
        ),
    ],
)
def test_bundler_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Integration tests for bundler package manager."""
    test_case = request.node.callspec.id

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                branch="bundler/e2e",
                packages=({"path": ".", "type": "bundler", "allow_binary": "true"},),
                check_output=True,
                check_deps_checksums=False,
                expected_exit_code=0,
                expected_output="",
            ),
            [],  # No additional commands are run to verify the build
            [],
            id="bundler_e2e",
        ),
        pytest.param(
            utils.TestParameters(
                branch="bundler/e2e-missing-gemspec",
                packages=({"path": ".", "type": "bundler", "allow_binary": "true"},),
                check_output=True,
                check_deps_checksums=False,
                expected_exit_code=0,
                expected_output="",
            ),
            [],  # No additional commands are run to verify the build
            [],
            id="bundler_e2e_missing_gemspec",
        ),
    ],
)
def test_e2e_bundler(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    End to end test for bundler.

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
