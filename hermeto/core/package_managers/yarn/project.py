"""
Parse the relevant files of a yarn project.

It also provides basic utility functions. The main logic to resolve and prefetch the dependencies
should be implemented in other modules.
"""

import json
import logging
import re
from collections import UserDict
from pathlib import Path
from typing import Any, Literal, NamedTuple, TypedDict

import semver
import yaml

from hermeto.core.errors import (
    InvalidLockfileFormat,
    LockfileNotFound,
    PackageRejected,
    UnexpectedFormat,
)
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


ChecksumBehavior = Literal["throw", "update", "ignore"]
PnpMode = Literal["strict", "loose"]
NodeLinker = Literal["pnp", "pnpm", "node-modules"]


class Plugin(TypedDict):
    """A plugin defined in the yarnrc file."""

    path: str
    spec: str


class YarnRc(UserDict):
    """A yarnrc file.

    This class maps the contents of a yarnrc file to a specialized dictionary while setting
    defaults for a few attributes in order to allow the request's processing.
    """

    def __init__(self, path: RootedPath, data: dict[str, Any]) -> None:
        """Initialize a YarnRc object.

        :param path: the path to the yarnrc file, relative to the request source dir.
        :param data: the raw data for the yarnrc file.
        """
        self._path = path
        super().__init__(data)

    def write(self) -> None:
        """Write the data to the yarnrc file."""
        with self._path.path.open("w") as f:
            yaml.safe_dump(self.data, f)

    @classmethod
    def from_file(cls, file_path: RootedPath) -> "YarnRc":
        """Parse the content of a yarnrc file."""
        try:
            with file_path.path.open("r") as f:
                yarnrc_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise PackageRejected(
                f"Can't parse the {file_path.subpath_from_root} file. Parser error: {e}",
                solution=(
                    "The yarnrc file must contain valid YAML. "
                    "Refer to the parser error and fix the contents of the file."
                ),
            )

        if yarnrc_data is None:
            yarnrc_data = {}

        return cls(file_path, yarnrc_data)


class PackageJson(UserDict):
    """A package.json file.

    This class maps the contents of a package.json file to a specialized dictionary.
    """

    def __init__(self, path: RootedPath, data: dict[str, Any]) -> None:
        """Initialize a PackageJson object.

        :param path: the path to the package.json file, relative to the request source dir.
        :param data: the raw data for the package.json file.
        """
        self._path = path
        super().__init__(data)

    @classmethod
    def from_file(cls, file_path: RootedPath) -> "PackageJson":
        """Parse the content of a package.json file."""
        try:
            with file_path.path.open("r") as f:
                package_json_data = json.load(f)
        except FileNotFoundError:
            raise LockfileNotFound(
                files=file_path.path,
                solution=(
                    "Please double-check that you have specified the correct path "
                    "to the package directory containing this file"
                ),
            )
        except json.decoder.JSONDecodeError as e:
            raise InvalidLockfileFormat(
                lockfile_path=file_path.path,
                err_details=str(e),
                solution=(
                    "The package.json file must contain valid JSON. "
                    "Refer to the parser error and fix the contents of the file."
                ),
            )

        return cls(file_path, package_json_data)

    def write(self) -> None:
        """Write the data to the package.json file."""
        with self._path.path.open("w") as f:
            json.dump(self.data, f, indent=2)
            f.write("\n")


class Project(NamedTuple):
    """A directory containing yarn sources."""

    source_dir: RootedPath
    yarn_rc: YarnRc
    package_json: PackageJson

    @property
    def is_zero_installs(self) -> bool:
        """If a project is using the zero-installs workflow or not.

        This is determined either by the existence of a non-empty yarn cache folder
        (default PnP mode) or by a presence of an expanded node_modules directory which would work
        similarly or exactly the same way as with the NPM ecosystem.
        For more details on zero-installs, see: https://v3.yarnpkg.com/features/zero-installs.
        """
        node_linker = self.yarn_rc.get("nodeLinker")
        if node_linker is None or node_linker == "pnp":
            if self.yarn_cache.path.exists() and self.yarn_cache.path.is_dir():
                # in this case the cache folder will be populated with downloaded ZIP dependencies
                return any(file.suffix == ".zip" for file in self.yarn_cache.path.iterdir())

        elif node_linker == "pnpm" or node_linker == "node-modules":
            # in this case the cache may or may not be populated with ZIP files because an expanded
            # node_modules directory tree just like with NPM is enough for zero installs to work
            return self.source_dir.join_within_root("node_modules").path.exists()

        return False

    @property
    def yarn_cache(self) -> RootedPath:
        """The path to the yarn cache folder.

        The cache location is affected by the cacheFolder configuration in yarnrc. See:
        https://v3.yarnpkg.com/configuration/yarnrc#cacheFolder.
        """
        return self.source_dir.join_within_root(self.yarn_rc.get("cacheFolder", "./.yarn/cache"))

    @classmethod
    def from_source_dir(cls, source_dir: RootedPath) -> "Project":
        """Create a Project from a sources directory path."""
        yarn_rc_path = source_dir.join_within_root(".yarnrc.yml")

        if yarn_rc_path.path.exists():
            yarn_rc = YarnRc.from_file(yarn_rc_path)
        else:
            yarn_rc = YarnRc(yarn_rc_path, {})

        package_json = PackageJson.from_file(source_dir.join_within_root("package.json"))
        return cls(source_dir, yarn_rc, package_json)


def get_semver_from_yarn_path(yarn_path: str | None) -> semver.version.Version | None:
    """Parse yarnPath from yarnrc and return a semver Version if possible else None."""
    if not yarn_path:
        return None

    # https://github.com/yarnpkg/berry/blob/2dc59443e541098bc0104d97b5fc452781c64baf/packages/plugin-essentials/sources/commands/set/version.ts#L208
    yarn_spec_pattern = re.compile(r"^yarn-(.+)\.cjs$")
    match = yarn_spec_pattern.match(Path(yarn_path).name)
    if not match:
        log.warning(
            (
                "The yarn version specified by yarnPath in .yarnrc.yml (%s) does not match the "
                "expected format yarn-<semver>.cjs. Attempting to use the version specified by "
                "packageManager in package.json."
            ),
            yarn_path,
        )
        return None

    yarn_version = match.group(1)
    try:
        return semver.version.Version.parse(yarn_version)
    except ValueError:
        log.warning(
            (
                "The yarn version specified by yarnPath in .yarnrc.yml (%s) is not a valid semver. "
                "Attempting to use the version specified by packageManager in package.json."
            ),
            yarn_path,
        )
        return None


def get_semver_from_package_manager(
    package_manager: str | None,
) -> semver.version.Version | None:
    """Parse packageManager from package.json and return a semver Version if possible.

    :raises UnexpectedFormat:
        if packageManager doesn't match the name@semver format
        if packageManager does not specify yarn
        if packageManager version is not a valid semver
    """
    if not package_manager:
        return None

    # https://github.com/nodejs/corepack/blob/787e24df609513702eafcd8c6a5f03544d7d45cc/sources/specUtils.ts#L10
    package_manager_spec_pattern = re.compile(r"^(?!_)(.+)@(.+)$")
    match = package_manager_spec_pattern.match(package_manager)
    if not match:
        raise UnexpectedFormat(
            "could not parse packageManager spec in package.json (expected name@semver)"
        )

    name, version = match.groups()
    if name != "yarn":
        raise UnexpectedFormat("packageManager in package.json must be yarn")

    try:
        return semver.version.Version.parse(version)
    except ValueError as e:
        raise UnexpectedFormat(
            f"{version} is not a valid semver for packageManager in package.json"
        ) from e
