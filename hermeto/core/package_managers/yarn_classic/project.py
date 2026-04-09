# SPDX-License-Identifier: GPL-3.0-only
"""
Parse the relevant files of a yarn project.

It also provides basic utility functions. The main logic to resolve and prefetch
the dependencies should be implemented in other modules.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pyarn import lockfile  # type: ignore

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound, PackageRejected
from hermeto.core.package_managers.common import PackageJson
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(name=__name__)


@dataclass
class _CommonConfigFile(ABC):
    """A base class for representing a config file.

    :param path: the path to the config file, relative to the request source dir
    :param data: the raw data for the config file content
    """

    _path: RootedPath
    _data: dict[str, Any]

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @property
    def path(self) -> RootedPath:
        return self._path

    @classmethod
    @abstractmethod
    def from_file(cls, path: RootedPath) -> "_CommonConfigFile":
        """Construct a ConfigFile instance."""


@dataclass
class YarnLock(_CommonConfigFile):
    """A yarn.lock file.

    This class abstracts the underlying attributes.
    """

    yarn_lockfile: lockfile.Lockfile

    @classmethod
    def from_file(cls, path: RootedPath) -> "YarnLock":
        """Parse the content of a yarn.lock file."""
        try:
            yarn_lockfile = lockfile.Lockfile.from_file(path)
        except FileNotFoundError:
            raise LockfileNotFound(
                files=path.path,
                solution=(
                    "Please double-check that you have specified the correct path "
                    "to the package directory containing this file"
                ),
            )
        except ValueError as e:
            raise InvalidLockfileFormat(
                lockfile_path=path.path,
                err_details=str(e),
                solution="The yarn.lock file must be valid.",
            )

        if not yarn_lockfile:
            raise PackageRejected(
                reason="The yarn.lock file must not be empty",
                solution="Please verify the content of the file.",
            )

        return cls(path, yarn_lockfile.data, yarn_lockfile)


@dataclass(frozen=True)
class Project:
    """Minimally, a directory containing yarn sources and parsed package.json."""

    source_dir: RootedPath
    package_json: PackageJson

    @property
    def is_pnp_install(self) -> bool:
        """Is the project is using Plug'n'Play (PnP) workflow or not.

        This is determined by
        - `installConfig.pnp: true` in 'package.json'
        - the existence of file(s) with glob name '*.pnp.cjs'
        - the presence of an expanded node_modules directory
        For more details on PnP, see: https://classic.yarnpkg.com/en/docs/pnp
        """
        install_config = self.package_json.data.get("installConfig", {})
        install_config_pnp_enabled = install_config.get("pnp", False)

        pnp_cjs_exists = any(self.source_dir.path.glob("*.pnp.cjs"))
        node_modules_exists = self.source_dir.join_within_root("node_modules").path.exists()
        return install_config_pnp_enabled or pnp_cjs_exists or node_modules_exists

    @classmethod
    def from_source_dir(cls, source_dir: RootedPath) -> "Project":
        """Create a Project from a sources directory path."""
        package_json = PackageJson.from_dir(source_dir.path)
        return cls(source_dir, package_json)
