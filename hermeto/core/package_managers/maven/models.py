# SPDX-License-Identifier: GPL-3.0-only
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from packageurl import PackageURL

from hermeto.core.errors import PackageRejected
from hermeto.core.rooted_path import RootedPath


def _require_https(url: str) -> str:
    """Reject non-HTTPS resolved URLs to prevent SSRF via malicious lockfiles."""
    if urlparse(url).scheme != "https":
        raise PackageRejected(
            f"Resolved URL must use HTTPS: {url!r}",
            solution="Ensure all resolved URLs in the lockfile use the https:// scheme",
        )
    return url


@dataclass
class MavenComponent:
    """Maven component."""

    name: str
    purl: str
    version: str
    scope: str


class MavenDependency:
    """Maven dependency from lockfile.json."""

    def __init__(self, dependency_dict: dict[str, Any]) -> None:
        """Initialize a MavenDependency."""
        self._dependency_dict = dependency_dict

    @property
    def group_id(self) -> str:
        """Get the group ID."""
        value = self._dependency_dict.get("groupId")
        if not value:
            raise PackageRejected(
                "A dependency in the lockfile is missing the required 'groupId' field",
                solution="Ensure all dependencies in the lockfile have a valid 'groupId'",
            )
        return value

    @property
    def artifact_id(self) -> str:
        """Get the artifact ID."""
        value = self._dependency_dict.get("artifactId")
        if not value:
            raise PackageRejected(
                "A dependency in the lockfile is missing the required 'artifactId' field",
                solution="Ensure all dependencies in the lockfile have a valid 'artifactId'",
            )
        return value

    @property
    def version(self) -> str:
        """Get the version."""
        value = self._dependency_dict.get("version")
        if not value:
            raise PackageRejected(
                "A dependency in the lockfile is missing the required 'version' field",
                solution="Ensure all dependencies in the lockfile have a valid 'version'",
            )
        return value

    @property
    def name(self) -> str:
        """Get the full name (groupId:artifactId)."""
        return f"{self.group_id}:{self.artifact_id}"

    @property
    def scope(self) -> str:
        """Get the dependency scope."""
        return self._dependency_dict.get("scope", "compile")

    @property
    def checksum(self) -> str | None:
        """Get the checksum."""

        # Some checksums have additional information after the hash, so we need to split and take the first part
        raw_checksum = self._dependency_dict.get("checksum")
        if raw_checksum:
            return raw_checksum.split()[0]

        return None

    @property
    def checksum_algorithm(self) -> str | None:
        """Get the checksum algorithm."""
        return self._dependency_dict.get("checksumAlgorithm")

    @property
    def resolved_url(self) -> str | None:
        """Get the resolved URL."""
        url = self._dependency_dict.get("resolved")
        return _require_https(url) if url else None

    @property
    def children(self) -> list[dict[str, Any]]:
        """Get the children dependencies."""
        return self._dependency_dict.get("children", [])

    def to_component(self) -> MavenComponent:
        """Convert to MavenComponent."""
        purl = PackageURL(
            type="maven",
            namespace=self.group_id,
            name=self.artifact_id,
            version=self.version,
        )
        return MavenComponent(
            name=self.name,
            purl=purl.to_string(),
            version=self.version,
            scope=self.scope,
        )


class MavenLockfile:
    """A Maven lockfile.json file."""

    def __init__(self, lockfile_path: RootedPath, lockfile_data: dict[str, Any]) -> None:
        """Initialize a MavenLockfile."""
        self.lockfile_path = lockfile_path
        self.lockfile_data = lockfile_data
        self.dependencies = self._parse_dependencies()

    def _parse_dependencies(self) -> list[MavenDependency]:
        """Parse dependencies from lockfile data."""
        dependencies = []

        def parse_dependency_tree(dep_list: list[dict[str, Any]]) -> None:
            """Recursively parse dependency tree."""
            for dep_dict in dep_list:
                dep = MavenDependency(dep_dict)
                dependencies.append(dep)

                # recursively parse children
                if dep.children:
                    parse_dependency_tree(dep.children)

        parse_dependency_tree(self.lockfile_data.get("dependencies", []))
        return dependencies

    @classmethod
    def from_file(cls, lockfile_path: RootedPath) -> "MavenLockfile":
        """Create a MavenLockfile from a lockfile.json file."""
        with lockfile_path.path.open("r") as f:
            lockfile_data = json.load(f)

        return cls(lockfile_path, lockfile_data)

    def get_main_package(self) -> MavenComponent:
        """Get the main package as a MavenComponent."""
        group_id = self.lockfile_data["groupId"]
        artifact_id = self.lockfile_data["artifactId"]
        version = self.lockfile_data["version"]

        purl = PackageURL(
            type="maven",
            namespace=group_id,
            name=artifact_id,
            version=version,
        )

        return MavenComponent(
            name=f"{group_id}:{artifact_id}",
            purl=purl.to_string(),
            version=version,
            scope="compile",
        )

    def get_sbom_components(self) -> list[MavenComponent]:
        """Get all dependencies as MavenComponent objects."""
        return [dependency.to_component() for dependency in self.dependencies]

    def get_dependencies_to_download(self) -> dict[str, dict[str, str | None]]:
        """Get dictionary of dependencies to download."""
        result = {}

        for dependency in self.dependencies:
            if dependency.resolved_url:
                result[dependency.resolved_url] = {
                    "checksum": dependency.checksum,
                    "checksum_algorithm": dependency.checksum_algorithm,
                    "group_id": dependency.group_id,
                    "artifact_id": dependency.artifact_id,
                    "version": dependency.version,
                }

        return result

    def get_plugins_to_download(self) -> dict[str, dict[str, str | None]]:
        """Get dictionary of plugins and their dependencies to download."""
        result = {}

        def extract_dependency(dependency: dict[str, Any]) -> None:
            """Recursively extract a dependency and its children."""
            resolved_url = dependency.get("resolved")
            if resolved_url and resolved_url not in result:
                result[_require_https(resolved_url)] = {
                    "checksum": dependency.get("checksum"),
                    "checksum_algorithm": dependency.get("checksumAlgorithm"),
                    "group_id": dependency.get("groupId"),
                    "artifact_id": dependency.get("artifactId"),
                    "version": dependency.get("version"),
                }

            for child in dependency.get("children", []):
                extract_dependency(child)

        for plugin in self.lockfile_data.get("mavenPlugins", []):
            resolved_url = plugin.get("resolved")
            if resolved_url:
                result[_require_https(resolved_url)] = {
                    "checksum": plugin.get("checksum"),
                    "checksum_algorithm": plugin.get("checksumAlgorithm"),
                    "group_id": plugin.get("groupId"),
                    "artifact_id": plugin.get("artifactId"),
                    "version": plugin.get("version"),
                }

            for dependency in plugin.get("dependencies", []):
                extract_dependency(dependency)

        return result
