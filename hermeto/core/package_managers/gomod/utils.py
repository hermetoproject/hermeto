# SPDX-License-Identifier: GPL-3.0-only
import os
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any, TYPE_CHECKING

from hermeto import APP_NAME
from hermeto.core.package_managers.gomod.go import Go
from hermeto.core.type_aliases import StrPath

if TYPE_CHECKING:
    from typing_extensions import Self


class GoCacheTemporaryDirectory(tempfile.TemporaryDirectory):
    """
    A wrapper around the TemporaryDirectory context manager to also run `go clean -modcache`.

    The files in the Go cache are read-only by default and cause the default clean up behavior of
    tempfile.TemporaryDirectory to fail with a permission error. A way around this is to run
    `go clean -modcache` before the default clean up behavior is run.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize our TemporaryDirectory context manager wrapper.

        Store the Go toolchain version used in this session for the subsequent cleanup.
        """
        super().__init__(*args, **kwargs)
        # store the exact toolchain instance that was used for all actions within the context
        self._go_instance: Go | None = None

    def __enter__(self) -> "Self":
        super().__enter__()
        return self

    def __exit__(
        self,
        exc: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Clean up the temporary directory by first cleaning up the Go cache."""
        try:
            if go := self._go_instance:
                _clean_go_modcache(go, self.name)
        finally:
            super().__exit__(exc, value, tb)


def _clean_go_modcache(go: Go, dir_: StrPath | None) -> None:
    # It's easier to mock a helper when testing a huge function than individual object instances
    if dir_ is not None:
        go(["clean", "-modcache"], {"env": {"GOPATH": dir_, "GOCACHE": dir_}})


def _list_toolchain_files(dir_path: str, files: list[str]) -> list[str]:
    def is_a_toolchain_path(path: str | os.PathLike[str]) -> bool:
        # Go automatically downloads toolchains to paths like:
        #   - pkg/mod/cache/download/golang.org/toolchain/@v/v0.0.1-go1.21.5.*
        #   - pkg/mod/cache/download/sumdb/sum.golang.org/lookup/golang.org/toolchain@v0.0.1-go1.21.5.*
        return "golang.org/toolchain" in str(path) and "pkg/mod/cache" in str(path)

    return [file for file in files if is_a_toolchain_path(Path(dir_path) / file)]


def _go_exec_env(**extra_vars: str) -> dict[str, str]:
    """Build the base environment for go command execution."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", Path.home().as_posix()),  # HOME= can be unset, hence Path
        "NETRC": os.environ.get("NETRC", ""),
    }
    return env | extra_vars
