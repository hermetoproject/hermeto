# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
from unittest import mock

import pytest

from hermeto.core.package_managers.gomod.utils import (
    _go_exec_env,
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
