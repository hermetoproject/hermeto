# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from hermeto.core.errors import ExitError
from tests.integration import utils

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "bundler"},),
                check_output=False,
                expected_error=ExitError.ERR_LOCKFILE_NOT_FOUND,
                expected_output="Required files not found:",
            ),
            id="bundler_missing_gemfile",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "bundler"},),
                check_output=False,
                expected_error=ExitError.ERR_LOCKFILE_NOT_FOUND,
                expected_output="Required files not found:",
            ),
            id="bundler_missing_lockfile",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "bundler"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_MANAGER,
                expected_output="Failed to parse",
            ),
            id="bundler_missing_git_revision",
        ),
    ],
)
def test_bundler_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Integration tests for bundler package manager."""
    test_case = request.node.callspec.id
    source_dir = SCENARIOS_DIR / test_case / "in"
    repo_dir = utils.create_synthetic_repo(tmp_path, source_dir)

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "bundler", "binary": {}},),
                check_output=True,
                containerfile="Containerfile.ruby33",
            ),
            [],  # No additional commands are run to verify the build
            [],
            id="bundler_e2e_ruby33",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "bundler", "binary": {}},),
                check_output=True,
                containerfile="Containerfile.ruby40",
            ),
            [],
            [],
            id="bundler_e2e_ruby40",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "bundler", "binary": {}},),
                check_output=True,
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
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    End to end test for bundler.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id
    source_dir = SCENARIOS_DIR / test_case / "in"
    repo_dir = utils.create_synthetic_repo(tmp_path, source_dir)

    actual_repo_dir = utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )

    utils.build_image_and_check_cmd(
        tmp_path,
        actual_repo_dir,
        SCENARIOS_DIR,
        test_case,
        check_cmd,
        expected_cmd_output,
        hermeto_image,
        test_params=test_params,
    )
