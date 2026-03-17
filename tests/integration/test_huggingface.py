# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from . import utils


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                branch="huggingface/e2e",
                packages=({"path": ".", "type": "x-huggingface"},),
                check_output=True,
                check_deps_checksums=False,
                expected_exit_code=0,
            ),
            [],
            ["OK"],
            id="huggingface_e2e",
        ),
    ],
)
def test_e2e_huggingface(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    End to end test for Hugging Face fetcher.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id

    actual_repo_dir = utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )

    utils.build_image_and_check_cmd(
        tmp_path,
        actual_repo_dir,
        test_data_dir,
        test_case,
        check_cmd,
        expected_cmd_output,
        hermeto_image,
    )
