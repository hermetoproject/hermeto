# SPDX-License-Identifier: GPL-3.0-only
import copy
import logging

from hermeto.core.errors import LockfileNotFound, PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import ProjectFile, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.npm.fetch import _get_npm_dependencies
from hermeto.core.package_managers.npm.package_lock import (
    NpmComponentInfo,
    PackageLock,
    ResolvedNpmPackage,
)
from hermeto.core.package_managers.npm.project_files import (
    _update_package_json_files,
    _update_package_lock_with_local_paths,
)
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


def _generate_component_list(component_infos: list[NpmComponentInfo]) -> list[Component]:
    """Convert a list of NpmComponentInfo objects into a list of Component objects for the SBOM."""

    def to_component(component_info: NpmComponentInfo) -> Component:
        if component_info["missing_hash_in_file"]:
            missing_hash = frozenset({str(component_info["missing_hash_in_file"])})
        else:
            missing_hash = frozenset()

        return Component(
            name=component_info["name"],
            version=component_info["version"],
            purl=component_info["purl"],
            properties=PropertySet(
                npm_bundled=component_info["bundled"],
                npm_development=component_info["dev"],
                missing_hash_in_file=missing_hash,
            ).to_properties(),
            external_references=component_info["external_refs"],
        )

    return [to_component(component_info) for component_info in component_infos]


def fetch_npm_source(request: Request) -> RequestOutput:
    """Resolve and fetch npm dependencies for the given request.

    :param request: the request to process
    :return: A RequestOutput object with content for all npm packages in the request
    """
    component_info: list[NpmComponentInfo] = []
    project_files: list[ProjectFile] = []

    npm_deps_dir = request.output_dir.join_within_root("deps", "npm")
    npm_deps_dir.path.mkdir(parents=True, exist_ok=True)

    for package in request.npm_packages:
        info = _resolve_npm(request.source_dir.join_within_root(package.path), npm_deps_dir)
        component_info.append(info["package"])

        for dependency in info["dependencies"]:
            component_info.append(dependency)

        for projectfile in info["projectfiles"]:
            project_files.append(projectfile)

    components = _generate_component_list(component_info)
    annotations = []
    if backend_annotation := create_backend_annotation(components, "npm"):
        annotations.append(backend_annotation)
    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=[],
        project_files=project_files,
        annotations=annotations,
    )


def _resolve_npm(pkg_path: RootedPath, npm_deps_dir: RootedPath) -> ResolvedNpmPackage:
    """Resolve and fetch npm dependencies for the given package.

    :param pkg_path: the path to the directory containing npm-shrinkwrap.json or package-lock.json
    :return: a dictionary that has the following keys:
        ``package`` which is the dict representing the main Package,
        ``dependencies`` which is a list of dicts representing the package Dependencies
        ``package_lock_file`` which is the (updated) package-lock.json as a ProjectFile
    :raises PackageRejected: if the npm package is not compatible with our requirements
    """
    # npm-shrinkwrap.json and package-lock.json share the same format but serve slightly
    # different purposes. See the following documentation for more information:
    # https://docs.npmjs.com/files/package-lock.json.
    for lock_file in ("npm-shrinkwrap.json", "package-lock.json"):
        package_lock_path = pkg_path.join_within_root(lock_file)
        if package_lock_path.path.exists():
            break
    else:
        raise LockfileNotFound(
            files=package_lock_path.path,
            solution=(
                "Please double-check that you have npm-shrinkwrap.json or package-lock.json "
                "checked in to the repository, or the supplied lockfile path is correct."
            ),
        )

    node_modules_path = pkg_path.join_within_root("node_modules")
    if node_modules_path.path.exists():
        raise PackageRejected(
            "The 'node_modules' directory cannot be present in the source repository",
            solution="Ensure that there are no 'node_modules' directories in your repo",
        )

    original_package_lock = PackageLock.from_file(package_lock_path)
    package_lock = copy.deepcopy(original_package_lock)

    # Download dependencies via resolved URLs and return download_paths for updating
    # package-lock.json with local file paths
    download_paths = _get_npm_dependencies(
        npm_deps_dir, package_lock.get_dependencies_to_download()
    )

    # Update package-lock.json, package.json(s) files with local paths to dependencies and store them as ProjectFiles
    _update_package_lock_with_local_paths(download_paths, package_lock)
    projectfiles = _update_package_json_files(package_lock.workspaces, pkg_path)
    projectfiles.append(package_lock.get_project_file())

    return {
        "package": original_package_lock.get_main_package(),
        "dependencies": original_package_lock.get_sbom_components(),
        "projectfiles": projectfiles,
    }
