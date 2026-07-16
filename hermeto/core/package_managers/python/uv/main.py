# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from hermeto.core.checksum import must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import (
    LockfileNotFound,
    PackageManagerError,
    PackageRejected,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, RequestOutput
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.package_managers.python.pip.project_files import PyProjectTOML
from hermeto.core.package_managers.python.uv.models import (
    PackageArtifact,
    PackageSourceGit,
    UvLock,
    UvPackage,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball
from hermeto.core.utils import first_for, run_cmd

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE_NAME = "uv.lock"

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


def fetch_uv_source(request: Request) -> RequestOutput:
    """Resolve and fetch uv dependencies for the given request."""
    for package in request.uv_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        _resolve_uv(package_dir, request.output_dir)

    environment_variables = _generate_environment_variables()

    return RequestOutput.from_obj_list([], environment_variables)


def _resolve_uv(package_dir: RootedPath, output_dir: RootedPath) -> UvPackageResolved:
    pyproject = package_dir.join_within_root("pyproject.toml")
    if not pyproject.path.exists():
        raise PackageRejected(
            reason=(
                f"{pyproject.subpath_from_root} not found; "
                "a uv project requires one next to uv.lock"
            ),
            solution=None,
        )

    name, version = _get_pyproject_metadata(package_dir)

    _validate_lockfile(package_dir)

    lock = UvLock.from_file(package_dir)
    log.debug("Parsed %d packages from %s", len(lock.packages), DEFAULT_LOCKFILE_NAME)

    _download_dependencies(output_dir, lock)

    return UvPackageResolved(name=name, version=version)


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
                solution=None,
            ) from e

        raise PackageManagerError(
            f"`uv lock --check` failed for {lockfile.subpath_from_root} with rc={e.returncode}",
            stderr=stderr,
        ) from e


def _download_dependencies(output_dir: RootedPath, lock: UvLock) -> None:
    """Fetch every remote artifact recorded in uv.lock into a flat deps/uv directory."""
    deps_dir = output_dir.join_within_root("deps", "uv")
    deps_dir.path.mkdir(parents=True, exist_ok=True)

    items: list[_DownloadItem] = []
    files_to_download: dict[Url, Path] = {}
    for package in lock.packages:
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


def _download_git_package(
    package: UvPackage, source: PackageSourceGit, deps_dir: RootedPath
) -> None:
    """Clone a git package at its resolved commit and archive it as a tarball."""
    commit = source.commit
    tarball = deps_dir.join_within_root(f"{package.name}-gitcommit-{commit}.tar.gz")
    if tarball.path.exists():
        log.debug("%s already exists, skipping clone", tarball.path.name)
        return

    log.info("Cloning git repository for %s==%s", package.name, package.version)
    clone_as_tarball(source.clone_url, commit, to_path=tarball.path)


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
