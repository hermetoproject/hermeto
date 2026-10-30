# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path
from subprocess import CalledProcessError
from unittest import mock

import pytest

from hermeto.core.package_managers.javascript.yarn.utils import (
    PackageManagerError,
    extract_yarn_version_from_env,
    run_yarn_cmd,
)
from hermeto.core.rooted_path import RootedPath


@pytest.mark.parametrize(
    "env, expect_path",
    [
        (None, os.environ["PATH"]),
        ({}, os.environ["PATH"]),
        ({"yarn_global_folder": "/tmp/yarnberry"}, os.environ["PATH"]),
        ({"PATH": "/bin"}, "/bin"),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_cmd")
def test_run_yarn_cmd(
    mock_run_cmd: mock.Mock,
    env: dict[str, str] | None,
    expect_path: str,
    rooted_tmp_path: RootedPath,
) -> None:
    yarn_bin = Path("/cache/yarn/4.6.0/yarn.js")
    run_yarn_cmd(["info", "--json"], rooted_tmp_path, env, yarn_bin=yarn_bin)

    expect_env = (env or {}) | {"PATH": expect_path, "NODE_USE_SYSTEM_CA": "1"}
    mock_run_cmd.assert_called_once_with(
        cmd=["node", str(yarn_bin), "info", "--json"],
        params={"cwd": rooted_tmp_path, "env": expect_env},
    )


@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_cmd")
def test_run_yarn_cmd_preserves_error_output(
    mock_run_cmd: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    cmd = ["install", "--mode", "skip-build"]
    yarn_bin = Path("/cache/yarn/4.6.0/yarn.js")
    output = "yarn stdout"
    mock_run_cmd.side_effect = CalledProcessError(
        1, ["node", str(yarn_bin), *cmd], output=output, stderr="unrelated stderr"
    )

    with pytest.raises(PackageManagerError) as exc_info:
        run_yarn_cmd(cmd, rooted_tmp_path, yarn_bin=yarn_bin)

    assert exc_info.value.stderr == output


@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_cmd")
def test_run_yarn_cmd_fail(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    cmd = ["foo", "bar"]
    mock_run_cmd.side_effect = CalledProcessError(1, cmd=cmd)

    with pytest.raises(PackageManagerError):
        run_yarn_cmd(cmd, rooted_tmp_path, yarn_bin=Path("/cache/yarn/4.6.0/yarn.js"))


@pytest.mark.parametrize("entry_point", ["1.22.22/bin/yarn.js", "4.6.0/yarn.js"])
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_cmd")
def test_version_check_preserves_startup_failure(
    mock_run_cmd: mock.Mock, rooted_tmp_path: RootedPath, entry_point: str
) -> None:
    yarn_bin = rooted_tmp_path.path / "cache" / entry_point
    cmd = ["node", str(yarn_bin), "--version"]
    error = CalledProcessError(1, cmd=cmd, output="", stderr="Node startup error")
    mock_run_cmd.side_effect = error

    with pytest.raises(PackageManagerError) as exc_info:
        extract_yarn_version_from_env(rooted_tmp_path, yarn_bin=yarn_bin)

    cause = exc_info.value.__cause__
    assert isinstance(cause, PackageManagerError)
    assert cause.__cause__ is error
    assert exc_info.value.stderr == error.stderr
    assert exc_info.value.solution is not None
