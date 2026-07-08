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
                packages=({"path": ".", "type": "cargo"},),
                check_output=False,
            ),
            id="cargo_just_a_crate_dependency",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "cargo"},),
                check_output=False,
            ),
            id="cargo_just_a_git_dependency",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "cargo"},),
                check_output=False,
            ),
            id="cargo_mixed_git_crate_dependency",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "cargo"},),
                check_output=False,
            ),
            id="cargo_uses_resolver_v3",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "cargo"},),
                check_output=False,
                expected_error=ExitError.ERR_LOCKFILE_NOT_FOUND,
                expected_output="Cargo.lock not found",
            ),
            id="cargo_missing_lockfile",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "cargo"},),
                global_flags=["--mode", "permissive"],
                check_output=False,
            ),
            id="cargo_missing_lockfile_permissive_mode",
        ),
    ],
)
def test_cargo_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Integration tests for cargo package manager."""
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
                packages=({"path": ".", "type": "cargo"},),
                check_output=True,
            ),
            [],  # No additional commands are run to verify the build
            [],
            id="cargo_mixed_git_crate_dependency",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "cargo"},),
                check_output=True,
            ),
            ["foo"],
            ["The word foo has 3 letters"],
            id="cargo_e2e",
        ),
    ],
)
def test_e2e_cargo(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """End to end test for cargo."""
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
