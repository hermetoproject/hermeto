# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
from unittest import mock

import pytest

from hermeto.core.package_managers.gomod.utils import (
    _go_exec_env,
    _list_toolchain_files,
)

_ENV_VARS_BASE_INIT = {v: "/some/path" for v in ("PATH", "HOME", "NETRC")}


@pytest.mark.parametrize(
    "env, extra_env, expected",
    [
        pytest.param(_ENV_VARS_BASE_INIT, None, _ENV_VARS_BASE_INIT, id="vars_inherited"),
        pytest.param(
            {},
            None,
            {"PATH": "", "HOME": "/mocked/home", "NETRC": ""},
            id="vars_defaults",
        ),
        pytest.param(
            _ENV_VARS_BASE_INIT,
            {"GOPATH": "/tmp/go"},
            _ENV_VARS_BASE_INIT | {"GOPATH": "/tmp/go"},
            id="with_extra_env",
        ),
    ],
)
@mock.patch("pathlib.Path.home", return_value=Path("/mocked/home"))
def test_go_exec_env(
    mock_home: mock.Mock,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    extra_env: dict[str, str] | None,
    expected: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", env)

    actual = _go_exec_env() if extra_env is None else _go_exec_env(**extra_env)
    assert actual == expected


@pytest.mark.parametrize(
    "dir_path, files, expected",
    [
        pytest.param(
            "pkg/mod/cache/download/golang.org/toolchain/@v",
            ["v0.0.1-go1.21.5.linux-amd64.zip", "v0.0.1-go1.22.0.linux-amd64.zip"],
            ["v0.0.1-go1.21.5.linux-amd64.zip", "v0.0.1-go1.22.0.linux-amd64.zip"],
            id="toolchain_files",
        ),
        pytest.param(
            "pkg/mod/cache/download/sumdb/sum.golang.org/lookup",
            [
                "golang.org/toolchain@v0.0.1-go1.21.5.linux-amd64",
                "github.com/example/module@v1.0.0",
            ],
            ["golang.org/toolchain@v0.0.1-go1.21.5.linux-amd64"],
            id="mixed_sumdb_files",
        ),
        pytest.param(
            "pkg/mod/cache/download/golang.org/x/crypto/@v",
            ["v0.1.0.mod", "v0.1.0.zip"],
            [],
            id="golang_org_module_files",
        ),
        pytest.param(
            "pkg/mod/cache/download/github.com/example/module/@v",
            ["v1.0.0.zip", "v1.1.0.zip"],
            [],
            id="other_module_files",
        ),
        pytest.param(
            "foo/bar/golang.org/toolchain/@v",
            ["version.zip"],
            [],
            id="toolchain_under_strange_path",
        ),
        pytest.param(
            "pkg/mod/cache/download",
            [],
            [],
            id="empty_files_list",
        ),
    ],
)
def test_ignore_toolchain_files(dir_path: str, files: list[str], expected: list[str]) -> None:
    result = _list_toolchain_files(dir_path, files)
    assert result == expected
