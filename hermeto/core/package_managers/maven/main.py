import asyncio
import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, TypedDict
from urllib.parse import urlparse

from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import PackageRejected, UnexpectedFormat
from hermeto.core.models.input import Request
from hermeto.core.models.output import ProjectFile, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

# Maven scope types
MAVEN_SCOPES = ("compile", "provided", "runtime", "test", "system", "import")
DEFAULT_LOCKFILE_NAME = "lockfile.json"

# Mapping from Java MessageDigest algorithm names to Python hashlib algorithm names
JAVA_TO_PYTHON_CHECKSUM_ALGORITHMS = {
    "SHA-256": "sha256",
    "SHA-1": "sha1", 
    "SHA-512": "sha512",
    "SHA-224": "sha224",
    "SHA-384": "sha384",
    "MD5": "md5",
}


def _convert_java_checksum_algorithm_to_python(java_algorithm: str) -> str:
    """Convert Java MessageDigest algorithm name to Python hashlib algorithm name.
    
    :param java_algorithm: Algorithm name from Java MessageDigest (e.g., "SHA-256")
    :return: Algorithm name compatible with Python hashlib (e.g., "sha256")
    :raises PackageRejected: If the algorithm is not supported
    """
    python_algorithm = JAVA_TO_PYTHON_CHECKSUM_ALGORITHMS.get(java_algorithm)
    if not python_algorithm:
        raise PackageRejected(
            f"Unsupported checksum algorithm: {java_algorithm}",
            solution=f"Supported algorithms: {', '.join(JAVA_TO_PYTHON_CHECKSUM_ALGORITHMS.keys())}"
        )
    return python_algorithm


def _derive_repository_id(url: str) -> str:
    """Derive a repository ID from a URL.
    
    :param url: The URL to derive repository ID from
    :return: Repository ID string
    """
    parsed_url = urlparse(url)
    hostname = parsed_url.hostname or "unknown"
    
    # Map common Maven repository hostnames to standard IDs
    hostname_to_id = {
        "repo1.maven.org": "central",
        "repo.maven.apache.org": "central", 
        "central.maven.org": "central",
        "oss.sonatype.org": "sonatype",
        "s01.oss.sonatype.org": "sonatype",
        "repository.jboss.org": "jboss",
        "repo.spring.io": "spring",
    }
    
    return hostname_to_id.get(hostname, hostname)


class MavenComponentInfo(TypedDict):
    """Contains the data needed to generate a maven SBOM component."""

    name: str
    purl: str
    version: str
    scope: str
    included: bool
    missing_hash_in_file: Optional[Path]


class ResolvedMavenPackage(TypedDict):
    """Contains all of the data for a resolved maven package."""

    package: MavenComponentInfo
    dependencies: list[MavenComponentInfo]
    projectfiles: list[ProjectFile]


class MavenDependency:
    """A Maven dependency from lockfile.json."""

    def __init__(self, dependency_dict: dict[str, Any]) -> None:
        """Initialize a MavenDependency.

        :param dependency_dict: the raw dict for a dependency from lockfile.json
        """
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
    def name(self) -> str:
        """Get the full name (groupId:artifactId)."""
        return f"{self.group_id}:{self.artifact_id}"

    @property
    def scope(self) -> str:
        """Get the dependency scope."""
        return self._dependency_dict.get("scope", "compile")

    @property
    def checksum(self) -> Optional[str]:
        """Get the checksum."""

        # Some checksums have additional information after the hash, so we need to split and take the first part
        raw_checksum = self._dependency_dict.get("checksum")
        if raw_checksum:
            return raw_checksum.split()[0]
        return None

    @property
    def checksum_algorithm(self) -> Optional[str]:
        """Get the checksum algorithm."""
        return self._dependency_dict.get("checksumAlgorithm")

    @property
    def resolved_url(self) -> Optional[str]:
        """Get the resolved URL."""
        return self._dependency_dict.get("resolved")

    @property
    def included(self) -> bool:
        """Return True if this dependency should be included."""
        return self._dependency_dict.get("included", True)

    @property
    def children(self) -> list[dict[str, Any]]:
        """Get the children dependencies."""
        return self._dependency_dict.get("children", [])

    def to_component_info(self) -> MavenComponentInfo:
        """Convert to MavenComponentInfo."""
        purl = PackageURL(
            type="maven",
            namespace=self.group_id,
            name=self.artifact_id,
            version=self.version,
        )

        return MavenComponentInfo(
            name=self.name,
            purl=str(purl),
            version=self.version,
            scope=self.scope,
            included=self.included,
            missing_hash_in_file=None if self.checksum else Path("lockfile.json"),
        )


class MavenLockfile:
    """A Maven lockfile.json file."""

    def __init__(self, lockfile_path: RootedPath, lockfile_data: dict[str, Any]) -> None:
        """Initialize a MavenLockfile."""
        self._lockfile_path = lockfile_path
        self._lockfile_data = lockfile_data
        self._dependencies = self._parse_dependencies()

    @property
    def lockfile_data(self) -> dict[str, Any]:
        """Get content of lockfile.json stored in Dictionary."""
        return self._lockfile_data

    @property
    def dependencies(self) -> list[MavenDependency]:
        """Get list of dependencies loaded from lockfile.json."""
        return self._dependencies

    @property
    def main_package_info(self) -> dict[str, Any]:
        """Get the main package information."""
        return {
            "groupId": self._lockfile_data["groupId"],
            "artifactId": self._lockfile_data["artifactId"],
            "version": self._lockfile_data["version"],
        }

    def _parse_dependencies(self) -> list[MavenDependency]:
        """Parse dependencies from lockfile data."""
        dependencies = []
        
        def parse_dependency_tree(dep_list: list[dict[str, Any]]) -> None:
            """Recursively parse dependency tree."""
            for dep_dict in dep_list:
                dep = MavenDependency(dep_dict)
                dependencies.append(dep)
                
                # Recursively parse children
                if dep.children:
                    parse_dependency_tree(dep.children)

        parse_dependency_tree(self._lockfile_data.get("dependencies", []))
        return dependencies

    @classmethod
    def from_file(cls, lockfile_path: RootedPath) -> "MavenLockfile":
        """Create a MavenLockfile from a lockfile.json file."""
        try:
            with lockfile_path.path.open("r") as f:
                lockfile_data = json.load(f)
        except FileNotFoundError:
            raise PackageRejected(
                f"The {DEFAULT_LOCKFILE_NAME} file must be present for the maven package manager",
                solution=f"Please ensure that {DEFAULT_LOCKFILE_NAME} is present in the package directory",
            )
        except json.JSONDecodeError as e:
            raise UnexpectedFormat(
                f"Invalid JSON in {lockfile_path.subpath_from_root}: {e}",
                solution="Please ensure the lockfile.json contains valid JSON",
            )

        return cls(lockfile_path, lockfile_data)

    def get_project_file(self) -> ProjectFile:
        """Get the updated lockfile as a ProjectFile."""
        return ProjectFile(
            abspath=self._lockfile_path.path,
            template=json.dumps(self._lockfile_data, indent=2) + "\n",
        )

    def get_main_package(self) -> MavenComponentInfo:
        """Get the main package as a MavenComponentInfo."""
        main_info = self.main_package_info
        purl = PackageURL(
            type="maven",
            namespace=main_info["groupId"],
            name=main_info["artifactId"],
            version=main_info["version"],
        )

        return MavenComponentInfo(
            name=f"{main_info['groupId']}:{main_info['artifactId']}",
            purl=str(purl),
            version=main_info["version"],
            scope="compile",
            included=True,
            missing_hash_in_file=None,
        )

    def get_sbom_components(self) -> list[MavenComponentInfo]:
        """Get all dependencies as MavenComponentInfo objects."""
        components = []
        for dependency in self.dependencies:
            if dependency.included:
                components.append(dependency.to_component_info())
        return components

    def get_dependencies_to_download(self) -> dict[str, dict[str, Optional[str]]]:
        """Get dictionary of dependencies to download."""
        deps_to_download = {}
        
        for dependency in self.dependencies:
            if dependency.included and dependency.resolved_url:
                deps_to_download[dependency.resolved_url] = {
                    "checksum": dependency.checksum,
                    "checksum_algorithm": dependency.checksum_algorithm,
                    "group_id": dependency.group_id,
                    "artifact_id": dependency.artifact_id,
                    "version": dependency.version,
                }
        
        return deps_to_download


def _get_maven_dependencies(
    download_dir: RootedPath, deps_to_download: dict[str, dict[str, Optional[str]]]
) -> dict[str, RootedPath]:
    """Download Maven dependencies and return their local paths."""
    download_paths = {}
    files_to_download = {}
    # Track artifacts by directory to create _remote.repositories files
    artifacts_by_directory = {}
    
    for url, dep_info in deps_to_download.items():
        # Create Maven directory structure: groupId/artifactId/version/
        group_id = dep_info["group_id"] or ""
        group_path = group_id.replace(".", "/")
        artifact_path = f"{group_path}/{dep_info['artifact_id']}/{dep_info['version']}"
        
        # Extract filename from URL
        parsed_url = urlparse(url)
        filename = Path(parsed_url.path).name
        
        # Create local path
        local_path = download_dir.join_within_root(artifact_path, filename)
        local_path.path.parent.mkdir(parents=True, exist_ok=True)
        
        files_to_download[url] = local_path.path
        download_paths[url] = local_path
        
        # Track artifacts by directory for _remote.repositories
        artifact_dir = local_path.path.parent
        if artifact_dir not in artifacts_by_directory:
            artifacts_by_directory[artifact_dir] = []
        
        # Derive repository ID from URL
        repository_id = _derive_repository_id(url)
        artifacts_by_directory[artifact_dir].append({
            "filename": filename,
            "repository_id": repository_id,
            "url": url
        })
        
        # WORKAROUND: Also track .pom files for _remote.repositories
        pom_filename = Path(filename).stem + ".pom"
        artifacts_by_directory[artifact_dir].append({
            "filename": pom_filename,
            "repository_id": repository_id,
            "url": url.replace(filename, pom_filename)
        })

    # WORKAROUND: Download .pom files and their checksums since they should be in lockfile
    # TODO: Remove this workaround once lockfile includes .pom files and checksums
    pom_files_to_download = {}
    pom_checksums_to_download = {}
    
    for url, dep_info in deps_to_download.items():
        # Derive .pom URL by replacing the file extension
        parsed_url = urlparse(url)
        path_parts = Path(parsed_url.path)
        pom_filename = path_parts.stem + ".pom"
        pom_url = url.replace(path_parts.name, pom_filename)
        
        # Create local path for .pom file
        artifact_dir = download_paths[url].path.parent
        pom_local_path = artifact_dir / pom_filename
        pom_files_to_download[pom_url] = pom_local_path
        
        # If we have checksum info, try to download .pom checksum file
        if dep_info["checksum_algorithm"]:
            python_algorithm = _convert_java_checksum_algorithm_to_python(dep_info["checksum_algorithm"])
            checksum_extension = python_algorithm
            pom_checksum_url = f"{pom_url}.{checksum_extension}"
            pom_checksum_local_path = artifact_dir / f"{pom_filename}.{checksum_extension}"
            pom_checksums_to_download[pom_checksum_url] = pom_checksum_local_path

    # Download all files (artifacts, pom files, and pom checksums)
    all_files_to_download = {**files_to_download, **pom_files_to_download, **pom_checksums_to_download}
    
    if all_files_to_download:
        asyncio.run(
            async_download_files(
                all_files_to_download,
                get_config().concurrency_limit,
            )
        )

    # Verify checksums and save checksum files
    for url, dep_info in deps_to_download.items():
        if dep_info["checksum"] and dep_info["checksum_algorithm"]:
            python_algorithm = _convert_java_checksum_algorithm_to_python(dep_info["checksum_algorithm"])
            checksum_info = ChecksumInfo(
                algorithm=python_algorithm,
                hexdigest=dep_info["checksum"],
            )
            must_match_any_checksum(download_paths[url].path, [checksum_info])
            
            # Save checksum file with appropriate extension
            artifact_path = download_paths[url].path
            checksum_extension = python_algorithm
            checksum_file_path = artifact_path.with_suffix(f"{artifact_path.suffix}.{checksum_extension}")
            
            with checksum_file_path.open("w") as checksum_file:
                checksum_file.write(f"{dep_info['checksum']}\n")
        else:
            log.warning(f"Missing checksum for {url}, integrity check skipped.")

    # WORKAROUND: Verify .pom file checksums if available
    # TODO: Remove this workaround once lockfile includes .pom checksums
    for url, dep_info in deps_to_download.items():
        if dep_info["checksum_algorithm"]:
            python_algorithm = _convert_java_checksum_algorithm_to_python(dep_info["checksum_algorithm"])
            checksum_extension = python_algorithm
            
            # Derive .pom paths
            parsed_url = urlparse(url)
            path_parts = Path(parsed_url.path)
            pom_filename = path_parts.stem + ".pom"
            artifact_dir = download_paths[url].path.parent
            pom_local_path = artifact_dir / pom_filename
            pom_checksum_local_path = artifact_dir / f"{pom_filename}.{checksum_extension}"
            
            # Verify .pom checksum if checksum file was downloaded
            if pom_checksum_local_path.exists():
                try:
                    with pom_checksum_local_path.open("r") as f:
                        pom_checksum = f.read().strip().split()[0]  # Take first part in case of additional info
                    
                    if pom_local_path.exists():
                        checksum_info = ChecksumInfo(
                            algorithm=python_algorithm,
                            hexdigest=pom_checksum,
                        )
                        must_match_any_checksum(pom_local_path, [checksum_info])
                        log.debug(f"Verified checksum for {pom_local_path}")
                    else:
                        log.warning(f"POM file {pom_local_path} not found for checksum verification")
                except Exception as e:
                    log.warning(f"Failed to verify checksum for {pom_local_path}: {e}")
            else:
                log.debug(f"No checksum file available for {pom_filename}, skipping verification")

    # Create _remote.repositories files
    for artifact_dir, artifacts in artifacts_by_directory.items():
        remote_repos_file = Path(artifact_dir) / "_remote.repositories"
        with remote_repos_file.open("w") as f:
            f.write("#NOTE: This is a Maven Resolver internal implementation file, its format can be changed without prior notice.\n")
            f.write("#Generated by Hermeto\n")
            for artifact in artifacts:
                f.write(f"{artifact['filename']}={artifact['repository_id']}\n")

    return download_paths


def _update_maven_lockfile_with_local_paths(
    download_paths: dict[str, RootedPath],
    maven_lockfile: MavenLockfile,
) -> None:
    """Update lockfile.json with local paths to downloaded dependencies."""
    
    def update_dependency_paths(dep_list: list[dict[str, Any]]) -> None:
        """Recursively update dependency paths."""
        for dep_dict in dep_list:
            resolved_url = dep_dict.get("resolved")
            if resolved_url and resolved_url in download_paths:
                local_path = download_paths[resolved_url]
                # Use template variable for output directory
                templated_path = Path("${output_dir}", local_path.subpath_from_root)
                dep_dict["resolved"] = f"file://{templated_path}"
            
            # Recursively update children
            if dep_dict.get("children"):
                update_dependency_paths(dep_dict["children"])

    update_dependency_paths(maven_lockfile.lockfile_data.get("dependencies", []))


def _generate_component_list(component_infos: list[MavenComponentInfo]) -> list[Component]:
    """Convert a list of MavenComponentInfo objects into a list of Component objects for the SBOM."""

    def to_component(component_info: MavenComponentInfo) -> Component:
        if component_info["missing_hash_in_file"]:
            missing_hash = frozenset({str(component_info["missing_hash_in_file"])})
        else:
            missing_hash = frozenset()

        return Component(
            name=component_info["name"],
            version=component_info["version"],
            purl=component_info["purl"],
            properties=PropertySet(
                missing_hash_in_file=missing_hash,
                maven_scope=component_info["scope"],
            ).to_properties(),
        )

    return [to_component(component_info) for component_info in component_infos]


def fetch_maven_source(request: Request) -> RequestOutput:
    """Resolve and fetch Maven dependencies for the given request.

    :param request: the request to process
    :return: A RequestOutput object with content for all Maven packages in the request
    """
    component_info: list[MavenComponentInfo] = []
    project_files: list[ProjectFile] = []

    maven_deps_dir = request.output_dir.join_within_root("deps", "maven")
    maven_deps_dir.path.mkdir(parents=True, exist_ok=True)

    for package in request.maven_packages:
        info = _resolve_maven(request.source_dir.join_within_root(package.path), maven_deps_dir)
        component_info.append(info["package"])

        for dependency in info["dependencies"]:
            component_info.append(dependency)

        for projectfile in info["projectfiles"]:
            project_files.append(projectfile)

    return RequestOutput.from_obj_list(
        components=_generate_component_list(component_info),
        environment_variables=[],
        project_files=project_files,
    )


def _resolve_maven(pkg_path: RootedPath, maven_deps_dir: RootedPath) -> ResolvedMavenPackage:
    """Resolve and fetch Maven dependencies for the given package.

    :param pkg_path: the path to the directory containing lockfile.json
    :param maven_deps_dir: the directory to store downloaded dependencies
    :return: a dictionary that has the following keys:
        ``package`` which is the dict representing the main Package,
        ``dependencies`` which is a list of dicts representing the package Dependencies
        ``projectfiles`` which is a list of ProjectFile objects
    :raises PackageRejected: if the Maven package is not compatible with our requirements
    """
    lockfile_path = pkg_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    if not lockfile_path.path.exists():
        raise PackageRejected(
            f"The {DEFAULT_LOCKFILE_NAME} file must be present for the maven package manager",
            solution=f"Please ensure that {DEFAULT_LOCKFILE_NAME} is present in the package directory",
        )

    original_lockfile = MavenLockfile.from_file(lockfile_path)
    lockfile = copy.deepcopy(original_lockfile)

    # Download dependencies and return download paths
    download_paths = _get_maven_dependencies(
        maven_deps_dir, lockfile.get_dependencies_to_download()
    )

    # Update lockfile.json with local paths to dependencies
    _update_maven_lockfile_with_local_paths(download_paths, lockfile)
    projectfiles = [lockfile.get_project_file()]

    return {
        "package": original_lockfile.get_main_package(),
        "dependencies": original_lockfile.get_sbom_components(),
        "projectfiles": projectfiles,
    } 