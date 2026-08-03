# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from hermeto.core.errors import ExitError
from tests.integration import utils

SCENARIOS_DIR = Path(__file__).parent / "scenarios"

_SUBMODULE_SCENARIOS = {
    "gomod_correct_vendor_in_submodule_passes_vendor_check",
    "gomod_wrong_vendor_in_submodule_fails_vendor_check",
}


def _create_repo(tmp_path: Path, test_case: str) -> Path:
    """Create a synthetic repo, handling submodule scenarios transparently."""
    scenario_dir = SCENARIOS_DIR / test_case
    if test_case in _SUBMODULE_SCENARIOS:
        return utils.create_synthetic_repo(
            tmp_path,
            scenario_dir / "in" / "parent",
            submodules=[
                utils.SyntheticSubmoduleSpec(
                    source_dir=scenario_dir / "in" / "submodule",
                    path="integration-tests",
                ),
            ],
        )
    return utils.create_synthetic_repo(tmp_path, scenario_dir / "in")


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_with_deps",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_without_deps",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_correct_vendor_passes_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": "integration-tests", "type": "gomod"},),
            ),
            id="gomod_correct_vendor_in_submodule_passes_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_REJECTED,
                expected_output=(
                    "PackageRejected: The content of the vendor directory is not "
                    "consistent with go.mod. Please check the logs for more details"
                ),
            ),
            id="gomod_wrong_vendor_fails_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": "integration-tests", "type": "gomod"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_REJECTED,
                expected_output=(
                    "PackageRejected: The content of the vendor directory is not "
                    "consistent with go.mod. Please check the logs for more details"
                ),
            ),
            id="gomod_wrong_vendor_in_submodule_fails_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                global_flags=["--mode=permissive"],
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_wrong_vendor_passes_vendor_check_in_permissive_mode",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_REJECTED,
                expected_output=(
                    "PackageRejected: The content of the vendor directory is not "
                    "consistent with go.mod. Please check the logs for more details"
                ),
            ),
            id="gomod_empty_vendor_fails_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                global_flags=["--mode=permissive"],
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_empty_vendor_passes_vendor_check_in_permissive_mode",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_local_deps",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_generate_imported",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": ".", "type": "gomod"},
                    {"path": "spam-module", "type": "gomod"},
                    {"path": "eggs-module", "type": "gomod"},
                ),
            ),
            id="gomod_missing_checksums",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": "./workspace_modules/hello", "type": "gomod"},),
            ),
            id="gomod_workspaces",
        ),
    ],
)
def test_gomod_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Test fetched dependencies for gomod."""
    test_case = request.node.callspec.id
    repo_dir = _create_repo(tmp_path, test_case)

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            ["retrodep", "--help"],
            ["retrodep: help requested"],
            id="gomod_e2e_1_18",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            ["retrodep", "--help"],
            ["retrodep: help requested"],
            id="gomod_e2e_1_21",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": ".", "type": "gomod"},
                    {"path": "spam-module", "type": "gomod"},
                    {"path": "eggs-module", "type": "gomod"},
                ),
            ),
            [],
            [""],
            id="gomod_e2e_multiple_modules",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": "hi/hiii", "type": "gomod"},),
            ),
            [],
            [""],
            id="gomod_e2e_1_22_workspace_vendoring",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": "vendored-module", "type": "gomod"},
                    {"path": "non-vendored-module", "type": "gomod"},
                ),
            ),
            [],
            [""],
            id="gomod_e2e_vendor_nonvendor_module_mix_ordering_1",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": "non-vendored-module", "type": "gomod"},
                    {"path": "vendored-module", "type": "gomod"},
                ),
            ),
            [],
            [""],
            id="gomod_e2e_vendor_nonvendor_module_mix_ordering_2",
        ),
    ],
)
def test_e2e_gomod(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """End to end test for gomod."""
    test_case = request.node.callspec.id
    repo_dir = _create_repo(tmp_path, test_case)

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
