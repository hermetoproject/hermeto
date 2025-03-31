from pathlib import Path
from typing import List

import pytest

from . import utils


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                branch="generic/file-not-reachable",
                packages=({"path": ".", "type": "generic"},),
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=1,
                expected_output="Unsuccessful download",
            ),
            id="generic_file_not_reachable",
        )
    ],
)
def test_generic_fetcher(
    test_params: utils.TestParameters,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test fetched dependencies for the generic fetcher.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                branch="generic/e2e",
                packages=({"path": ".", "type": "generic"},),
                check_output=True,
                check_deps_checksums=True,
                check_vendor_checksums=False,
                expected_exit_code=0,
            ),
            ["ls", "/deps"],
            ["archive.zip\nv1.0.0.zip\n"],
            id="generic_e2e",
        ),
        pytest.param(
            utils.TestParameters(
                branch="generic/e2e-maven",
                packages=({"path": ".", "type": "generic"},),
                check_output=True,
                check_deps_checksums=True,
                check_vendor_checksums=False,
                expected_exit_code=0,
            ),
            [],
            ["Apache Ant(TM) version 1.10.14"],
            id="generic_e2e_maven",
        ),
    ],
)
def test_e2e_generic(
    test_params: utils.TestParameters,
    check_cmd: List[str],
    expected_cmd_output: str,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    End to end test for generic fetcher.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
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
