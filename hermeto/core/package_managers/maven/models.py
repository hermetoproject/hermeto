import json
from dataclasses import dataclass
from typing import Any

from packageurl import PackageURL

from hermeto.core.rooted_path import RootedPath


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
        return self._dependency_dict["groupId"]

    @property
    def artifact_id(self) -> str:
        """Get the artifact ID."""
        return self._dependency_dict["artifactId"]

    @property
    def version(self) -> str:
        """Get the version."""
        return self._dependency_dict["version"]
    
    @property
    def classifier(self) -> str | None:
        """Get the classifier."""
        return self._dependency_dict.get("classifier")
    
    @property
    def artifact_type(self) -> str:
        """Get the artifact type. Defaults to "jar"."""
        return self._dependency_dict.get("type", "jar")

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
        return self._dependency_dict.get("resolved")

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
        if self.classifier:
            purl.qualifiers["classifier"] = self.classifier
        if self.artifact_type != "jar":
            purl.qualifiers["type"] = self.artifact_type

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
                    "classifier": dependency.classifier,
                    "artifact_type": dependency.artifact_type,
                }

        return result

    def get_plugins_to_download(self) -> dict[str, dict[str, str | None]]:
        """Get dictionary of plugins and their dependencies to download."""
        result = {}

        def extract_dependency(dependency: dict[str, Any]) -> None:
            """Recursively extract a dependency and its children."""
            resolved_url = dependency.get("resolved")
            if resolved_url and resolved_url not in result:
                result[resolved_url] = {
                    "checksum": dependency.get("checksum"),
                    "checksum_algorithm": dependency.get("checksumAlgorithm"),
                    "group_id": dependency.get("groupId"),
                    "artifact_id": dependency.get("artifactId"),
                    "version": dependency.get("version"),
                    "classifier": dependency.get("classifier"),
                    "artifact_type": dependency.get("type", "jar"),
                }

            for child in dependency.get("children", []):
                extract_dependency(child)

        for plugin in self.lockfile_data.get("mavenPlugins", []):
            resolved_url = plugin.get("resolved")
            if resolved_url:
                result[resolved_url] = {
                    "checksum": plugin.get("checksum"),
                    "checksum_algorithm": plugin.get("checksumAlgorithm"),
                    "group_id": plugin.get("groupId"),
                    "artifact_id": plugin.get("artifactId"),
                    "version": plugin.get("version"),
                    "classifier": plugin.get("classifier"),
                    "artifact_type": plugin.get("type", "jar"),
                }

            for dependency in plugin.get("dependencies", []):
                extract_dependency(dependency)

        return result
