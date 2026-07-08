# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from tests.integration import utils

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "npm"},),
            ),
            id="npm_bundled_lockfile3",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "npm"},),
            ),
            id="npm_yarn_registry_lockfile3",
        ),
    ],
)
def test_npm_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Smoketest for npm offline install development.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
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
                packages=({"path": ".", "type": "npm"},),
            ),
            [],
            [],
            id="npm_smoketest_lockfile2",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "npm"},),
            ),
            [],
            [],
            id="npm_smoketest_lockfile3",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "npm"},),
            ),
            [],
            [],
            id="npm_multiple_dep_versions",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "npm"},),
            ),
            [],
            [],
            id="npm_aliased_deps",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "npm"},),
            ),
            [],
            [],
            id="npm_dev_optional_peer_deps",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": "first_pkg", "type": "npm"},
                    {"path": "second_pkg", "type": "npm"},
                    {"path": "third_pkg", "type": "npm"},
                ),
            ),
            [],
            [],
            id="npm_multiple_packages",
        ),
    ],
)
def test_e2e_npm(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    End to end test for npm.

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
