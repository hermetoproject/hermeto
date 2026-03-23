# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

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
                flags=[],
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            [],
            [],
            id="multiple_gomod_and_npm",
        ),
        pytest.param(
            utils.TestParameters(
                branch="pip/e2e_rust_extensions-siva",
                # Use the fork/branch fixture repository (branch: pip/e2e_rust_extensions-siva)
                # https://github.com/Siva-Sainath/integration-tests/tree/pip/e2e_rust_extensions-siva
                repo_url="https://github.com/Siva-Sainath/integration-tests.git",
                packages=(
                    {"type": "cargo", "path": "rust-crate"},
                    {"type": "pip", "path": "python-pkg"},
                    # using RPM to provide cargo and python in the image
                    {"type": "rpm"},
                ),
                flags=[],
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
                check_deps_checksums=False,
                container_network="host",
                mount_rpm_repos=True,
            ),
            [],
            [],
            id="multiple_cargo_and_pip",
        ),
    ],
)
def test_e2e_multiple(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
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
        container_network=test_params.container_network,
        mount_rpm_repos=test_params.mount_rpm_repos,
    )
