# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path

from hermeto.core.package_managers.gomod.go import Go
from hermeto.core.type_aliases import StrPath


def _clean_go_modcache(go: Go, dir_: StrPath | None) -> None:
    # It's easier to mock a helper when testing a huge function than individual object instances
    if dir_ is not None:
        go(["clean", "-modcache"], {"env": {"GOPATH": dir_, "GOCACHE": dir_}})


def _go_exec_env(**extra_vars: str) -> dict[str, str]:
    """Build the base environment for go command execution."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", Path.home().as_posix()),  # HOME= can be unset, hence Path
        "NETRC": os.environ.get("NETRC", ""),
    }
    return env | extra_vars
