# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from tests.integration import utils

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"type": "generic"}, {"type": "pnpm"}),
            ),
            [],
            [],
            id="pnpm_e2e_v10",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"type": "generic"}, {"type": "pnpm"}),
            ),
            [],
            [],
            id="pnpm_e2e_v11",
        ),
    ],
)
def test_e2e_pnpm(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """End to end tests for pnpm."""
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
