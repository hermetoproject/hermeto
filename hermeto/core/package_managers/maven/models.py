# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hermeto.core.errors import LockfileNotFound


class MavenLockfile(BaseModel):
    """Class representing JSON lockfile for Maven."""

    group_id: str = Field(alias="groupId")
    artifact_id: str = Field(alias="artifactId")
    version: str = Field(alias="version")
    pom: dict[str, Any] = Field(alias="pom")
    dependencies: list[dict[str, Any]] = Field(alias="dependencies")
    maven_plugins: list[dict[str, Any]] = Field(alias="mavenPlugins")

    model_config = ConfigDict(extra="allow")

    @classmethod
    def from_file(cls, path: Path) -> "MavenLockfile":
        """Create a MavenLockfile object from the provided path."""
        if not path.exists():
            raise LockfileNotFound(
                path, solution="Ensure the Maven lockfile exists at the specified path."
            )

        with path.open() as f:
            data = json.load(f)

        return cls.model_validate(data)


class MavenArtifact(BaseModel):
    """Class representing a Maven artifact from the lockfile."""

    url: str = Field(alias="resolved")
    group_id: str = Field(alias="groupId")
    artifact_id: str = Field(alias="artifactId")
    version: str = Field(alias="version")
    checksum_algorithm: str = Field(alias="checksumAlgorithm")
    checksum: str = Field(alias="checksum")

    @field_validator("checksum_algorithm", mode="before")
    @classmethod
    def _validate_checksum_algorithm(cls, value: str) -> str:
        return _convert_java_checksum_algorithm_to_hashlib(value)

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


def _convert_java_checksum_algorithm_to_hashlib(java_algorithm: str) -> str:
    """
    Convert the Java checksum algorithm name to the Python hashlib algorithm name.

    >>> _convert_java_checksum_algorithm_to_hashlib("SHA-256")
    'sha256'
    >>> _convert_java_checksum_algorithm_to_hashlib("SHA-512")
    'sha512'
    >>> _convert_java_checksum_algorithm_to_hashlib("MD5")
    'md5'
    """
    return java_algorithm.replace("-", "").lower()


def _extract_pom_chain(pom: dict[str, Any] | None, result: list[MavenArtifact]) -> None:
    """Recursively extract other Maven artifacts from the given POM."""
    if not pom or not isinstance(pom, dict):
        return

    result.append(MavenArtifact.model_validate(pom))
    _extract_pom_chain(pom.get("parent"), result)
    for bom in pom.get("boms", []):
        _extract_pom_chain(bom, result)


def _extract_artifact(artifact: dict[str, Any], result: list[MavenArtifact]) -> None:
    """Recursively extract other Maven artifacts from the given artifact."""
    result.append(MavenArtifact.model_validate(artifact))
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
    """Parse the dependencies from the lockfile."""
    result: list[MavenArtifact] = []
    for dependency in lockfile.dependencies:
        _extract_artifact(dependency, result)

    return result


def _parse_plugins(lockfile: MavenLockfile) -> list[MavenArtifact]:
    """Parse the Maven plugins from the lockfile."""
    result: list[MavenArtifact] = []
    for plugin in lockfile.maven_plugins:
        _extract_artifact(plugin, result)

    return result


def _parse_root_pom(lockfile: MavenLockfile) -> list[MavenArtifact]:
    """Parse the root POM from the lockfile."""
    result: list[MavenArtifact] = []
    root_pom = lockfile.pom.get("parent")
    if root_pom is not None:
        _extract_artifact(root_pom, result)

    boms = lockfile.pom.get("boms", [])
    for bom in boms:
        _extract_artifact(bom, result)

    return result


def parse_maven_artifacts(lockfile: MavenLockfile) -> set[MavenArtifact]:
    """
    Parse all Maven artifacts from the lockfile to a set.

    The same resolved URL can appear multiple times (e.g., shared transitive deps across plugins).
    """
    merged = _parse_dependencies(lockfile) + _parse_plugins(lockfile) + _parse_root_pom(lockfile)
    return set(merged)
