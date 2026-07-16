# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import tomlkit
from typing_extensions import assert_never

from hermeto.core.checksum import must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import (
    LockfileNotFound,
    PackageManagerError,
    PackageRejected,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.sbom import Annotation, Component, create_backend_annotation
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.package_managers.python.pip.project_files import PyProjectTOML
from hermeto.core.package_managers.python.uv.build_deps import (
    download_build_dependencies,
    to_component,
)
from hermeto.core.package_managers.python.uv.models import (
    _WHEEL_LOCATIONS,
    PackageArtifact,
    PackageSourceGit,
    PackageSourceLocal,
    PackageSourceRegistry,
    PackageSourceUrl,
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
    artifact: PackageArtifact
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
            reason=(
                f"{pyproject.subpath_from_root} not found; "
                "a uv project requires one next to uv.lock"
            ),
        )

    name, version = _get_pyproject_metadata(package_dir)

    _validate_lockfile(package_dir)

    lockfile_doc = load_lockfile_document(package_dir)
    lock = UvLock.from_toml(lockfile_doc, package_dir.join_within_root(DEFAULT_LOCKFILE_NAME).path)
    log.debug("Parsed %d packages from %s", len(lock.packages), DEFAULT_LOCKFILE_NAME)

    _download_dependencies(output_dir, lock.packages)

    build_deps = download_build_dependencies(package_dir, output_dir)
    components = [to_component(dep) for dep in build_deps]

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
        # uv resolves an interpreter for requires-python before it checks anything and
        # downloads one when none matches; --no-python-downloads keeps it from running
        # a runtime hermeto never vetted
        run_cmd(
            ["uv", "lock", "--check", "--no-cache", "--no-python-downloads"],
            params={"cwd": package_dir.path},
            suppress_errors=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""

        # uv gives rc=2 to a missing interpreter, unreadable input and a network failure
        # alike, so its message is the only way to pick out the one hermeto tolerates.
        # Matching stderr is not robust, but uv offers nothing better.
        if "No interpreter found" in stderr:
            # no interpreter in hermeto's image satisfies requires-python. Nothing the
            # user can act on, and the prefetch needs none of its own, so it goes on
            # with the lockfile unverified.
            detail: str = first_for(
                lambda line: line.startswith("error:"),
                stderr.splitlines(),
                "no Python interpreter satisfies requires-python",
            ).removeprefix("error: ")
            log.warning("Skipped validating %s: %s", lockfile.subpath_from_root, detail)
            return

        if stderr:
            log.error("`uv lock --check` stderr:\n%s", stderr.rstrip())

        # uv exits 1 once the check ran and rejected the lockfile, whether it is stale or
        # pyproject.toml cannot be resolved at all. Either way it is not fit to prefetch.
        if e.returncode == 1:
            raise PackageRejected(
                reason=f"`uv lock --check` rejected {lockfile.subpath_from_root}",
            ) from e

        raise PackageManagerError(
            f"`uv lock --check` failed for {lockfile.subpath_from_root} with rc={e.returncode}",
            stderr=stderr,
        ) from e


def _download_dependencies(output_dir: RootedPath, packages: list[UvPackage]) -> None:
    """Fetch every remote artifact recorded in uv.lock into a flat deps/uv directory."""
    deps_dir = output_dir.join_within_root("deps", "uv")
    deps_dir.path.mkdir(parents=True, exist_ok=True)

    items: list[_DownloadItem] = []
    files_to_download: dict[Url, Path] = {}
    for package in packages:
        if isinstance(package.source, PackageSourceGit):
            _download_git_package(package, package.source, deps_dir)
        else:
            # everything else lands here: registry, url and the four local kinds
            for artifact in package.artifacts_to_download:
                if artifact.url is None:
                    # should not happen
                    raise RuntimeError(f"artifact for {package.name} has no download URL")
                target = deps_dir.join_within_root(
                    artifact.get_target_filename(package.source)
                ).path
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


def _is_local_package(package: UvPackage) -> bool:
    """Whether the package's files are already in the project tree, so nothing is fetched."""
    return isinstance(package.source, PackageSourceLocal)


def _git_tarball_filename(package: UvPackage, source: PackageSourceGit) -> str:
    """Name of the tarball under deps/uv for a git package.

    The download and lockfile-rewrite phases must agree on this name.
    """
    return f"{package.name}-gitcommit-{source.commit}.tar.gz"


def _download_git_package(
    package: UvPackage, source: PackageSourceGit, deps_dir: RootedPath
) -> None:
    """Clone a git package at its resolved commit and archive it as a tarball."""
    commit = source.commit
    tarball = deps_dir.join_within_root(_git_tarball_filename(package, source))
    if tarball.path.exists():
        log.debug("%s already exists, skipping clone", tarball.path.name)
        return

    log.info("Cloning git repository for %s==%s", package.name, package.version)
    clone_as_tarball(source.clone_url, commit, to_path=tarball.path)


def _replace_source_with_path(raw_package: Any, filename: str) -> None:
    """Swap a remote source for the local path it was downloaded to."""
    # tomlkit.inline_table() would write `source = {path = "..."}`; uv writes
    # `source = { registry = "..." }`. Parsing a snippet keeps uv's brace padding,
    # so rewritten entries do not stand out from the ones left untouched.
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
    if all(_is_local_package(package) for package in lock.packages):
        return None

    for raw_package, package in zip(doc.get("package", []), lock.packages, strict=True):
        if (raw_package["name"], raw_package.get("version")) != (package.name, package.version):
            # should not happen: both views parse the same file, in order
            raise RuntimeError(
                f"uv.lock entry {raw_package['name']} does not match the parsed {package.name}"
            )

        match package.source:
            case PackageSourceRegistry():
                if package.sdist is not None:
                    filename = package.sdist.get_target_filename(package.source)
                    raw_package["sdist"]["url"] = f"file://{_TEMPLATED_DEPS_DIR}/{filename}"
                raw_wheels = raw_package.get("wheels", [])
                for raw_wheel, wheel in zip(raw_wheels, package.wheels, strict=True):
                    if any(raw_wheel.get(key) != getattr(wheel, key) for key in _WHEEL_LOCATIONS):
                        # should not happen: as above, one file parsed two ways
                        raise RuntimeError(
                            f"uv.lock wheel for {package.name} does not match the parsed entry"
                        )

                    filename = wheel.get_target_filename(package.source)
                    raw_wheel["url"] = f"file://{_TEMPLATED_DEPS_DIR}/{filename}"
            case PackageSourceGit() as source:
                _replace_source_with_path(raw_package, _git_tarball_filename(package, source))
            case PackageSourceUrl():
                recorded = package.sole_artifact
                if recorded is None:
                    # should not happen: artifacts_to_download rejects such a package
                    raise RuntimeError(f"{package.name} records no sdist and no wheels in uv.lock")
                _replace_source_with_path(raw_package, recorded.get_target_filename(package.source))
            case PackageSourceLocal():
                # nothing was fetched for these, so the entries already point at the files
                pass
            case _:
                assert_never(package.source)

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
