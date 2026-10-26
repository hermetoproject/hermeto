# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path

import pytest

from tests.integration import utils

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@pytest.mark.skipif(
    os.getenv("HERMETO_TEST_LOCAL_NEXUS") != "1",
    reason="the nginx endpoint rejecting generic User-Agents runs only with the local "
    "Nexus (HERMETO_TEST_LOCAL_NEXUS=1)",
)
@pytest.mark.parametrize(
    "test_params",
    [
        # The generic backend is only the vehicle for an HTTP download here, it is not under test
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "generic"},),
                check_output=False,
            ),
            id="general_user_agent",
        ),
    ],
)
def test_user_agent(
    nexus_user_agent_artifact: None,
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test that hermeto identifies itself in the User-Agent.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id
    repo_dir = utils.create_synthetic_repo(tmp_path, SCENARIOS_DIR / test_case)

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )
