# SPDX-License-Identifier: GPL-3.0-only
import zipfile
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
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_REJECTED,
                expected_output="PackageRejected: Yarn zero install detected, PnP zero installs are unsupported by hermeto",
            ),
            id="yarn_zero_installs",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                expected_error=ExitError.ERR_UNSUPPORTED_FEATURE,
                expected_output="UnsupportedFeature: Found 8 unsupported dependencies, more details in the logs.",
            ),
            id="yarn_disallowed_protocols",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                expected_output="Processing the request using yarn@3.6.1",
            ),
            id="yarn_correct_version_is_installed_by_corepack",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                expected_output="Processing the request using yarn@4.5.2",
            ),
            id="yarn_v4",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_MANAGER,
                expected_output="The lockfile would have been modified by this install, which is explicitly forbidden.",
            ),
            id="yarn_immutable_installs",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_MANAGER,
                expected_output="typescript@npm:5.3.3: The remote archive doesn't match the expected checksum",
            ),
            id="yarn_incorrect_checksum",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                expected_error=ExitError.ERR_LOCKFILE_NOT_FOUND,
                expected_output="Required files not found:",
            ),
            id="yarn_missing_lockfile",
        ),
    ],
)
def test_yarn_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test fetched dependencies for yarn berry.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id
    source_dir = SCENARIOS_DIR / test_case / "in"
    repo_dir = utils.create_synthetic_repo(tmp_path, source_dir)

    # create a dummy ZIP inside .yarn cache for the following simple test case
    if test_case == "yarn_zero_installs":
        cache_dir = repo_dir / ".yarn" / "cache"
        cache_dir.mkdir(parents=True)
        with zipfile.ZipFile(cache_dir / "dummy-0.0.0.zip", "w") as zf:
            zf.writestr("package.json", '{"name": "dummy", "version": "0.0.0"}')

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params, check_cmd, expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
            ),
            ["yarn", "berryscary"],
            "Hello, World!",
            id="yarn_e2e",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": "first-pkg", "type": "yarn"},
                    {"path": "second-pkg", "type": "yarn"},
                ),
            ),
            ["yarn", "node", "index.js"],
            "Hello from first package!",
            id="yarn_e2e_multiple_packages",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn", "workspaces": ["app"]},),
            ),
            ["node", "packages/app/index.js"],
            "workspace focus works!",
            id="yarn_e2e_workspace_focus",
        ),
    ],
)
def test_e2e_yarn(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """End to end test for yarn berry."""
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
        test_params,
        check_cmd,
        expected_cmd_output,
        hermeto_image,
    )
