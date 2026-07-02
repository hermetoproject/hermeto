# SPDX-License-Identifier: GPL-3.0-only
"""This module provides functionality to parse and validate pylock lockfiles."""

from abc import ABC, abstractmethod
from copy import deepcopy
from functools import cached_property
from pathlib import Path
from typing import Any

import pydantic
import tomlkit
from packaging.specifiers import Specifier
from packaging.utils import canonicalize_name
from packaging.version import Version
from tomlkit.exceptions import ParseError
from typing_extensions import Self

from hermeto.core.errors import InvalidLockfileFormat, PackageRejected
from hermeto.core.package_managers.pip.requirements import PipRequirement
from hermeto.core.rooted_path import RootedPath


class _SourceModel(pydantic.BaseModel, extra="ignore"):
    url: str | None = None
    hashes: dict[str, str] = {}
    subdirectory: str | None = None

    @pydantic.model_validator(mode="before")
    @classmethod
    def _reject_local_path(cls, data: Any) -> Any:
        if isinstance(data, dict) and "path" in data:
            raise PackageRejected(
                "Package uses a local path source which is not supported.",
                solution="Use a remote URL instead of a local path.",
            )
        return data


class _VCSSourceModel(_SourceModel):
    model_config = pydantic.ConfigDict(populate_by_name=True)

    url: str
    type: str
    commit_id: str = pydantic.Field(alias="commit-id")


class _ArchiveSourceModel(_SourceModel):
    url: str


class _PackageModel(pydantic.BaseModel, extra="ignore"):
    name: str
    version: str | None = None
    dependencies: list[str] | None = None
    index: str | None = None
    vcs: _VCSSourceModel | None = None
    archive: _ArchiveSourceModel | None = None
    directory: Any = None
    sdist: _SourceModel | None = None
    wheels: list[_SourceModel] | None = None

    @pydantic.model_validator(mode="after")
    def _reject_directory(self) -> Self:
        if self.directory is not None:
            raise PackageRejected(
                f"Package '{self.name}' uses a local directory source which is not supported.",
                solution="Replace the directory dependency.",
            )
        return self


class _LockfileModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(populate_by_name=True, extra="ignore")

    lock_version: str = pydantic.Field(alias="lock-version")
    packages: list[Any]

    @pydantic.field_validator("lock_version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if Version(v) not in Specifier("~=1.0"):
            raise ValueError(f"unsupported lockfile version: {v}")
        return v


class PipLockfile(ABC):
    """Base class for a pip lock-style files."""


class PyLockfileV1(PipLockfile):
    """Parser for pylock lockfile version 1.x."""

    def __init__(self, path: RootedPath) -> None:
        """Initialize a PipLockfile object."""
        self.file_path = path
        self._data = self._load_data()

        try:
            _LockfileModel.model_validate(self._data)
        except pydantic.ValidationError as e:
            raise InvalidLockfileFormat(
                lockfile_path=self.file_path.path,
                err_details=str(e),
                solution="Regenerate the lockfile with the correct version.",
            ) from e

    def _load_data(self) -> dict[str, Any]:
        """Load the data from the lockfile."""
        with open(self.file_path) as f:
            try:
                data = tomlkit.load(f)
            except ParseError:
                raise InvalidLockfileFormat(
                    lockfile_path=self.file_path.path,
                    err_details="Invalid TOML syntax.",
                    solution="Regenerate the lockfile.",
                )
            return data

    def merge_wheel_hashes(self) -> None:
        """Merge the wheel hashes into the sdist hashes."""
        for package in self.packages:
            if isinstance(package, PyLockIndexPackage):
                package.hashes.extend(package.wheel_hashes)

    @cached_property
    def packages(self) -> list["PyLockPackage"]:
        """Get the packages from the lockfile."""
        return [
            PyLockPackage.from_package_data(package_data, self.file_path.path)
            for package_data in self._data.get("packages", [])
        ]

    @property
    def requirements(self) -> list[PipRequirement]:
        """Alias for `packages` property."""
        return list(self.packages)

    @cached_property
    def data(self) -> dict[str, Any]:
        """Get a deepcopy of the data from the lockfile."""
        return deepcopy(self._data)


class PyLockArtifact:
    """Common fields shared by VCS, archive, and sdist source entries."""

    def __init__(
        self,
        url: str | None = None,
        hashes: list[str] | None = None,
        subdirectory: str | None = None,
    ) -> None:
        """Initialize a PyLockArtifact."""
        self.url = url
        self.hashes = hashes or []
        self.subdirectory = subdirectory

    @classmethod
    def from_source(cls, source: dict[str, Any], pkg_name: str, lockfile_path: Path) -> Self:
        """Parse common artifact fields from a source dict.

        Rejects local path sources since Hermeto cannot fetch or verify them.
        """
        if "path" in source:
            raise PackageRejected(
                f"Package '{pkg_name}' in {lockfile_path} uses a local path source which is "
                f"not supported by Hermeto because it cannot be fetched or verified.",
                solution="Replace the local path with a remote URL.",
            )

        return cls(
            url=source.get("url"),
            hashes=[f"{alg}:{dig}" for alg, dig in source.get("hashes", {}).items()],
            subdirectory=source.get("subdirectory"),
        )


class PyLockPackage(PipRequirement, ABC):
    """Base class for a package from pylock data.

    Inherits from PipRequirement so pylock packages can be used directly
    in the existing pip download pipeline.
    """

    def __init__(self) -> None:
        """Initialize a PyLockPackage object."""
        super().__init__()
        self.name: str = ""
        self.version: str | None = None
        self.dependencies: list[str] | None = None

    @classmethod
    def from_package_data(cls, data: dict[str, Any], path: Path) -> "PyLockPackage":
        """Parse a package from pylock data."""
        try:
            _PackageModel.model_validate(data)
        except pydantic.ValidationError as e:
            raise InvalidLockfileFormat(
                lockfile_path=path,
                err_details=str(e),
                solution="Fix the package entry in the lockfile.",
            ) from e

        package: PyLockPackage
        if "vcs" in data:
            package = PyLockVCSPackage.from_data(data, path)
        elif "archive" in data:
            package = PyLockArchivePackage.from_data(data, path)
        else:
            package = PyLockIndexPackage.from_data(data, path)

        package.name = data["name"]
        package.version = data.get("version")
        package.dependencies = data.get("dependencies")

        package.package = canonicalize_name(data["name"])
        package.raw_package = data["name"]

        return package

    @classmethod
    @abstractmethod
    def from_data(cls, data: dict[str, Any], path: Path) -> Self:
        """Parse a package from pylock data."""


class PyLockVCSPackage(PyLockPackage):
    """A VCS package from pylock data."""

    def __init__(self) -> None:
        """Initialize a PyLockVCSPackage object."""
        super().__init__()
        self.kind = "vcs"
        self.vcs_type: str = ""
        self.commit_id: str = ""
        self.subdirectory: str | None = None

    @classmethod
    def from_data(cls, data: dict[str, Any], path: Path) -> Self:
        """Parse a VCS package from pylock data."""
        vcs = data["vcs"]
        artifact = PyLockArtifact.from_source(vcs, data["name"], path)

        package = cls()
        package.vcs_type = vcs["type"]
        package.commit_id = vcs["commit-id"]
        package._url = f"{package.vcs_type}+{artifact.url}@{package.commit_id}"
        package.subdirectory = artifact.subdirectory
        package.download_line = f"{data['name']} @ {package._url}"

        return package


class PyLockArchivePackage(PyLockPackage):
    """An archive package from pylock data."""

    def __init__(self) -> None:
        """Initialize a PyLockArchivePackage object."""
        super().__init__()
        self.kind = "url"
        self.subdirectory: str | None = None

    @classmethod
    def from_data(cls, data: dict[str, Any], path: Path) -> Self:
        """Parse an archive package from pylock data."""
        artifact = PyLockArtifact.from_source(data["archive"], data["name"], path)

        package = cls()
        package._url = artifact.url
        package.hashes = artifact.hashes
        package.subdirectory = artifact.subdirectory
        package.download_line = f"{data['name']} @ {artifact.url}"

        return package


class PyLockIndexPackage(PyLockPackage):
    """An index package from pylock data."""

    def __init__(self) -> None:
        """Initialize a PyLockIndexPackage object."""
        super().__init__()
        self.kind = "pypi"
        self.wheel_hashes: list[str] = []
        self.index: str | None = None

    @classmethod
    def from_data(cls, data: dict[str, Any], path: Path) -> Self:
        """Parse an index package from pylock data."""
        version = data.get("version")
        if not version:
            # https://peps.python.org/pep-0751/#packages-version
            raise PackageRejected(
                f"Index package '{data['name']}' in {path} is missing a version.",
                solution="Version SHOULD be specified when the version is known to "
                "be stable (i.e. when an sdist or wheels are specified).",
            )

        package = cls()
        package.download_line = f"{data['name']}=={version}"
        package.version_specs = [("==", version)]
        package.index = data.get("index")

        if sdist := data.get("sdist"):
            artifact = PyLockArtifact.from_source(sdist, data["name"], path)
            package.hashes = artifact.hashes
        for wheel in data.get("wheels", []):
            artifact = PyLockArtifact.from_source(wheel, data["name"], path)
            package.wheel_hashes.extend(artifact.hashes)

        return package
