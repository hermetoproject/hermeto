# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from pathlib import Path

from packageurl import PackageURL
from packaging.utils import canonicalize_name

from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, PackageRejected, UnsupportedFeature
from hermeto.core.models.input import PipBinaryFilters, Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.general import get_vcs_qualifiers
from hermeto.core.package_managers.python.packaging_tool import PythonPackagingTool
from hermeto.core.package_managers.python.pip.lockfile import (
    _LOCKFILE_TYPES,
    _download_lockfiles,
    _infer_packaging_tool,
    _resolve_lockfile_paths,
)
from hermeto.core.package_managers.python.pip.packages import PipPackageInfo
from hermeto.core.package_managers.python.pip.project_files import PyProjectTOML, SetupCFG, SetupPY
from hermeto.core.package_managers.python.pip.requirements import (
    PipRequirement,
    PipRequirementsFile,
    get_external_requirement_filepath,
)
from hermeto.core.package_managers.python.pip.rust import (
    filter_packages_with_rust_code,
    find_and_fetch_rust_dependencies,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import get_repo_id

log = logging.getLogger(__name__)


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
        info = _resolve_pip(
            package_path,
            request.output_dir,
            package.requirements_files,
            package.requirements_build_files,
            package.lockfile,
            package.lockfile_extras,
            package.packaging_tool,
            package.binary,
        )
        purl = _generate_purl_main_package(info, package_path)
        components.append(Component(name=info.name, version=info.version, purl=purl))

        for dep in info.requires:
            components.append(dep.to_component(build_dependency=False))
        for dep in info.build_requires:
            components.append(dep.to_component(build_dependency=True))

        replaced_requirements_files = map(_replace_external_requirements, info.requirements)
        project_files.extend(filter(None, replaced_requirements_files))
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
    requirement_files: list[Path] | None = None,
    build_requirement_files: list[Path] | None = None,
    lockfile: Path | None = None,
    lockfile_extras: list[Path] | None = None,
    packaging_tool: PythonPackagingTool | None = None,
    binary_filters: PipBinaryFilters | None = None,
) -> PipPackageInfo:
    """Resolve and fetch pip dependencies for the given pip application.

    :raises PackageRejected | UnsupportedFeature: if the package is not compatible with our
        requirements/expectations
    """
    pkg_name, pkg_version = _get_pip_metadata(package_path)

    tool = _infer_packaging_tool(packaging_tool, lockfile)
    if tool == PythonPackagingTool.PYLOCK:
        raise NotImplementedError("pylock is not supported yet")

    lockfile_type = _LOCKFILE_TYPES[tool]

    if lockfile is not None:
        resolved_lockfiles = _resolve_lockfile_paths(
            package_path, [lockfile], lockfile_type.default_file
        )
        resolved_extras = _resolve_lockfile_paths(
            package_path, lockfile_extras, lockfile_type.default_build_file
        )
    else:
        resolved_lockfiles = _resolve_lockfile_paths(
            package_path, requirement_files, lockfile_type.default_file
        )
        resolved_extras = _resolve_lockfile_paths(
            package_path, build_requirement_files, lockfile_type.default_build_file
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

    requires = _download_lockfiles(lockfile_type, resolved_lockfiles, output_dir, binary_filters)
    build_requires = _download_lockfiles(lockfile_type, resolved_extras, output_dir, binary_filters)

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
        requirements=[*resolved_lockfiles, *resolved_extras],
        packages_containing_rust_code=packages_containing_rust_code,
    )


def _get_external_requirement_filepath(requirement: PipRequirement) -> Path:
    """Get the relative path under deps/pip/ where a URL or VCS requirement should be placed."""
    digest = requirement.hashes[0].partition(":")[2] if requirement.hashes else ""
    return get_external_requirement_filepath(
        requirement.kind, requirement.direct_access_url, requirement.package, digest
    )


def _replace_external_requirements(requirements_file_path: RootedPath) -> ProjectFile | None:
    """Generate an updated requirements file.

    Replace the urls of external dependencies with file paths (templated).
    If no updates are needed, return None.
    """
    requirements_file = PipRequirementsFile(requirements_file_path)

    def maybe_replace(requirement: PipRequirement) -> PipRequirement | None:
        if requirement.kind in ("url", "vcs"):
            path = _get_external_requirement_filepath(requirement)
            templated_abspath = Path("${output_dir}", "deps", "pip", path)
            return requirement.update(url=f"file://{templated_abspath}")
        return None

    replaced = [maybe_replace(requirement) for requirement in requirements_file.requirements]
    if not any(replaced):
        # No need for a custom requirements file
        return None

    requirements = [
        replaced or original for replaced, original in zip(replaced, requirements_file.requirements)
    ]
    replaced_requirements_file = PipRequirementsFile.from_requirements_and_options(
        requirements, requirements_file.options
    )

    return ProjectFile(
        abspath=Path(requirements_file_path).resolve(),
        template=replaced_requirements_file.generate_file_content(),
    )
