"""
Parse the relevant files of a yarn project.

It also provides basic utility functions. The main logic to resolve and prefetch
the dependencies should be implemented in other modules.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Union

from pyarn import lockfile  # type: ignore

from cachi2.core.errors import PackageRejected
from cachi2.core.rooted_path import RootedPath

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


class PackageJson(_CommonConfigFile):
    """A package.json file.

    This class abstracts the underlying attributes and only exposes what
    is relevant for the request processing.
    """

    @property
    def install_config(self) -> dict[str, Any]:
        """Get the installConfig dict."""
        return self.data.get("installConfig", {})

    @classmethod
    def from_file(cls, path: RootedPath) -> "PackageJson":
        """Construct a PackageJson instance."""
        try:
            package_json_data = json.loads(path.path.read_text())
        except FileNotFoundError:
            raise PackageRejected(
                reason="The package.json file must be present for the yarn package manager",
                solution=(
                    "Please double-check that you have specified the correct path "
                    "to the package directory containing this file"
                ),
            )
        except json.decoder.JSONDecodeError as e:
            raise PackageRejected(
                reason=f"Can't parse the {path.subpath_from_root} file. {e}",
                solution=(
                    "The package.json file must contain valid JSON. "
                    "Refer to the parser error and fix the contents of the file."
                ),
            )

        return cls(path, package_json_data)


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
            raise PackageRejected(
                reason="The yarn.lock file must be present for the yarn package manager",
                solution=(
                    "Please double-check that you have specified the correct path "
                    "to the package directory containing this file"
                ),
            )
        except ValueError:
            raise PackageRejected(
                reason=f"Can't parse the {path} file.\n",
                solution="The yarn.lock file must be valid.",
            )

        if not yarn_lockfile:
            raise PackageRejected(
                reason="The yarn.lock file must not be empty",
                solution="Please verify the content of the file.",
            )

        return cls(path, yarn_lockfile.data, yarn_lockfile)


ConfigFile = Union[PackageJson, YarnLock]


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
        install_config_pnp_enabled = self.package_json.install_config.get("pnp", False)
        pnp_cjs_exists = any(self.source_dir.path.glob("*.pnp.cjs"))
        node_modules_exists = self.source_dir.join_within_root("node_modules").path.exists()
        return install_config_pnp_enabled or pnp_cjs_exists or node_modules_exists

    @classmethod
    def from_source_dir(cls, source_dir: RootedPath) -> "Project":
        """Create a Project from a sources directory path."""
        package_json = PackageJson.from_file(source_dir.join_within_root("package.json"))
        return cls(source_dir, package_json)
