# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import tomlkit

from hermeto.core.checksum import must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import (
    LockfileNotFound,
    MissingChecksum,
    PackageRejected,
    UnexpectedFormat,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.sbom import Annotation, Component, create_backend_annotation
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.package_managers.python.pip.project_files import PyProjectTOML
from hermeto.core.package_managers.python.uv.build_requirements import download_build_requirements
from hermeto.core.package_managers.python.uv.models import (
    UvArtifact,
    UvLock,
    UvPackage,
    load_lockfile_document,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball
from hermeto.core.utils import first_for, run_cmd

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE_NAME = "uv.lock"

# string.Template placeholder resolved by ProjectFile.resolve_content at inject-files time
_TEMPLATED_DEPS_DIR = "${output_dir}/deps/uv"

Url = str


class _DownloadItem(NamedTuple):
    package: UvPackage
    artifact: UvArtifact
    target: Path


@dataclass
class UvPackageResolved:
    """Resolved uv package with everything fetch-deps produced for it."""

    name: str
    version: str | None
    components: list[Component]
    rewritten_lockfile: ProjectFile | None


def fetch_uv_source(request: Request) -> RequestOutput:
    """Resolve and fetch uv dependencies for the given request."""
    annotations: list[Annotation] = []
    components: list[Component] = []
    project_files: list[ProjectFile] = []

    for package in request.uv_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        resolution_result = _resolve_uv(package_dir, request.output_dir)
        components.extend(resolution_result.components)
        if resolution_result.rewritten_lockfile is not None:
            project_files.append(resolution_result.rewritten_lockfile)

    if backend_annotation := create_backend_annotation(components, "x-uv"):
        annotations.append(backend_annotation)

    environment_variables = _generate_environment_variables()

    return RequestOutput.from_obj_list(
        components, environment_variables, project_files, annotations=annotations
    )


def _resolve_uv(package_dir: RootedPath, output_dir: RootedPath) -> UvPackageResolved:
    pyproject = package_dir.join_within_root("pyproject.toml")
    if not pyproject.path.exists():
        raise PackageRejected(
            reason="pyproject.toml not found",
            solution="A uv project requires a pyproject.toml next to uv.lock.",
        )

    name, version = _get_pyproject_metadata(package_dir)

    _validate_lockfile(package_dir)

    lockfile_doc = load_lockfile_document(package_dir)
    lock = UvLock.from_toml(lockfile_doc, package_dir.join_within_root(DEFAULT_LOCKFILE_NAME).path)
    log.debug("Parsed %d packages from %s", len(lock.packages), DEFAULT_LOCKFILE_NAME)

    _download_dependencies(output_dir, lock)

    build_requires = download_build_requirements(package_dir, output_dir)
    components = [dep.to_component(build_dependency=True, backend="uv") for dep in build_requires]

    return UvPackageResolved(
        name=name,
        version=version,
        components=components,
        rewritten_lockfile=_rewrite_lockfile(lockfile_doc, package_dir, lock),
    )


def _validate_lockfile(package_dir: RootedPath) -> None:
    """`uv lock --check` validates without modifying the lockfile; non-zero on mismatch."""
    lockfile = package_dir.join_within_root(DEFAULT_LOCKFILE_NAME)
    if not lockfile.path.exists():
        raise LockfileNotFound(
            files=lockfile.path,
            solution="Run `uv lock` in the project directory to generate uv.lock.",
        )

    log.info("Validating %s", lockfile.subpath_from_root)
    try:
        run_cmd(["uv", "lock", "--check", "--no-cache"], params={"cwd": package_dir.path})
    except subprocess.CalledProcessError as e:
        detail: str = first_for(
            lambda line: line.startswith("error:"),
            (e.stderr or "").splitlines(),
            "uv.lock is not in sync with pyproject.toml",
        )
        raise PackageRejected(
            reason=f"`uv lock --check` failed for {lockfile.subpath_from_root}: {detail}",
            solution="Regenerate the lockfile with `uv lock`",
        ) from e


def _download_dependencies(output_dir: RootedPath, lock: UvLock) -> None:
    """Fetch every remote artifact recorded in uv.lock into a flat deps/uv directory."""
    deps_dir = output_dir.join_within_root("deps", "uv")
    deps_dir.path.mkdir(parents=True, exist_ok=True)

    items: list[_DownloadItem] = []
    files_to_download: dict[Url, Path] = {}
    for package in lock.packages:
        if package.source.kind == "git":
            _download_git_package(package, deps_dir)
            continue
        for artifact in _artifacts_to_download(package):
            if artifact.url is None:
                # should not happen
                raise RuntimeError(f"artifact for {package.name} has no download URL")
            target = deps_dir.join_within_root(artifact.get_target_filename(package.source)).path
            items.append(_DownloadItem(package, artifact, target))
            if not target.exists():
                files_to_download[artifact.url] = target

    if files_to_download:
        log.info("Downloading %d artifacts from %s", len(files_to_download), DEFAULT_LOCKFILE_NAME)
        asyncio.run(async_download_files(files_to_download, get_config().runtime.concurrency_limit))

    for package, artifact, target in items:
        if artifact.checksum_info is not None:
            must_match_any_checksum(target, [artifact.checksum_info])
        else:
            log.warning("Missing checksum for %s==%s", package.name, package.version)


def _artifacts_to_download(package: UvPackage) -> list[UvArtifact]:
    """Return the remote artifacts to fetch for a package.

    Only sdists are fetched for now. Like pip's process_package_distributions,
    this is the single place where binary filters will decide the sdist/wheel
    split once they are supported. Local sources need no fetching and git
    sources are cloned rather than downloaded, so both yield nothing here.
    """
    if package.source.is_local:
        return []

    if package.source.kind == "registry":
        # registry checksums are optional in uv.lock; a missing hash is
        # tolerated here and reported by the download phase
        if package.sdist is None:
            # wheel-only packages lock fine and pass `uv lock --check`, but
            # cannot be built from source under UV_NO_BINARY=true
            raise PackageRejected(
                reason=(
                    f"{package.name}=={package.version} has no sdist in uv.lock; "
                    "the package likely only publishes wheels"
                ),
                solution=None,
            )
        if package.sdist.url is None:
            # uv always records a URL for registry sdists; `uv lock --check`
            # does not catch its absence, but `uv sync` would fail on it
            raise UnexpectedFormat(
                f"registry sdist for {package.name}=={package.version} has no URL in uv.lock",
                solution="The lockfile looks corrupted. Regenerate it with `uv lock`.",
            )
        return [package.sdist]

    if package.source.kind == "url":
        # a url source points at a single file, so uv records exactly one
        # distribution for it: the sdist, or a single wheel. Its hash is
        # mandatory, but the download URL lives only in the source itself.
        recorded = package.sole_artifact
        if recorded is None or recorded.hash is None:
            raise MissingChecksum(
                f"{package.name}=={package.version}",
                solution=(
                    "uv requires a hash for URL dependencies, so this lockfile looks "
                    "corrupted. Regenerate it with `uv lock`."
                ),
            )
        # copying rather than rebuilding keeps the sdist/wheel type, which the
        # SBOM reads back to tell a source build from a binary one
        return [recorded.model_copy(update={"url": package.source.location})]

    return []


def _git_tarball_filename(package: UvPackage) -> str:
    """Name of the tarball under deps/uv for a git package.

    The download and lockfile-rewrite phases must agree on this name.
    """
    return f"{package.name}-gitcommit-{package.source.get_git_commit()}.tar.gz"


def _download_git_package(package: UvPackage, deps_dir: RootedPath) -> None:
    """Clone a git package at its resolved commit and archive it as a tarball."""
    commit = package.source.get_git_commit()
    tarball = deps_dir.join_within_root(_git_tarball_filename(package))
    if tarball.path.exists():
        log.debug("%s already exists, skipping clone", tarball.path.name)
        return

    log.info("Cloning git repository for %s==%s", package.name, package.version)
    clone_as_tarball(package.source.git_clone_url, commit, to_path=tarball.path)


def _replace_source_with_path(raw_package: Any, filename: str) -> None:
    """Swap a remote source for the local path it was downloaded to."""
    # built from a snippet rather than tomlkit.inline_table() to match the
    # brace padding style uv itself writes, e.g. `{ registry = "..." }`
    source = tomlkit.value('{ path = "" }')
    source["path"] = f"{_TEMPLATED_DEPS_DIR}/{filename}"
    raw_package["source"] = source


def _rewrite_lockfile(
    doc: tomlkit.TOMLDocument, package_dir: RootedPath, lock: UvLock
) -> ProjectFile | None:
    """Redirect every remote artifact reference in uv.lock into deps/uv.

    `uv sync` fetches the URLs recorded in uv.lock verbatim and cannot fall
    back to UV_FIND_LINKS, so the lockfile itself must point at the prefetched
    artifacts. The rewrite edits the raw TOML document in place (callers must
    not reuse it): the UvLock model is deliberately lossy, while tomlkit
    preserves every field and the original formatting.

    Returns None if the lockfile references no remote artifacts.
    """
    if all(package.source.is_local for package in lock.packages):
        return None

    # both views parse the same file, so the entries are index-aligned
    for raw_package, package in zip(doc.get("package", []), lock.packages, strict=True):
        if package.source.is_local:
            # nothing was fetched for it, so the entry already points at the file
            continue
        if package.source.kind == "registry":
            if package.sdist is not None:
                filename = package.sdist.get_target_filename(package.source)
                raw_package["sdist"]["url"] = f"file://{_TEMPLATED_DEPS_DIR}/{filename}"
            raw_wheels = raw_package.get("wheels", [])
            for raw_wheel, wheel in zip(raw_wheels, package.wheels, strict=True):
                filename = wheel.get_target_filename(package.source)
                raw_wheel["url"] = f"file://{_TEMPLATED_DEPS_DIR}/{filename}"
        elif package.source.kind == "git":
            _replace_source_with_path(raw_package, _git_tarball_filename(package))
        elif package.source.kind == "url":
            recorded = package.sole_artifact
            if recorded is None:
                # should not happen: _artifacts_to_download rejects such a package
                raise RuntimeError(f"{package.name} records no sdist and no wheels in uv.lock")
            _replace_source_with_path(raw_package, recorded.get_target_filename(package.source))

    lockfile_path = package_dir.join_within_root(DEFAULT_LOCKFILE_NAME)
    return ProjectFile(abspath=lockfile_path.path, template=tomlkit.dumps(doc))


def _get_pyproject_metadata(package_dir: RootedPath) -> tuple[str, str | None]:
    """Read the project's name/version from pyproject.toml's [project] table."""
    pyproject = PyProjectTOML(package_dir)

    name = pyproject.get_name()
    if not name:
        raise PackageRejected(
            reason="pyproject.toml does not declare a project name",
            solution="Add a [project] table with a `name` field to pyproject.toml.",
        )

    version = pyproject.get_version()
    if version is None:
        log.warning("Could not resolve version from pyproject.toml at %s", package_dir)

    return name, version


def _generate_environment_variables() -> list[EnvironmentVariable]:
    return [
        EnvironmentVariable(name="UV_OFFLINE", value="true"),
        EnvironmentVariable(name="UV_FIND_LINKS", value="${output_dir}/deps/uv"),
        EnvironmentVariable(name="UV_FROZEN", value="true"),
        EnvironmentVariable(name="UV_NO_BINARY", value="true"),
    ]
