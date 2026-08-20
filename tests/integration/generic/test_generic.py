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
                packages=({"path": ".", "type": "generic"},),
                check_output=False,
                expected_error=ExitError.ERR_FETCH,
                expected_output="Could not download",
            ),
            id="generic_file_not_reachable",
        )
    ],
)
def test_generic_fetcher(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test fetched dependencies for the generic fetcher.

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
                packages=({"path": ".", "type": "generic"},),
                check_output=True,
            ),
            ["ls", "/deps"],
            ["archive.zip\nv1.0.0.zip\n"],
            id="generic_e2e",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "generic"},),
                check_output=True,
            ),
            [],
            ["Apache Ant(TM) version 1.10.14"],
            id="generic_e2e_maven",
        ),
    ],
)
def test_e2e_generic(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    End to end test for generic fetcher.

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
        test_params,
        check_cmd,
        expected_cmd_output,
        hermeto_image,
    )


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "generic"},),
                check_output=True,
            ),
            id="generic_e2e_basic_auth",
            marks=pytest.mark.skipif(
                os.getenv("HERMETO_TEST_LOCAL_NEXUS") != "1",
                reason="HERMETO_TEST_LOCAL_NEXUS!=1",
            ),
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "generic"},),
                check_output=True,
            ),
            id="generic_e2e_bearer_auth",
            marks=pytest.mark.skipif(
                os.getenv("HERMETO_TEST_LOCAL_NEXUS") != "1",
                reason="HERMETO_TEST_LOCAL_NEXUS!=1",
            ),
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "generic"},),
                check_output=False,
                expected_error=ExitError.ERR_FETCH,
                expected_output="Could not download",
            ),
            id="generic_e2e_auth_wrong_creds",
            marks=pytest.mark.skipif(
                os.getenv("HERMETO_TEST_LOCAL_NEXUS") != "1",
                reason="HERMETO_TEST_LOCAL_NEXUS!=1",
            ),
        ),
    ],
)
def test_generic_auth(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test generic fetcher with authentication from the lockfile.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id
    source_dir = SCENARIOS_DIR / test_case / "in"
    repo_dir = utils.create_synthetic_repo(tmp_path, source_dir)

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )
