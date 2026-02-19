# SPDX-License-Identifier: GPL-3.0-only
import logging
from pathlib import Path

import pytest

from . import utils

log = logging.getLogger(__name__)


def test_help(hermeto_image: utils.ContainerImage, tmp_path: Path) -> None:
    """
    Simple test to verify if there's only the expected naming in our help.

    TODO: Drop this when we no longer support the legacy entrypoint.
    """
    for cmd in ("fetch-deps", "generate-env", "inject-files", "merge-sboms"):
        output, exit_code = hermeto_image.run_cmd_on_image(
            [cmd, "--help"], tmp_path, entrypoint="cachi2"
        )

        assert exit_code == 0, f"Querying help failed, output-cmd: {output}"
        for line in output.split(sep="\n"):
            line = line.strip()
            if cmd in line:
                assert "cachi2" in line and not ("hermeto" in line or "APP_NAME" in line)
        if cmd == "fetch-deps":
            assert "[default: ./cachi2-output]" in output


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                branch="cargo/mixed-git-crate-dependency",
                packages=({"path": ".", "type": "cargo"},),
                check_output=True,
                check_deps_checksums=False,
                expected_exit_code=0,
                expected_output="",
            ),
            [],  # No additional commands are run to verify the build
            [],
            id="legacy_entrypoint_e2e_test",
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
    """
    End to end test for cargo using the legacy entrypoint.

    TODO: Drop this when we no longer support the legacy entrypoint.
    """
    test_case = request.node.callspec.id

    actual_repo_dir = utils.fetch_deps_and_check_output(
        tmp_path,
        test_case,
        test_params,
        test_repo_dir,
        test_data_dir,
        hermeto_image,
        fetch_output_dirname="cachi2-output",
        entrypoint="cachi2",
    )

    utils.build_image_and_check_cmd(
        tmp_path,
        actual_repo_dir,
        test_data_dir,
        test_case,
        check_cmd,
        expected_cmd_output,
        hermeto_image,
        fetch_output_dirname="cachi2-output",
        env_vars_filename="cachi2.env",
        hermeto_image_entrypoint="cachi2",
    )
