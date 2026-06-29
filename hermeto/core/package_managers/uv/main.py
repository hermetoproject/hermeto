# SPDX-License-Identifier: GPL-3.0-only
import logging
import subprocess
from dataclasses import dataclass

from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.pip.project_files import PyProjectTOML
from hermeto.core.package_managers.uv.models import UvLock
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import run_cmd

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE_NAME = "uv.lock"


@dataclass
class UvPackageInfo:
    """Resolved uv package with everything fetch-deps produced for it."""

    name: str
    version: str | None
    components: list[Component]
    rewritten_lockfile: ProjectFile | None


def fetch_uv_source(request: Request) -> RequestOutput:
    """Resolve and fetch uv dependencies for the given request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []

    for package in request.uv_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        info = _resolve_uv(package_dir)
        components.extend(info.components)
        if info.rewritten_lockfile is not None:
            project_files.append(info.rewritten_lockfile)

    environment_variables = _generate_environment_variables()

    return RequestOutput.from_obj_list(components, environment_variables, project_files)


def _resolve_uv(package_dir: RootedPath) -> UvPackageInfo:
    pyproject = package_dir.join_within_root("pyproject.toml")
    if not pyproject.path.exists():
        raise PackageRejected(
            reason="pyproject.toml not found",
            solution="A uv project requires a pyproject.toml next to uv.lock.",
        )

    name, version = _get_pyproject_metadata(package_dir)

    _validate_lockfile(package_dir)
    log.info("uv.lock validated for %s (name=%s version=%s)", package_dir, name, version)

    lock = UvLock.from_file(package_dir)
    log.debug("Parsed %d packages from %s", len(lock.packages), DEFAULT_LOCKFILE_NAME)

    return UvPackageInfo(name=name, version=version, components=[], rewritten_lockfile=None)


def _validate_lockfile(package_dir: RootedPath) -> None:
    """`uv lock --check` validates without modifying the lockfile; non-zero on mismatch."""
    log.info("Validating uv.lock with `uv lock --check --no-cache`")
    try:
        run_cmd(["uv", "lock", "--check", "--no-cache"], params={"cwd": package_dir.path})
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip() or "uv.lock is not in sync with pyproject.toml"
        raise PackageRejected(
            reason=f"`uv lock --check` failed: {detail}",
            solution="Run `uv lock` to regenerate the lockfile (matching your uv version), then commit it.",
        ) from e


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
