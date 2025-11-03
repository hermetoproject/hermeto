import os
from subprocess import CalledProcessError
from unittest import mock

import pytest

from hermeto.core.package_managers.yarn.utils import PackageManagerError, run_yarn_cmd
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
@mock.patch("hermeto.core.package_managers.yarn.utils.run_cmd")
def test_run_yarn_cmd(
    mock_run_cmd: mock.Mock,
    env: dict[str, str] | None,
    expect_path: str,
    rooted_tmp_path: RootedPath,
) -> None:
    run_yarn_cmd(["info", "--json"], rooted_tmp_path, env)

    expect_env = (env or {}) | {"PATH": expect_path}
    mock_run_cmd.assert_called_once_with(
        cmd=["yarn", "info", "--json"], params={"cwd": rooted_tmp_path, "env": expect_env}
    )


@mock.patch("hermeto.core.package_managers.yarn.utils.run_cmd")
def test_run_yarn_cmd_fail(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    cmd = ["foo", "bar"]
    mock_run_cmd.side_effect = CalledProcessError(1, cmd=cmd)

    with pytest.raises(PackageManagerError, match=f"Yarn command failed: {' '.join(cmd)}"):
        run_yarn_cmd(cmd, rooted_tmp_path)
