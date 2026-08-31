# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from pathlib import Path

from packageurl import PackageURL
from packaging.utils import canonicalize_name

from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import InvalidInput, NotAGitRepo, PackageRejected, UnsupportedFeature
from hermeto.core.models.input import PipBinaryFilters, PipPackageInput, Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.general import get_vcs_qualifiers
from hermeto.core.package_managers.python.pip.lockfile import (
    RequirementsLockfile,
    infer_lockfile_type,
    process_lockfiles,
    resolve_lockfile_paths,
)
from hermeto.core.package_managers.python.pip.packages import PipPackage, PipPackageInfo
from hermeto.core.package_managers.python.pip.project_files import PyProjectTOML, SetupCFG, SetupPY
from hermeto.core.package_managers.python.pip.rust import (
    filter_packages_with_rust_code,
    find_and_fetch_rust_dependencies,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import get_repo_id

log = logging.getLogger(__name__)

# Re-exported for backends (e.g. uv) that reuse pip's requirements-file download path.
DEFAULT_REQUIREMENTS_FILE = RequirementsLockfile.default_file
DEFAULT_BUILD_REQUIREMENTS_FILE = RequirementsLockfile.default_build_file


def download_from_requirement_files(
    output_dir: RootedPath,
    files: list[RootedPath],
    package_path: RootedPath,
    binary_filters: PipBinaryFilters | None = None,
    deps_dir_name: str = "pip",
) -> list[PipPackage]:
    """Download dependencies listed in the requirement files.

    Used by other backends (e.g. uv) that reuse pip's requirements-file machinery.

    :param output_dir: the root output directory for this request
    :param files: list of absolute paths to pip requirements files
    :param package_path: directory of the Hermeto package being processed
    :param binary_filters: process wheels?
    :param deps_dir_name: subdirectory of {output_dir}/deps to download into
    :return: list of PipPackage instances for each downloaded package
    :raises LockfileNotFound: If a requirement file does not exist
    """
    packages, _ = process_lockfiles(
        RequirementsLockfile,
        files,
        output_dir,
        binary_filters,
        package_path,
        deps_dir_name=deps_dir_name,
    )
    return packages


def fetch_pip_source(request: Request) -> RequestOutput:
    """Resolve and fetch pip dependencies for the given request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []
    environment_variables: list[EnvironmentVariable] = [
        EnvironmentVariable(name="PIP_FIND_LINKS", value="${output_dir}/deps/pip"),
        EnvironmentVariable(name="PIP_NO_INDEX", value="true"),
    ]
    packages_containing_rust_code = []

    for package in request.pip_packages:
        package_path = request.source_dir.join_within_root(package.path)
        info = _resolve_pip(package_path, request.output_dir, package)
        purl = _generate_purl_main_package(info, package_path)
        components.append(Component(name=info.name, version=info.version, purl=purl))

        for dep in info.requires:
            components.append(dep.to_component(build_dependency=False))
        for dep in info.build_requires:
            components.append(dep.to_component(build_dependency=True))

        project_files.extend(info.project_files)
        # each package can have Rust dependencies
        packages_containing_rust_code += info.packages_containing_rust_code

    annotations = []
    if backend_annotation := create_backend_annotation(components, "pip"):
        annotations.append(backend_annotation)
    pip_packages = RequestOutput.from_obj_list(
        components=components,
        environment_variables=environment_variables,
        project_files=project_files,
        annotations=annotations,
    )

    cargo_packages = find_and_fetch_rust_dependencies(request, packages_containing_rust_code)
    return pip_packages + cargo_packages


def _generate_purl_main_package(package: PipPackageInfo, package_path: RootedPath) -> str:
    """Get the purl for this package."""
    type = "pypi"
    name = package.name
    version = package.version
    try:
        qualifiers = get_vcs_qualifiers(package_path.root)
    except NotAGitRepo:
        if get_config().mode == Mode.PERMISSIVE:
            qualifiers = None
        else:
            raise

    if package_path.subpath_from_root != Path("."):
        subpath = package_path.subpath_from_root.as_posix()
    else:
        subpath = None

    purl = PackageURL(
        type=type,
        name=name,
        version=version,
        qualifiers=qualifiers,
        subpath=subpath,
    )

    return purl.to_string()


def _infer_package_name_from_origin_url(package_dir: RootedPath) -> str:
    try:
        repo_id = get_repo_id(package_dir.root)
    except NotAGitRepo:
        raise PackageRejected(
            reason="Unable to infer package name from origin URL",
            solution=(
                "Provide valid metadata in the package files or ensure "
                "the package files are in a git repository whose 'origin' remote has a valid URL."
            ),
        )
    except UnsupportedFeature:
        raise PackageRejected(
            reason="Unable to infer package name from origin URL",
            solution=(
                "Provide valid metadata in the package files or ensure "
                "the git repository has an 'origin' remote with a valid URL."
            ),
        )

    repo_name = Path(repo_id.parsed_origin_url.path).stem
    resolved_name = Path(repo_name).joinpath(package_dir.subpath_from_root)
    return canonicalize_name(str(resolved_name).replace("/", "-")).strip("-.")


def _extract_metadata_from_config_files(
    package_dir: RootedPath,
) -> tuple[str | None, str | None]:
    """
    Extract package name and version in the following order.

    1. pyproject.toml
    2. setup.py
    3. setup.cfg

    Note: version is optional in the SBOM, but name is required
    """
    pyproject_toml = PyProjectTOML(package_dir)
    if pyproject_toml.exists():
        log.debug("Checking pyproject.toml for metadata")
        name = pyproject_toml.get_name()
        version = pyproject_toml.get_version()

        if name:
            return name, version

    setup_py = SetupPY(package_dir)
    if setup_py.exists():
        log.debug("Checking setup.py for metadata")
        name = setup_py.get_name()
        version = setup_py.get_version()

        if name:
            return name, version

    setup_cfg = SetupCFG(package_dir)
    if setup_cfg.exists():
        log.debug("Checking setup.cfg for metadata")
        name = setup_cfg.get_name()
        version = setup_cfg.get_version()

        if name:
            return name, version

    return None, None


def _get_pip_metadata(package_dir: RootedPath) -> tuple[str, str | None]:
    """Attempt to retrieve name and version of a pip package."""
    name, version = _extract_metadata_from_config_files(package_dir)

    if not name:
        name = _infer_package_name_from_origin_url(package_dir)

    log.info("Resolved name %s for package at %s", name, package_dir)
    if version:
        log.info("Resolved version %s for package at %s", version, package_dir)
    else:
        log.warning("Could not resolve version for package at %s", package_dir)

    return name, version


def _resolve_pip(
    package_path: RootedPath,
    output_dir: RootedPath,
    package: PipPackageInput,
) -> PipPackageInfo:
    """Resolve and fetch pip dependencies for the given pip application.

    :raises PackageRejected | UnsupportedFeature: if the package is not compatible with our
        requirements/expectations
    :raises InvalidInput: if the selected lockfile type is not supported
    """
    pkg_name, pkg_version = _get_pip_metadata(package_path)

    lockfile_type = infer_lockfile_type(package.packaging_tool, package.lockfile)
    if lockfile_type is None:
        raise InvalidInput(
            "Selected lockfile type is not supported yet",
            solution="Select a supported lockfile type (e.g. 'requirements' or 'pylock').",
        )

    main_dep_paths = (
        [package.lockfile] if package.lockfile is not None else package.requirements_files
    )
    build_dep_paths = (
        package.lockfile_extras
        if package.lockfile_extras is not None
        else package.requirements_build_files
    )

    resolved_lockfiles = resolve_lockfile_paths(
        package_path, main_dep_paths, lockfile_type.default_file
    )
    resolved_extras = resolve_lockfile_paths(
        package_path, build_dep_paths, lockfile_type.default_build_file
    )

    if not resolved_lockfiles:
        log.warning("No lockfiles found, no dependencies will be fetched")
    else:
        log.info(
            "Using lockfiles: %s",
            ", ".join(str(f.subpath_from_root) for f in resolved_lockfiles),
        )

    if not resolved_extras:
        log.info("No build lockfiles found")
    else:
        log.info(
            "Using build lockfiles: %s",
            ", ".join(str(f.subpath_from_root) for f in resolved_extras),
        )

    requires, main_project_files = process_lockfiles(
        lockfile_type, resolved_lockfiles, output_dir, package.binary, package_path
    )
    build_requires, build_project_files = process_lockfiles(
        lockfile_type, resolved_extras, output_dir, package.binary, package_path
    )

    all_deps = requires + build_requires
    if get_config().pip.ignore_dependencies_crates:
        packages_containing_rust_code = []
    else:
        packages_containing_rust_code = filter_packages_with_rust_code(all_deps)

    return PipPackageInfo(
        name=pkg_name,
        version=pkg_version,
        requires=requires,
        build_requires=build_requires,
        project_files=[*main_project_files, *build_project_files],
        packages_containing_rust_code=packages_containing_rust_code,
    )
