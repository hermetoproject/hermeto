import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, RequestOutput
from hermeto.core.package_managers.maven.models import (
    MavenComponent,
    MavenLockfile,
)
from hermeto.core.package_managers.maven.utils import (
    convert_java_checksum_algorithm_to_python,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import run_cmd

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE = "lockfile.json"

EXPERIMENTAL_WARNING = ("Maven package manager is currently EXPERIMENTAL."
    " Hermeto may make breaking changes to its implementation, or remove it entirely a future release.")


def fetch_maven_source(request: Request) -> RequestOutput:
    """Resolve and fetch Maven dependencies for the given request."""

    log.warning(EXPERIMENTAL_WARNING)
    deps_dir = request.output_dir.join_within_root("deps", "maven")
    deps_dir.path.mkdir(parents=True, exist_ok=True)

    for package in request.maven_packages:
        _resolve_maven(request.source_dir.join_within_root(package.path), deps_dir)

    return RequestOutput.from_obj_list(
        components=[],
        environment_variables=[
            EnvironmentVariable(
                name="MAVEN_OPTS", value="-Dmaven.repo.local=${output_dir}/deps/maven"
            )
        ],
        project_files=[],
    )


def _resolve_maven(package_dir: RootedPath, deps_dir: RootedPath) -> list[MavenComponent]:
    """Resolve and fetch Maven dependencies for the given package."""
    lockfile_path = package_dir.join_within_root(DEFAULT_LOCKFILE)
    if not lockfile_path.path.exists():
        raise PackageRejected(
            f"The {DEFAULT_LOCKFILE} file must be present for the maven package manager",
            solution=f"Please ensure that {DEFAULT_LOCKFILE} is present in the package directory",
        )

    lockfile = MavenLockfile.from_file(lockfile_path)
    dependencies = lockfile.get_dependencies_to_download()
    plugins = lockfile.get_plugins_to_download()

    _download_maven_artifacts(package_dir, deps_dir.path, dependencies, plugins)
    # TODO: Return SBOM components
    return []


def _download_maven_artifacts(
    package_dir: RootedPath,
    deps_dir: Path,
    dependencies: dict[str, dict[str, Any]],
    plugins: dict[str, dict[str, Any]],
) -> None:
    """Download Maven dependencies using mvn dependency:get."""
    maven_stuff = {**dependencies, **plugins}

    # Download each artifact using Maven dependency plugin
    for url, dependency in maven_stuff.items():
        group_id = dependency["group_id"]
        artifact_id = dependency["artifact_id"]
        version = dependency["version"]
        # Optional: classifier, default empty
        classifier = dependency.get("classifier", "")
        # Optional: artifact type, default "jar"
        artifact_type = dependency.get("artifact_type", "jar")

        # Build artifact coordinate string
        artifact_coord = f"{group_id}:{artifact_id}:{version}:{classifier}:{artifact_type}"

        log.info("Downloading Maven artifact %s", artifact_coord)

        # Execute mvn dependency:get
        cmd = [
            "mvn",
            "org.apache.maven.plugins:maven-dependency-plugin:3.9.0:get",
            f"-DgroupId={group_id}",
            f"-DartifactId={artifact_id}",
            f"-Dversion={version}",
            f"-Dmaven.repo.local={deps_dir}",
            "-Dtransitive=false",
        ]

        if classifier:
            cmd.append(f"-Dclassifier={classifier}")

        if artifact_type != "jar":
            cmd.append(f"-Dpackaging={artifact_type}")

        run_cmd(cmd, {"cwd": package_dir.path})

    # Save checksums from lockfile data
    _save_checksums(maven_stuff, deps_dir)


def _save_checksums(
    artifacts: dict[str, dict[str, Any]],
    deps_dir: Path,
) -> None:
    """Save checksum files from lockfile data to Maven repository layout."""
    for url, dependency in artifacts.items():
        if not dependency.get("checksum") or not dependency.get("checksum_algorithm"):
            continue

        group_id = dependency["group_id"]
        artifact_id = dependency["artifact_id"]
        version = dependency["version"]

        # Calculate Maven repository layout path
        group_dir = group_id.replace(".", "/")
        artifact_dir = deps_dir / group_dir / artifact_id / version

        # Determine artifact filename from URL
        parsed_url = urlparse(url)
        url_path = Path(parsed_url.path)
        filename = url_path.name

        # Maven stores artifacts as artifact-version.ext
        # If the filename doesn't match, construct it from coordinates
        if not filename.startswith(f"{artifact_id}-{version}"):
            # Extract extension from URL or default to .jar
            ext = url_path.suffix or ".jar"
            filename = f"{artifact_id}-{version}{ext}"

        artifact_path = artifact_dir / filename

        # Save checksum file
        algorithm = convert_java_checksum_algorithm_to_python(dependency["checksum_algorithm"])
        checksum_file_path = artifact_path.with_suffix(f"{artifact_path.suffix}.{algorithm}")
        checksum_file_path.write_text(dependency["checksum"])
