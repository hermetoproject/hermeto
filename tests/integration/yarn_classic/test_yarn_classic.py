# SPDX-License-Identifier: GPL-3.0-only
import os
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
                expected_output="Processing the request using yarn@1.22.",
            ),
            id="yarn_classic_corepack_ignored",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                expected_output="Processing the request using yarn@1.22.",
            ),
            id="yarn_classic_yarn_path_ignored",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_MANAGER,
                expected_output='Integrity check failed for "@colors/colors"',
            ),
            id="yarn_classic_invalid_checksum",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_MANAGER,
                expected_output="Your lockfile needs to be updated, but yarn was run with `--frozen-lockfile`.",
            ),
            id="yarn_classic_updating_frozen_lockfile_fails",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "yarn"},),
                check_output=False,
            ),
            id="yarn_classic_lifecycle_scripts",
        ),
    ],
)
def test_yarn_classic_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test fetched dependencies for yarn classic.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id
    source_dir = SCENARIOS_DIR / test_case / "in"
    repo_dir = utils.create_synthetic_repo(tmp_path, source_dir)

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )


@pytest.mark.skipif(
    os.getenv("HERMETO_TEST_LOCAL_NEXUS") != "1",
    reason="Requires HERMETO_TEST_LOCAL_NEXUS == 1",
)
def test_yarn_classic_offline_mirror_collision(
    nexus_collision_tarballs: None,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
) -> None:
    """Test that hermeto detects tarball collisions in the offline mirror.

    Two URL dependencies resolve to tarballs with the same filename but
    different content. The tarballs are served from a local Nexus instance.
    """
    test_case = "yarn_classic_offline_mirror_collision"
    test_params = utils.TestParameters(
        packages=({"path": ".", "type": "yarn"},),
        check_output=False,
        expected_error=ExitError.ERR_PACKAGE_MANAGER,
        expected_output="Tarball collision in the offline mirror",
    )
    source_dir = SCENARIOS_DIR / test_case / "in"
    repo_dir = utils.create_synthetic_repo(tmp_path, source_dir)

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
            ["yarn", "node", "index.js"],
            "Hello world!",
            id="yarn_classic_e2e",
        ),
    ],
)
def test_e2e_yarn_classic(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """End to end test for yarn classic."""
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
