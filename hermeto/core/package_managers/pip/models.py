# SPDX-License-Identifier: GPL-3.0-only
"""Data models for pip package manager artifacts and dependencies."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Union


@dataclass(frozen=True)
class CommonArtifact(ABC):
    """Base class for representing a packaged Python artifact."""

    # The package name.
    package: str

    # Path where the artifact is stored locally.
    path: Path

    # Path to the requirements file that specified this dependency.
    requirement_file: str

    # Whether this artifact is missing checksums in the requirements file.
    missing_req_file_checksum: bool

    # Whether this is a build-time dependency (from requirements-build.txt).
    build_dependency: bool

    # Discriminator set by each subclass.
    kind: Literal["pypi", "url", "vcs"] = field(init=False)

    @property
    @abstractmethod
    def version(self) -> str:
        """Version identifier for the artifact."""

    def to_filter_dict(self) -> dict[str, Any]:
        """Convert to dict format expected by filter_packages_with_rust_code."""
        return {
            "package": self.package,
            "path": self.path,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class PyPIArtifact(CommonArtifact):
    """Artifact downloaded from a Python Package Index (e.g., PyPI)."""

    # URL of the package index.
    index_url: str

    # Type of Python package distribution.
    package_type: Literal["sdist", "wheel"]

    _version: str

    @property
    def version(self) -> str:
        """Return version string for PyPI artifacts."""
        return self._version

    kind: Literal["pypi"] = field(default="pypi", init=False)


@dataclass(frozen=True)
class VCSArtifact(CommonArtifact):
    """Artifact downloaded from a version control system (e.g., git)."""

    # The VCS URL.
    url: str

    # The VCS host (e.g., github.com).
    host: str

    # The namespace/organization (e.g., 'containerbuildsystem').
    namespace: str

    # The repository name.
    repo: str

    # The git reference (commit hash, branch, tag).
    ref: str

    kind: Literal["vcs"] = field(default="vcs", init=False)

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("VCS artifact must have non-empty url")
        if not self.ref:
            raise ValueError("VCS artifact must have non-empty ref")
        if not self.host:
            raise ValueError("VCS artifact must have non-empty host")
        if not self.namespace:
            raise ValueError("VCS artifact must have non-empty namespace")
        if not self.repo:
            raise ValueError("VCS artifact must have non-empty repo")

    @property
    def version(self) -> str:
        """Return version string for VCS artifacts."""
        return f"git+{self.url}@{self.ref}"


@dataclass(frozen=True)
class URLArtifact(CommonArtifact):
    """Artifact downloaded from a direct URL."""

    # The original URL specified in requirements.
    original_url: str

    # The URL with hash fragment added if needed.
    url_with_hash: str

    # Package type, set to 'wheel' for wheel URLs, None otherwise.
    package_type: Literal["wheel"] | None = None

    kind: Literal["url"] = field(default="url", init=False)

    @property
    def version(self) -> str:
        """Return version string for URL artifacts."""
        return self.url_with_hash


# Type alias for any artifact download
Artifact = Union[PyPIArtifact, VCSArtifact, URLArtifact]
