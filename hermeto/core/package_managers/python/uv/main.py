# SPDX-License-Identifier: GPL-3.0-only
import logging
from dataclasses import dataclass

from hermeto.core.errors import (
    PackageRejected,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import Component, EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.package_managers.python.pip.project_files import PyProjectTOML
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


@dataclass
class UvResolutionResult:
    """Everything fetch-deps produced for a single uv project."""

    name: str
    version: str | None


def fetch_uv_source(request: Request) -> RequestOutput:
    """Resolve and fetch uv dependencies for the given request."""
    components: list[Component] = []
    environment_variables: list[EnvironmentVariable] = []
    project_files: list[ProjectFile] = []

    for package in request.uv_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        _resolve_uv(package_dir)

    return RequestOutput.from_obj_list(components, environment_variables, project_files)


def _resolve_uv(package_dir: RootedPath) -> UvResolutionResult:
    pyproject = package_dir.join_within_root("pyproject.toml")
    if not pyproject.path.exists():
        raise PackageRejected(
            reason=(
                f"{pyproject.subpath_from_root} not found; "
                "a uv project requires one next to uv.lock"
            ),
        )

    name, version = _get_pyproject_metadata(package_dir)

    return UvResolutionResult(name=name, version=version)


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
