# SPDX-License-Identifier: GPL-3.0-only
import asyncio
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.models.input import Request
from hermeto.core.models.output import Component, EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.property_semantics import Property, PropertyEnum
from hermeto.core.models.sbom import Annotation, create_backend_annotation
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.package_managers.maven.models import (
    MavenArtifact,
    MavenLockfile,
    parse_maven_artifacts,
)

MIRROR_ID = "hermeto-local"
# https://maven.apache.org/settings.html
SETTINGS_XML_TEMPLATE = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/SETTINGS/1.2.0 https://maven.apache.org/xsd/settings-1.2.0.xsd">
  <localRepository>${{output_dir}}/deps/maven</localRepository>
  <offline>true</offline>
  <mirrors>
    <mirror>
      <id>{MIRROR_ID}</id>
      <mirrorOf>*</mirrorOf>
      <url>file://${{output_dir}}/deps/maven</url>
    </mirror>
  </mirrors>
  <profiles>
    <profile>
      <activation>
        <activeByDefault>true</activeByDefault>
      </activation>
      <properties>
        <maven.test.skip>true</maven.test.skip>
      </properties>
    </profile>
  </profiles>
</settings>
"""


def fetch_maven_source(request: Request) -> RequestOutput:
    """Resolve and fetch Maven dependencies for the given request."""
    annotations: list[Annotation] = []
    components: list[Component] = []

    deps_dir = request.output_dir.join_within_root("deps", "maven")
    deps_dir.path.mkdir(parents=True, exist_ok=True)

    for package in request.maven_packages:
        project_dir = request.source_dir.join_within_root(package.path)
        components.extend(_resolve_maven_project(project_dir.path, deps_dir.path))

    if backend_annotation := create_backend_annotation(components, "x-maven"):
        annotations.append(backend_annotation)

    return RequestOutput.from_obj_list(
        annotations=annotations,
        components=components,
        environment_variables=[
            EnvironmentVariable(name="MAVEN_ARGS", value="-s ${output_dir}/settings.xml")
        ],
        project_files=[
            ProjectFile(
                abspath=request.output_dir.path / "settings.xml", template=SETTINGS_XML_TEMPLATE
            ),
        ],
    )


def _resolve_maven_project(project_dir: Path, deps_dir: Path) -> list[Component]:
    """Resolve and fetch Maven artifacts for the given project."""
    lockfile = MavenLockfile.from_file(project_dir / "lockfile.json")
    artifacts = parse_maven_artifacts(lockfile)
    _download_maven_artifacts(deps_dir, artifacts)

    components = _generate_sbom_components(artifacts)
    main_component = _generate_main_component(lockfile)
    return components + [main_component]


def _generate_sbom_components(artifacts: Iterable[MavenArtifact]) -> list[Component]:
    """Generate SBOM components from Maven dependencies and plugins."""
    result: list[Component] = []
    for artifact in artifacts:
        name = f"{artifact.group_id}.{artifact.artifact_id}"
        purl = PackageURL(
            type="maven",
            namespace=artifact.group_id,
            name=artifact.artifact_id,
            version=artifact.version,
        )
        component = Component(
            name=name,
            purl=purl.to_string(),
            version=artifact.version,
            properties=[Property(name=PropertyEnum.PROP_MAVEN_SCOPE, value=artifact.scope)],
        )
        result.append(component)

    return result


def _generate_main_component(lockfile: MavenLockfile) -> Component:
    """Get the main component from Maven lockfile."""
    group_id = lockfile.data["groupId"]
    artifact_id = lockfile.data["artifactId"]
    version = lockfile.data["version"]

    name = f"{group_id}.{artifact_id}"
    purl = PackageURL(type="maven", namespace=group_id, name=artifact_id, version=version)

    return Component(
        name=name,
        purl=purl.to_string(),
        version=version,
        properties=[Property(name=PropertyEnum.PROP_MAVEN_SCOPE, value="compile")],
    )


def _create_artifact_dir(deps_dir: Path, artifact: MavenArtifact) -> Path:
    """Create a directory for the Maven artifact to download."""
    artifact_dir_abs_path = deps_dir / artifact.artifact_relative_dir
    artifact_dir_abs_path.mkdir(parents=True, exist_ok=True)
    return artifact_dir_abs_path / artifact.filename


def _download_maven_artifacts(deps_dir: Path, artifacts: Iterable[MavenArtifact]) -> None:
    """Download Maven artifacts."""
    download_paths = {a.url: _create_artifact_dir(deps_dir, a) for a in artifacts}
    pom_files = _get_matching_pom_files(deps_dir, artifacts)

    async def download_all() -> None:
        config = get_config()
        concurrency_limit = config.runtime.concurrency_limit
        await async_download_files(download_paths, concurrency_limit)
        await async_download_files(pom_files, concurrency_limit)

    asyncio.run(download_all())
    _verify_checksums(artifacts, download_paths)
    _write_checksums_files(artifacts, download_paths)
    _write_remote_repositories_files(deps_dir, artifacts)


def _get_matching_pom_files(deps_dir: Path, artifacts: Iterable[MavenArtifact]) -> dict[str, Path]:
    """Get POM files to download for the Maven artifacts (JARs)."""
    pom_files: dict[str, Path] = {}

    for artifact in artifacts:
        parsed_url = urlparse(artifact.url)
        url_path = Path(parsed_url.path)

        if url_path.suffix != ".pom":
            pom_filename = f"{artifact.artifact_id}-{artifact.version}.pom"
            pom_file_url = artifact.url.replace(url_path.name, pom_filename)

            artifact_dir = deps_dir / artifact.artifact_relative_dir
            pom_files[pom_file_url] = artifact_dir / pom_filename

    return pom_files


def _verify_checksums(artifacts: Iterable[MavenArtifact], download_paths: dict[str, Path]) -> None:
    """Verify the checksums of the Maven artifacts."""
    for artifact in artifacts:
        download_path = download_paths[artifact.url]
        must_match_any_checksum(
            download_path,
            [ChecksumInfo(artifact.checksum_algorithm, artifact.checksum)],
        )


def _write_checksums_files(
    artifacts: Iterable[MavenArtifact], download_paths: dict[str, Path]
) -> None:
    """Write a checksum file for each Maven artifact."""
    for artifact in artifacts:
        download_path = download_paths[artifact.url]
        file = download_path.with_suffix(f"{download_path.suffix}.{artifact.checksum_algorithm}")
        file.write_text(artifact.checksum)


def _write_remote_repositories_files(deps_dir: Path, artifacts: Iterable[MavenArtifact]) -> None:
    """Write a _remote.repositories file for each Maven artifact."""
    for artifact in artifacts:
        artifact_dir_abs_path = deps_dir / artifact.artifact_relative_dir
        artifact_dir_abs_path.mkdir(parents=True, exist_ok=True)
        remote_repos_file = artifact_dir_abs_path.joinpath("_remote.repositories")
        remote_repos_file.write_text(f"{artifact.filename}>{MIRROR_ID}=\n")
