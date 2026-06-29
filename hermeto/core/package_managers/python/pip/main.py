# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import functools
import logging
import tarfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, NamedTuple
from urllib import parse as urlparse

import aiohttp
import pypi_simple
import requests.auth
from packageurl import PackageURL
from packaging.utils import canonicalize_name

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import LockfileNotFound, NotAGitRepo, PackageRejected, UnsupportedFeature
from hermeto.core.models.input import PipBinaryFilters, Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.sbom import (
    Component,
    create_backend_annotation,
)
from hermeto.core.package_managers.general import (
    async_download_files,
    download_binary_file,
    extract_git_info,
    get_vcs_qualifiers,
)
from hermeto.core.package_managers.python.pip.package_distributions import (
    DistributionPackageInfo,
    process_package_distributions,
)
from hermeto.core.package_managers.python.pip.packages import (
    PipPackage,
    PipPackageInfo,
    PyPIPackage,
    URLPackage,
    VCSPackage,
)
from hermeto.core.package_managers.python.pip.project_files import PyProjectTOML, SetupCFG, SetupPY
from hermeto.core.package_managers.python.pip.requirements import (
    ALL_FILE_EXTENSIONS,
    SDIST_FILE_EXTENSIONS,
    WHEEL_FILE_EXTENSION,
    PipRequirement,
    PipRequirementsFile,
    process_requirements_options,
    validate_requirements,
    validate_requirements_hashes,
)
from hermeto.core.package_managers.python.pip.rust import (
    filter_packages_with_rust_code,
    find_and_fetch_rust_dependencies,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball, get_repo_id

log = logging.getLogger(__name__)

DEFAULT_BUILD_REQUIREMENTS_FILE = "requirements-build.txt"
DEFAULT_REQUIREMENTS_FILE = "requirements.txt"


class _PyPIArtifact(NamedTuple):
    requirement: PipRequirement
    dpi: DistributionPackageInfo


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


def _checksum_must_match_or_path_unlink(path: Path, checksum_info: Iterable[ChecksumInfo]) -> bool:
    try:
        must_match_any_checksum(path, checksum_info)
        return True
    except PackageRejected:
        path.unlink(missing_ok=True)
        log.warning("Download '%s' was removed from the output directory", path.name)
        return False


def _download_pypi_packages(
    requirements_file: PipRequirementsFile,
    pip_deps_dir: RootedPath,
    pypi_artifacts: list[_PyPIArtifact],
    index_url: str,
    proxy_url: str | None = None,
    auth: str | None = None,
) -> list[PyPIPackage]:
    files = {dpi.url: dpi.path for _, dpi in pypi_artifacts if not dpi.path.exists()}
    if files:
        headers = None
        if auth is not None:
            headers = {url: {"Authorization": auth} for url in files}
        log.info("Downloading %d PyPI artifacts", len(files))
        asyncio.run(
            async_download_files(files, get_config().runtime.concurrency_limit, headers=headers)
        )

    result: list[PyPIPackage] = []
    for req, dpi in pypi_artifacts:
        missing_req_file_checksum = not bool(dpi.req_file_checksums)
        if dpi.checksums_to_match:
            if not _checksum_must_match_or_path_unlink(dpi.path, dpi.checksums_to_match):
                continue
        if dpi.package_type == "sdist":
            _check_metadata_in_sdist(dpi.path)

        dep = PyPIPackage(
            name=dpi.name,
            path=dpi.path,
            requirement_file=str(requirements_file.file_path.subpath_from_root),
            missing_req_file_checksum=missing_req_file_checksum,
            package_type=dpi.package_type,
            version=dpi.version,
            index_url=index_url,
            proxy_url=proxy_url,
        )
        log.debug(
            "Successfully processed '%s' in path '%s'",
            req.download_line,
            dep.path.relative_to(pip_deps_dir.root),
        )
        result.append(dep)
    return result


def _download_vcs_package(
    req: PipRequirement,
    requirements_file: PipRequirementsFile,
    pip_deps_dir: RootedPath,
) -> VCSPackage:
    """Fetch a Python package from VCS (only git is supported)."""
    git_info = extract_git_info(req.url)

    download_to = pip_deps_dir.join_within_root(_get_external_requirement_filepath(req))
    download_to.path.parent.mkdir(exist_ok=True, parents=True)

    clone_as_tarball(git_info["url"], git_info["ref"], to_path=download_to.path)

    dep = VCSPackage(
        name=req.package,
        path=download_to.path,
        requirement_file=str(requirements_file.file_path.subpath_from_root),
        missing_req_file_checksum=True,
        package_type="",
        url=git_info["url"],
        ref=git_info["ref"],
    )
    log.debug(
        "Successfully processed '%s' in path '%s'",
        req.download_line,
        dep.path.relative_to(pip_deps_dir.root),
    )
    return dep


def _download_url_package(
    req: PipRequirement,
    requirements_file: PipRequirementsFile,
    pip_deps_dir: RootedPath,
    trusted_hosts: set[str],
) -> URLPackage | None:
    """Download a Python package from a URL.

    :param trusted_hosts: if host (or host:port) is trusted, do not verify SSL
    """
    parsed_url = urlparse.urlparse(req.url)

    download_to = pip_deps_dir.join_within_root(_get_external_requirement_filepath(req))
    download_to.path.parent.mkdir(exist_ok=True, parents=True)

    if parsed_url.port is not None and f"{parsed_url.hostname}:{parsed_url.port}" in trusted_hosts:
        log.debug(
            "Disabling SSL verification, %s:%s is a --trusted-host",
            parsed_url.hostname,
            parsed_url.port,
        )
        insecure = True
    elif parsed_url.hostname in trusted_hosts:
        log.debug("Disabling SSL verification, %s is a --trusted-host", parsed_url.hostname)
        insecure = True
    else:
        insecure = False

    download_binary_file(req.url, download_to.path, insecure=insecure)

    hashes = req.hashes
    missing_req_file_checksum = True
    if hashes:
        missing_req_file_checksum = False
        if not _checksum_must_match_or_path_unlink(
            download_to.path, list(map(ChecksumInfo.from_hash, hashes))
        ):
            return None

    dep = URLPackage(
        name=req.package,
        path=download_to.path,
        requirement_file=str(requirements_file.file_path.subpath_from_root),
        missing_req_file_checksum=missing_req_file_checksum,
        package_type="wheel" if parsed_url.path.endswith(WHEEL_FILE_EXTENSION) else "",
        original_url=req.url,
        checksum=req.hashes[0],
    )
    log.debug(
        "Successfully processed '%s' in path '%s'",
        req.download_line,
        dep.path.relative_to(pip_deps_dir.root),
    )
    return dep


async def _resolve_pypi_distributions(
    reqs: list[PipRequirement],
    resolve_callback: Callable[[PipRequirement], list[DistributionPackageInfo]],
) -> list[list[DistributionPackageInfo]]:
    """Resolve PyPI distributions for all requirements concurrently."""
    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, resolve_callback, req) for req in reqs]
    return await asyncio.gather(*tasks)


def _resolve_and_download_pypi_packages(
    pypi_reqs: list[PipRequirement],
    requirements_file: PipRequirementsFile,
    pip_deps_dir: RootedPath,
    binary_filters: PipBinaryFilters | None,
    index_url: str,
) -> list[PyPIPackage]:
    """Resolve and download all PyPI packages."""
    config = get_config()
    proxy_url = str(config.pip.proxy_url) if config.pip.proxy_url is not None else None
    # A proxy must be used only if an index is the standard one, custom indices must
    # be preserved. A custom index would always override proxy.
    is_standard = lambda index_url: index_url and index_url == pypi_simple.PYPI_SIMPLE_ENDPOINT
    query_url = proxy_url if (proxy_url is not None and is_standard(index_url)) else index_url
    requests_auth = None
    aiohttp_auth = None
    # No attempt should be made at logging into custom indices, if query URL ends up pointing
    # to a custom index then authorization must be skipped.
    if config.pip.proxy_login and config.pip.proxy_password and (query_url == proxy_url):
        requests_auth = requests.auth.HTTPBasicAuth(
            config.pip.proxy_login, config.pip.proxy_password
        )
        aiohttp_auth = aiohttp.encode_basic_auth(config.pip.proxy_login, config.pip.proxy_password)

    resolve_callback = functools.partial(
        process_package_distributions,
        pip_deps_dir=pip_deps_dir,
        binary_filters=binary_filters,
        index_url=query_url,
        auth=requests_auth,
    )
    pypi_dpis = asyncio.run(_resolve_pypi_distributions(pypi_reqs, resolve_callback))
    reqs_dpis_zipped = zip(pypi_reqs, pypi_dpis)
    pypi_artifacts = [_PyPIArtifact(req, dpi) for req, dpis in reqs_dpis_zipped for dpi in dpis]
    # If a standard PyPI index is used with proxy URL then proxy URL must be reported,
    # if a custom index is used then proxy URL must not be reported even if set.
    proxy_to_report = proxy_url if (proxy_url is not None and (proxy_url != index_url)) else None
    return _download_pypi_packages(
        requirements_file,
        pip_deps_dir,
        pypi_artifacts,
        index_url=index_url,
        proxy_url=proxy_to_report,
        auth=aiohttp_auth,
    )


def _download_dependencies(
    output_dir: RootedPath,
    requirements_file: PipRequirementsFile,
    binary_filters: PipBinaryFilters | None = None,
) -> list[PipPackage]:
    """
    Download artifacts of all dependency packages in a requirements.txt file.

    :param output_dir: the root output directory for this request
    :param requirements_file: A requirements.txt file
    :param binary_filters: process wheels?
    :return: list of PipPackage instances for each downloaded package
    """
    options: dict[str, Any] = process_requirements_options(requirements_file.options)
    trusted_hosts = set(options["trusted_hosts"])
    processed: list[PipPackage] = []

    if options["require_hashes"]:
        log.info("Global --require-hashes option used, will require hashes")
        require_hashes = True
    elif any(req.hashes for req in requirements_file.requirements):
        log.info("At least one dependency uses the --hash option, will require hashes")
        require_hashes = True
    else:
        log.info(
            "No hash options used, will not require hashes unless HTTP(S) dependencies are present."
        )
        require_hashes = False

    validate_requirements(requirements_file.requirements)
    validate_requirements_hashes(requirements_file.requirements, require_hashes)

    pip_deps_dir: RootedPath = output_dir.join_within_root("deps", "pip")
    pip_deps_dir.path.mkdir(parents=True, exist_ok=True)

    pypi_reqs: list[PipRequirement] = []
    for req in requirements_file.requirements:
        log.info("-- Processing requirement line '%s'", req.download_line)
        if req.kind == "pypi":
            pypi_reqs.append(req)
            continue
        elif req.kind == "vcs":
            processed.append(_download_vcs_package(req, requirements_file, pip_deps_dir))
        elif req.kind == "url":
            download_info = _download_url_package(
                req, requirements_file, pip_deps_dir, trusted_hosts
            )
            if download_info is not None:
                processed.append(download_info)
        else:
            # Should not happen
            raise RuntimeError(f"Unexpected requirement kind: '{req.kind!r}'")

        log.info("-- Finished processing requirement line '%s'", req.download_line)

    if pypi_reqs:
        index_url = options["index_url"] or pypi_simple.PYPI_SIMPLE_ENDPOINT
        processed.extend(
            _resolve_and_download_pypi_packages(
                pypi_reqs, requirements_file, pip_deps_dir, binary_filters, index_url
            )
        )

    return processed


def _download_from_requirement_files(
    output_dir: RootedPath,
    files: list[RootedPath],
    binary_filters: PipBinaryFilters | None = None,
) -> list[PipPackage]:
    """
    Download dependencies listed in the requirement files.

    :param output_dir: the root output directory for this request
    :param files: list of absolute paths to pip requirements files
    :param binary_filters: process wheels?
    :return: list of PipPackage instances for each downloaded package
    :raises PackageRejected: If requirement file does not exist
    """
    requirements: list[PipPackage] = []
    for req_file in files:
        if not req_file.path.exists():
            raise LockfileNotFound(
                files=req_file.path,
                solution="Please check that you have specified correct requirements file paths",
            )
        requirements.extend(
            _download_dependencies(output_dir, PipRequirementsFile(req_file), binary_filters)
        )

    return requirements


def _default_requirement_file_list(path: RootedPath, devel: bool = False) -> list[RootedPath]:
    """
    Get the paths for the default pip requirement files, if they are present.

    :param path: the full path to the application source code
    :param devel: whether to return the build requirement files
    :return: list of str representing the absolute paths to the Python requirement files
    """
    filename = DEFAULT_BUILD_REQUIREMENTS_FILE if devel else DEFAULT_REQUIREMENTS_FILE
    req = path.join_within_root(filename)
    return [req] if req.path.is_file() else []


def _resolve_pip(
    package_path: RootedPath,
    output_dir: RootedPath,
    requirement_files: list[Path] | None = None,
    build_requirement_files: list[Path] | None = None,
    binary_filters: PipBinaryFilters | None = None,
) -> PipPackageInfo:
    """Resolve and fetch pip dependencies for the given pip application.

    :raises PackageRejected | UnsupportedFeature: if the package is not compatible with our
        requirements/expectations
    """
    pkg_name, pkg_version = _get_pip_metadata(package_path)

    def resolve_req_files(req_files: list[Path] | None, devel: bool) -> list[RootedPath]:
        resolved: list[RootedPath] = []
        # This could be an empty list
        if req_files is None:
            resolved.extend(_default_requirement_file_list(package_path, devel=devel))
        else:
            resolved.extend([package_path.join_within_root(r) for r in req_files])

        return resolved

    resolved_req_files = resolve_req_files(requirement_files, False)
    if not resolved_req_files:
        log.warning("No requirements files found, no dependencies will be fetched")
    else:
        log.info(
            "Using requirements files: %s",
            ", ".join(str(f.subpath_from_root) for f in resolved_req_files),
        )

    resolved_build_req_files = resolve_req_files(build_requirement_files, True)
    if not resolved_build_req_files:
        log.info("No build requirements files found")
    else:
        log.info(
            "Using build requirements files: %s",
            ", ".join(str(f.subpath_from_root) for f in resolved_build_req_files),
        )

    requires = _download_from_requirement_files(output_dir, resolved_req_files, binary_filters)
    build_requires = _download_from_requirement_files(
        output_dir, resolved_build_req_files, binary_filters
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
        requirements=[*resolved_req_files, *resolved_build_req_files],
        packages_containing_rust_code=packages_containing_rust_code,
    )


def _get_external_requirement_filepath(requirement: PipRequirement) -> Path:
    """Get the relative path under deps/pip/ where a URL or VCS requirement should be placed."""
    if requirement.kind == "url":
        package = requirement.package
        hash_spec = requirement.hashes[0]
        _, _, digest = hash_spec.partition(":")
        orig_url = urlparse.urlparse(requirement.url)
        file_ext = ""
        for ext in ALL_FILE_EXTENSIONS:
            if orig_url.path.endswith(ext):
                file_ext = ext
                break

        # wheel filename must remain unchanged and unquoted
        if file_ext == WHEEL_FILE_EXTENSION:
            filename = Path(orig_url.path).name
            filepath = Path(urlparse.unquote(filename))
        else:
            filepath = Path(f"{package}-{digest}{file_ext}")

    elif requirement.kind == "vcs":
        git_info = extract_git_info(requirement.url)
        repo = git_info["repo"]
        ref = git_info["ref"]
        filepath = Path(f"{repo}-gitcommit-{ref}.tar.gz")
    else:
        raise ValueError(f"{requirement.kind=} is neither 'url' nor 'vcs'")

    return filepath


def _iter_zip_file(file_path: Path) -> Iterator[str]:
    with zipfile.ZipFile(file_path, "r") as zf:
        yield from zf.namelist()


def _iter_tar_file(file_path: Path) -> Iterator[str]:
    with tarfile.open(file_path, "r") as tar:
        for member in tar:
            yield member.name


def _is_pkg_info_dir(path: str) -> bool:
    """Simply check whether a path represents the PKG_INFO directory.

    Generally, it is in the format for example: pkg-1.0/PKG_INFO
    """
    return Path(path).name == "PKG-INFO"


def _check_metadata_in_sdist(sdist_path: Path) -> None:
    """Check if a downloaded sdist package has metadata.

    :param sdist_path: the path of a sdist package file.
    :type sdist_path: pathlib.Path
    :raise PackageRejected: if the sdist is invalid.
    """
    if sdist_path.name.endswith(".zip"):
        files_iter = _iter_zip_file(sdist_path)
    elif sdist_path.name.endswith(".tar.Z"):
        log.warning("Skip checking metadata from compressed sdist %s", sdist_path.name)
        return
    elif any(map(sdist_path.name.endswith, SDIST_FILE_EXTENSIONS)):
        files_iter = _iter_tar_file(sdist_path)
    else:
        # Invalid usage of the method (we don't download files without a known extension)
        raise ValueError(
            f"Cannot check metadata from {sdist_path}, "
            f"which does not have a known supported extension.",
        )

    try:
        if not any(map(_is_pkg_info_dir, files_iter)):
            raise PackageRejected(
                f"{sdist_path.name} does not include metadata (there is no PKG-INFO file). "
                "It is not a valid sdist and cannot be downloaded from PyPI.",
                solution=(
                    "Consider editing your requirements file to download the package from git "
                    "or a direct download URL instead."
                ),
            )
    except tarfile.ReadError as e:
        raise PackageRejected(f"Cannot open {sdist_path} as a Tar file. Error: {e}")
    except zipfile.BadZipFile as e:
        raise PackageRejected(f"Cannot open {sdist_path} as a Zip file. Error: {e}")


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
            return requirement.copy(url=f"file://{templated_abspath}")
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
