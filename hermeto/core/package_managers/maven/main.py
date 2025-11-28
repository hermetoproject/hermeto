# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, RequestOutput
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.package_managers.maven.models import (
    MavenComponent,
    MavenLockfile,
)
from hermeto.core.package_managers.maven.utils import (
    convert_java_checksum_algorithm_to_python,
    derive_pom_filename,
    derive_repository_id,
)
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE = "lockfile.json"


def fetch_maven_source(request: Request) -> RequestOutput:
    """Resolve and fetch Maven dependencies for the given request."""
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

    _download_maven_artifacts(deps_dir, dependencies, plugins)
    # TODO: Return SBOM components
    return []


def _download_maven_artifacts(
    deps_dir: RootedPath,
    dependencies: dict[str, dict[str, Any]],
    plugins: dict[str, dict[str, Any]],
) -> None:
    """Download Maven dependencies."""
    maven_stuff = {**dependencies, **plugins}

    download_paths, artifacts = _prepare_artifact_downloads(maven_stuff, deps_dir)
    pom_files, pom_checksums = _prepare_pom_and_checksum_downloads(maven_stuff, download_paths)

    asyncio.run(async_download_files(download_paths | pom_files, 10))  # type: ignore
    asyncio.run(_async_download_optional_files(pom_checksums))

    _verify_and_save_checksums(maven_stuff, download_paths)
    _create_remote_repositories_files(artifacts)


def _prepare_artifact_downloads(
    to_download: dict[str, dict[str, Any]],
    deps_dir: RootedPath,
) -> tuple[dict[str, Path], dict[Path, list[dict[str, str]]]]:
    """Prepare artifact download paths and track artifacts for _remote.repositories files."""
    download_paths: dict[str, Path] = {}
    download_artifacts: dict[Path, list[dict[str, str]]] = defaultdict(list)

    for url, dependency in to_download.items():
        group_id: str = dependency["group_id"]
        group_dir: str = group_id.replace(".", "/")
        artifact_id: str = dependency["artifact_id"]
        version: str = dependency["version"]
        filename = Path(urlparse(url).path).name

        local_path = deps_dir.join_within_root(group_dir, artifact_id, version, filename).path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        download_paths[url] = local_path

        artifact_dir = local_path.parent

        repository_id = derive_repository_id(url)
        download_artifacts[artifact_dir].append(
            {
                "filename": filename,
                "repository_id": repository_id,
                "url": url,
            }
        )

        if not Path(filename).suffix == ".pom":
            pom_filename = derive_pom_filename(artifact_id, version)
            download_artifacts[artifact_dir].append(
                {
                    "filename": pom_filename,
                    "repository_id": repository_id,
                    "url": url.replace(filename, pom_filename),
                }
            )

    return download_paths, download_artifacts


def _prepare_pom_and_checksum_downloads(
    to_download: dict[str, dict[str, Any]],
    download_paths: dict[str, Path],
) -> tuple[dict[str, Path], dict[str, Path]]:
    """Prepare POM files and checksum files to download."""
    pom_files: dict[str, Path] = {}
    pom_checksums: dict[str, Path] = {}

    for url, dep_info in to_download.items():
        parsed_url = urlparse(url)
        url_path = Path(parsed_url.path)

        if not url_path.suffix == ".pom":
            pom_filename = derive_pom_filename(dep_info["artifact_id"], dep_info["version"])
            pom_file_url = url.replace(url_path.name, pom_filename)

            artifact_dir = download_paths[url].parent
            pom_files[pom_file_url] = artifact_dir / pom_filename

            algorithm = convert_java_checksum_algorithm_to_python(dep_info["checksum_algorithm"])
            pom_checksum_url = f"{pom_file_url}.{algorithm}"
            pom_checksum_path = artifact_dir / f"{pom_filename}.{algorithm}"
            pom_checksums[pom_checksum_url] = pom_checksum_path

    return pom_files, pom_checksums


async def _download_optional_file(session: aiohttp.ClientSession, url: str, path: Path) -> None:
    """Download an optional file."""
    suffixes = (".sha1", ".md5", ".sha256", ".sha512", ".sha224", ".sha384")
    async with session.get(url, raise_for_status=False) as response:
        if response.status == 404:
            log.debug("Skipping %s (404)", url)
            return

        content = await response.read()
        if path.suffix in suffixes:
            path.write_text(content.decode().strip())
        else:
            path.write_bytes(content)


async def _async_download_optional_files(files: dict[str, Path]) -> None:
    """Download optional files."""
    async with aiohttp.ClientSession(trust_env=True) as session:
        tasks = [_download_optional_file(session, url, path) for url, path in files.items()]
        await asyncio.gather(*tasks, return_exceptions=True)


def _verify_and_save_checksums(
    artifacts: dict[str, dict[str, Any]],
    download_paths: dict[str, Path],
) -> None:
    """Verify checksums and save checksum files."""
    for url, dependency in artifacts.items():
        algorithm = convert_java_checksum_algorithm_to_python(dependency["checksum_algorithm"])
        artifact_path = download_paths[url]
        checksum_file_path = artifact_path.with_suffix(f"{artifact_path.suffix}.{algorithm}")
        checksum_file_path.write_text(dependency["checksum"])


def _create_remote_repositories_files(artifacts: dict[Path, list[dict[str, str]]]) -> None:
    """Create _remote.repositories files for each artifact directory."""
    now = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S %Z %Y")
    for artifact_dir, items in artifacts.items():
        remote_repos_file = artifact_dir.joinpath("_remote.repositories")
        lines = [
            "#NOTE: This is a Maven Resolver internal implementation file, its format can be changed without prior notice.\n",
            f"#{now}\n",
        ]
        lines.extend(f"{item['filename']}>{item['repository_id']}=\n" for item in items)
        remote_repos_file.write_text("".join(lines))
