import logging
from pathlib import Path

import pytest

from . import utils

log = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                branch="cargo/just-a-crate-dependency",
                packages=({"path": ".", "type": "cargo"},),
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="",
            ),
            id="cargo_just_a_crate_dependency",
        ),
        pytest.param(
            utils.TestParameters(
                branch="cargo/just-a-git-dependency",
                packages=({"path": ".", "type": "cargo"},),
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="",
            ),
            id="cargo_just_a_git_dependency",
        ),
        pytest.param(
            utils.TestParameters(
                branch="cargo/mixed-git-crate-dependency",
                packages=({"path": ".", "type": "cargo"},),
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="",
            ),
            id="cargo_mixed_git_crate_dependency",
        ),
    ],
)
def test_cargo_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Integration tests for bundler package manager."""
    test_case = request.node.callspec.id

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                branch="cargo/mixed-git-crate-dependency",
                packages=({"path": ".", "type": "cargo"},),
                check_output=True,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="",
            ),
            [],  # No additional commands are run to verify the build
            [],
            id="cargo_mixed_git_crate_dependency",
        ),
    ],
)
def test_e2e_cargo(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """End to end test for cargo."""
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
