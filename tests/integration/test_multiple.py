from pathlib import Path
from typing import List

import pytest

from . import utils


@pytest.mark.parametrize(
    "test_params, check_cmd, expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                branch="multiple/gomod-and-npm",
                packages=(
                    ({"type": "gomod", "path": "gomod-package"}),
                    {"type": "npm", "path": "npm-package"},
                    # using RPM to provide gomod and npm in the image
                    {"type": "rpm"},
                ),
                check_vendor_checksums=False,
                flags=["--dev-package-managers"],
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            [],
            [],
            id="multiple_gomod_and_npm",
        ),
    ],
)
def test_e2e_multiple(
    test_params: utils.TestParameters,
    check_cmd: List[str],
    expected_cmd_output: str,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    test_case = request.node.callspec.id

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )

    utils.build_image_and_check_cmd(
        tmp_path,
        test_repo_dir,
        test_data_dir,
        test_case,
        check_cmd,
        expected_cmd_output,
        hermeto_image,
    )
