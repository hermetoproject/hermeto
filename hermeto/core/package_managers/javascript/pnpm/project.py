# SPDX-License-Identifier: GPL-3.0-only
from collections import UserDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from semver import Version

from hermeto.core.errors import LockfileNotFound, UnsupportedFeature
from hermeto.core.package_managers.npm import NPM_REGISTRY_URL


class PnpmLock(UserDict):
    """Class representing a pnpm-lock.yaml file."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        """Initialize a PnpmLock object."""
        self.path = path
        super().__init__(data)

    @classmethod
    def from_file(cls, path: Path) -> "PnpmLock":
        """Create a PnpmLock object from a pnpm-lock.yaml file."""
        if not path.exists():
            raise LockfileNotFound(
                path, solution="Run 'pnpm install' to generate the pnpm-lock.yaml file."
            )

        with path.open() as f:
            data = yaml.safe_load(f) or {}
            return cls(path, data)

    @classmethod
    def from_dir(cls, path: Path) -> "PnpmLock":
        """Create a PnpmLock object from a directory containing a pnpm-lock.yaml file."""
        return cls.from_file(path.joinpath("pnpm-lock.yaml"))

    @property
    def packages(self) -> dict[str, dict[str, Any]]:
        """Return the 'packages' key from the pnpm-lock.yaml file."""
        return self.get("packages", {})


@dataclass(frozen=True)
class PnpmPackage:
    """Class representing a package from a pnpm-lock.yaml file."""

    id: str
    scope: str
    name: str
    version: str
    url: str
    integrity: str | None = None  # git and URL dependencies don't have an integrity field


def ensure_lockfile_version_is_supported(lockfile: PnpmLock) -> None:
    """Ensure Hermeto supports the lockfile version in the pnpm-lock.yaml file."""
    raw_version = str(lockfile.get("lockfileVersion"))
    version = Version.parse(raw_version, optional_minor_and_patch=True)
    if version.major != 9:
        raise UnsupportedFeature(f"Unsupported lockfileVersion: '{raw_version}'")


def parse_packages(lockfile: PnpmLock) -> list[PnpmPackage]:
    """Parse the packages from the pnpm-lock.yaml file."""
    result: list[PnpmPackage] = []

    for id, data in lockfile.packages.items():
        scope, name, version_from_id = _parse_package_metadata(id)
        # git and URL dependencies have an extra version field
        version = data.get("version") or version_from_id
        resolution = data.get("resolution", {})
        url = _resolve_package_tarball_url(scope, name, version, resolution)
        integrity = resolution.get("integrity")
        result.append(PnpmPackage(id, scope, name, version, url, integrity))

    return result


def _parse_package_metadata(id: str) -> tuple[str, str, str]:
    """
    Parse the package scope, name, and version from the package ID.

    >>> _parse_package_metadata("foo@1.0.0")
    ('', 'foo', '1.0.0')
    >>> _parse_package_metadata("@foo/bar@1.0.0")
    ('foo', 'bar', '1.0.0')
    >>> _parse_package_metadata("@jsr/foo__bar@1.0.0")
    ('foo', 'bar', '1.0.0')
    >>> _parse_package_metadata("@jsr/foo@1.0.0")
    ('', 'foo', '1.0.0')
    """
    # JSR format
    if id.startswith("@jsr/"):
        full_name, version = id.removeprefix("@jsr/").split("@", maxsplit=1)
        if "__" in full_name:
            scope, name = full_name.split("__", 1)
        else:
            scope = ""
            name = full_name

        return scope, name, version

    # Scoped format
    if id.startswith("@"):
        scope, full_name = id.split("/", maxsplit=1)
        name, version = full_name.split("@", maxsplit=1)
        return scope.removeprefix("@"), name, version

    # Unscoped format
    if "@" not in id:
        raise ValueError(f"Invalid package id: {id}")

    name, version = id.split("@", maxsplit=1)
    return "", name, version


def _resolve_package_tarball_url(
    scope: str, name: str, version: str, resolution: dict[str, str]
) -> str:
    return resolution.get("tarball") or _construct_npm_registry_tarball_url(scope, name, version)


def _construct_npm_registry_tarball_url(scope: str, name: str, version: str) -> str:
    """
    Construct the tarball URL for a package from the npm registry.

    >>> _construct_npm_registry_tarball_url("", "vue", "1.0.0")
    'https://registry.npmjs.org/vue/-/vue-1.0.0.tgz'
    >>> _construct_npm_registry_tarball_url("vue", "core", "1.0.0")
    'https://registry.npmjs.org/@vue/core/-/core-1.0.0.tgz'
    """
    if scope:
        # The URL for scoped packages must include the '@' prefix.
        return f"{NPM_REGISTRY_URL}/@{scope}/{name}/-/{name}-{version}.tgz"

    return f"{NPM_REGISTRY_URL}/{name}/-/{name}-{version}.tgz"
