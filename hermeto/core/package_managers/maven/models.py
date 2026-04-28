# SPDX-License-Identifier: GPL-3.0-only
import json
from collections import UserDict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hermeto.core.errors import LockfileNotFound


class MavenLockfile:
    """Class representing JSON lockfile for Maven."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        """Initialize a MavenLockfile object."""
        self.path = path
        self.data = data

    @classmethod
    def from_file(cls, path: Path) -> "MavenLockfile":
        """Create a MavenLockfile object from the provided path."""
        if not path.exists():
            raise LockfileNotFound(
                path, solution="Ensure the Maven lockfile exists at the specified path."
            )

        with path.open() as f:
            data = json.load(f)

        return cls(path, data)


class MavenArtifact(UserDict):
    """Class representing a Maven artifact from the lockfile."""

    url: str
    group_id: str
    artifact_id: str
    version: str
    checksum_algorithm: str
    checksum: str
    scope: str

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize a MavenArtifact object."""
        self.url = data["resolved"]
        self.group_id = data["groupId"]
        self.artifact_id = data["artifactId"]
        self.version = data["version"]
        self.checksum_algorithm = _get_checksum_algorithm(data["checksumAlgorithm"])
        self.checksum = data["checksum"]
        self.scope = data.get("scope", "compile")  # fallback to 'compile' scope
        super().__init__(data)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MavenArtifact):
            return NotImplemented

        return (
            self.url == other.url
            and self.group_id == other.group_id
            and self.artifact_id == other.artifact_id
            and self.version == other.version
            and self.checksum_algorithm == other.checksum_algorithm
            and self.checksum == other.checksum
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.url,
                self.group_id,
                self.artifact_id,
                self.version,
                self.checksum_algorithm,
                self.checksum,
            )
        )

    @property
    def filename(self) -> str:
        """Get the filename of the artifact."""
        parsed_url = urlparse(self.url)
        return Path(parsed_url.path).name

    @property
    def artifact_relative_dir(self) -> Path:
        """Get the relative artifact directory."""
        group_dir = self.group_id.replace(".", "/")
        return Path(group_dir) / self.artifact_id / self.version


def _get_checksum_algorithm(java_algorithm: str) -> str:
    """
    Convert Java checksum algorithm name to Python hashlib algorithm name.

    >>> _get_checksum_algorithm("SHA-256")
    'sha256'
    >>> _get_checksum_algorithm("SHA-512")
    'sha512'
    >>> _get_checksum_algorithm("MD5")
    'md5'
    """
    return java_algorithm.replace("-", "").lower()


def _extract_pom_chain(pom: dict[str, Any] | None, result: list[MavenArtifact]) -> None:
    if not pom or not isinstance(pom, dict):
        return

    result.append(MavenArtifact(pom))
    _extract_pom_chain(pom.get("parent"), result)
    for bom in pom.get("boms", []):
        _extract_pom_chain(bom, result)


def _extract_artifact(artifact: dict[str, Any], result: list[MavenArtifact]) -> None:
    result.append(MavenArtifact(artifact))
    _extract_pom_chain(artifact.get("parent"), result)
    _extract_pom_chain(artifact.get("parentPom"), result)
    _extract_pom_chain(artifact.get("pom"), result)

    for bom in artifact.get("boms", []):
        _extract_pom_chain(bom, result)

    for child in artifact.get("children", []):
        _extract_artifact(child, result)

    for dep in artifact.get("dependencies", []):
        _extract_artifact(dep, result)


def _parse_dependencies(lockfile: MavenLockfile) -> list[MavenArtifact]:
    result: list[MavenArtifact] = []
    for dependency in lockfile.data.get("dependencies", []):
        _extract_artifact(dependency, result)

    return result


def _parse_plugins(lockfile: MavenLockfile) -> list[MavenArtifact]:
    result: list[MavenArtifact] = []
    for plugin in lockfile.data.get("mavenPlugins", []):
        _extract_artifact(plugin, result)

    return result


def _parse_parents(lockfile: MavenLockfile) -> list[MavenArtifact]:
    result: list[MavenArtifact] = []
    root_pom = lockfile.data.get("pom", {}).get("parent", {})
    _extract_artifact(root_pom, result)
    return result


def parse_maven_artifacts(lockfile: MavenLockfile) -> set[MavenArtifact]:
    """
    Parse all Maven artifacts from the lockfile to a set.

    The same resolved URL can appear multiple times (e.g. shared transitive deps across plugins).
    """
    merged = _parse_dependencies(lockfile) + _parse_plugins(lockfile) + _parse_parents(lockfile)
    return set(merged)
