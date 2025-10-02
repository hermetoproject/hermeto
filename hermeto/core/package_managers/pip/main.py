# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import logging
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib import parse as urlparse

import pypi_simple
from packageurl import PackageURL
from packaging.utils import canonicalize_name

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import LockfileNotFound, PackageRejected, UnsupportedFeature
from hermeto.core.models.input import PipBinaryFilters, Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.general import (
    async_download_files,
    download_binary_file,
    extract_git_info,
)
from hermeto.core.package_managers.pip.package_distributions import (
    DistributionPackageInfo,
    process_package_distributions,
)
from hermeto.core.package_managers.pip.project_files import PyProjectTOML, SetupCFG, SetupPY
from hermeto.core.package_managers.pip.requirements import (
    ALL_FILE_EXTENSIONS,
    SDIST_FILE_EXTENSIONS,
    WHEEL_FILE_EXTENSION,
    PipRequirement,
    PipRequirementsFile,
    process_requirements_options,
    validate_requirements,
    validate_requirements_hashes,
)
from hermeto.core.package_managers.pip.rust import (
    filter_packages_with_rust_code,
    find_and_fetch_rust_dependencies,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball, get_repo_id
from hermeto.core.type_aliases import StrPath

log = logging.getLogger(__name__)

DEFAULT_BUILD_REQUIREMENTS_FILE = "requirements-build.txt"
DEFAULT_REQUIREMENTS_FILE = "requirements.txt"


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
        purl = _generate_purl_main_package(info["package"], package_path)
        components.append(
            Component(name=info["package"]["name"], version=info["package"]["version"], purl=purl)
        )

        for dependency in info["dependencies"]:
            purl = _generate_purl_dependency(dependency)
            version = dependency["version"] if dependency["kind"] == "pypi" else None

            missing_hash_in_file: frozenset = frozenset()
            if dependency["missing_req_file_checksum"]:
                missing_hash_in_file = frozenset({dependency["requirement_file"]})

            pip_package_binary = False
            if dependency["package_type"] == "wheel":
                pip_package_binary = True

            pip_build_dependency = False
            if dependency["build_dependency"] is True:
                pip_build_dependency = True

            components.append(
                Component(
                    name=dependency["name"],
                    version=version,
                    purl=purl,
                    properties=PropertySet(
                        missing_hash_in_file=missing_hash_in_file,
                        pip_package_binary=pip_package_binary,
                        pip_build_dependency=pip_build_dependency,
                    ).to_properties(),
                )
            )

        replaced_requirements_files = map(_replace_external_requirements, info["requirements"])
        project_files.extend(filter(None, replaced_requirements_files))
        # each package can have Rust dependencies
        packages_containing_rust_code += info["packages_containing_rust_code"]

    pip_packages = RequestOutput.from_obj_list(
        components=components,
        environment_variables=environment_variables,
        project_files=project_files,
    )

    cargo_packages = find_and_fetch_rust_dependencies(request, packages_containing_rust_code)
    return pip_packages + cargo_packages


def _generate_purl_main_package(package: dict[str, Any], package_path: RootedPath) -> str:
    """Get the purl for this package."""
    type = "pypi"
    name = package["name"]
    version = package["version"]
    url = get_repo_id(package_path.root).as_vcs_url_qualifier()
    qualifiers = {"vcs_url": url}
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


def _generate_purl_dependency(package: dict[str, Any]) -> str:
    """Get the purl for this dependency."""
    type = "pypi"
    name = package["name"]
    dependency_kind = package.get("kind", None)
    version = None
    qualifiers: dict[str, str] | None = None

    if dependency_kind == "pypi":
        version = package["version"]
        index_url = package["index_url"]
        if index_url.rstrip("/") != pypi_simple.PYPI_SIMPLE_ENDPOINT.rstrip("/"):
            qualifiers = {"repository_url": index_url}
    elif dependency_kind == "vcs":
        qualifiers = {"vcs_url": package["version"]}
    elif dependency_kind == "url":
        defragmented_url, fragment = urlparse.urldefrag(package["version"])
        fragments: dict[str, list[str]] = urlparse.parse_qs(fragment)
        checksum: str = fragments["cachito_hash"][0]
        qualifiers = {"download_url": defragmented_url, "checksum": checksum}
    else:
        # Should not happen
        raise RuntimeError(f"Unexpected requirement kind: {dependency_kind}")

    purl = PackageURL(
        type=type,
        name=name,
        version=version,
        qualifiers=qualifiers,
    )

    return purl.to_string()


def _infer_package_name_from_origin_url(package_dir: RootedPath) -> str:
    try:
        repo_id = get_repo_id(package_dir.root)
    except UnsupportedFeature:
        raise PackageRejected(
            reason="Unable to infer package name from origin URL",
            solution=(
                "Provide valid metadata in the package files or ensure"
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


def _process_req(
    req: PipRequirement,
    requirements_file: PipRequirementsFile,
    pip_deps_dir: RootedPath,
    download_info: dict[str, Any],
    dpi: DistributionPackageInfo | None = None,
) -> dict[str, Any]:
    download_info["kind"] = req.kind
    download_info["requirement_file"] = str(requirements_file.file_path.subpath_from_root)
    download_info["missing_req_file_checksum"] = True
    download_info["package_type"] = ""

    def _checksum_must_match_or_path_unlink(
        path: Path, checksum_info: Iterable[ChecksumInfo]
    ) -> None:
        try:
            # returns None, raises PackageRejected on failure
            must_match_any_checksum(path, checksum_info)
        except PackageRejected:
            path.unlink()
            log.warning("Download '%s' was removed from the output directory", path.name)

    if dpi:
        if dpi.req_file_checksums:
            download_info["missing_req_file_checksum"] = False
        if dpi.has_checksums_to_match:
            _checksum_must_match_or_path_unlink(dpi.path, dpi.checksums_to_match)
        if dpi.package_type == "sdist":
            _check_metadata_in_sdist(dpi.path)
        download_info["package_type"] = dpi.package_type
        download_info["index_url"] = dpi.index_url
    elif req.kind == "vcs":
        # `missing_req_file_checksum` is *always* True for VCS deps
        pass
    else:
        if req.kind == "url":
            hashes = req.hashes or [req.qualifiers.get("cachito_hash", "")]
            if hashes:
                download_info["missing_req_file_checksum"] = False
                _checksum_must_match_or_path_unlink(
                    download_info["path"], list(map(ChecksumInfo.from_hash, hashes))
                )

    log.debug(
        "Successfully processed '%s' in path '%s'",
        req.download_line,
        download_info["path"].relative_to(pip_deps_dir.root),
    )

    return download_info


def _process_pypi_req(
    req: PipRequirement,
    requirements_file: PipRequirementsFile,
    index_url: str,
    pip_deps_dir: RootedPath,
    binary_filters: PipBinaryFilters | None = None,
) -> list[dict[str, Any]]:
    download_infos: list[dict[str, Any]] = []

    artifacts: list[DistributionPackageInfo] = process_package_distributions(
        req, pip_deps_dir, binary_filters, index_url
    )

    files: dict[str, StrPath] = {dpi.url: dpi.path for dpi in artifacts if not dpi.path.exists()}
    asyncio.run(async_download_files(files, get_config().runtime.concurrency_limit))

    for artifact in artifacts:
        download_infos.append(
            _process_req(
                req,
                requirements_file,
                pip_deps_dir,
                artifact.download_info,
                dpi=artifact,
            )
        )

    return download_infos


def _process_vcs_req(
    req: PipRequirement, pip_deps_dir: RootedPath, **kwargs: Any
) -> dict[str, Any]:
    return _process_req(
        req,
        pip_deps_dir=pip_deps_dir,
        download_info=_download_vcs_package(req, pip_deps_dir),
        **kwargs,
    )


def _process_url_req(
    req: PipRequirement, pip_deps_dir: RootedPath, trusted_hosts: set[str], **kwargs: Any
) -> dict[str, Any]:
    result = _process_req(
        req,
        pip_deps_dir=pip_deps_dir,
        download_info=_download_url_package(req, pip_deps_dir, trusted_hosts),
        **kwargs,
    )
    if req.url.endswith(WHEEL_FILE_EXTENSION):
        result["package_type"] = "wheel"

    return result


def _download_dependencies(
    output_dir: RootedPath,
    requirements_file: PipRequirementsFile,
    binary_filters: PipBinaryFilters | None = None,
) -> list[dict[str, Any]]:
    """
    Download artifacts of all dependency packages in a requirements.txt file.

    :param output_dir: the root output directory for this request
    :param requirements_file: A requirements.txt file
    :param binary_filters: process wheels?
    :return: Info about downloaded packages; all items will contain "kind" and "path" keys
        (and more based on kind, see _download_*_package functions for more details)
    :rtype: list[dict]
    """
    options: dict[str, Any] = process_requirements_options(requirements_file.options)
    trusted_hosts = set(options["trusted_hosts"])
    processed: list[dict[str, Any]] = []

    if options["require_hashes"]:
        log.info("Global --require-hashes option used, will require hashes")
        require_hashes = True
    elif any(req.hashes for req in requirements_file.requirements):
        log.info("At least one dependency uses the --hash option, will require hashes")
        require_hashes = True
    else:
        # URL deps with a `cachito_hash` qualifier (which is a loophole
        # allowing for unhashed VCS deps AND URL deps to coexist in a
        # 'requirements.txt', thus `require_hashes` should NOT be set), will
        # fall through to this branch.
        log.info(
            "No hash options used, will not require hashes unless HTTP(S) dependencies are present."
        )
        require_hashes = False

    validate_requirements(requirements_file.requirements)
    validate_requirements_hashes(requirements_file.requirements, require_hashes)

    pip_deps_dir: RootedPath = output_dir.join_within_root("deps", "pip")
    pip_deps_dir.path.mkdir(parents=True, exist_ok=True)

    for req in requirements_file.requirements:
        log.info("-- Processing requirement line '%s'", req.download_line)
        if req.kind == "pypi":
            download_infos: list[dict[str, Any]] = _process_pypi_req(
                req,
                requirements_file=requirements_file,
                index_url=options["index_url"] or pypi_simple.PYPI_SIMPLE_ENDPOINT,
                pip_deps_dir=pip_deps_dir,
                binary_filters=binary_filters,
            )
            processed.extend(download_infos)
        elif req.kind == "vcs":
            download_info = _process_vcs_req(
                req,
                requirements_file=requirements_file,
                pip_deps_dir=pip_deps_dir,
            )
            processed.append(download_info)
        elif req.kind == "url":
            download_info = _process_url_req(
                req,
                requirements_file=requirements_file,
                pip_deps_dir=pip_deps_dir,
                trusted_hosts=trusted_hosts,
            )
            processed.append(download_info)
        else:
            # Should not happen
            raise RuntimeError(f"Unexpected requirement kind: '{req.kind!r}'")

        log.info("-- Finished processing requirement line '%s'\n", req.download_line)

    return processed


def _download_vcs_package(requirement: PipRequirement, pip_deps_dir: RootedPath) -> dict[str, Any]:
    """
    Fetch the source for a Python package from VCS (only git is supported).

    :param PipRequirement requirement: VCS requirement from a requirements.txt file
    :param RootedPath pip_deps_dir: The deps/pip directory in an application request bundle

    :return: Dict with package name, download path and git info
    """
    git_info = extract_git_info(requirement.url)

    download_to = pip_deps_dir.join_within_root(_get_external_requirement_filepath(requirement))
    download_to.path.parent.mkdir(exist_ok=True, parents=True)

    clone_as_tarball(git_info["url"], git_info["ref"], to_path=download_to.path)

    return {
        "package": requirement.package,
        "path": download_to.path,
        **git_info,
    }


def _download_url_package(
    requirement: PipRequirement, pip_deps_dir: RootedPath, trusted_hosts: set[str]
) -> dict[str, Any]:
    """
    Download a Python package from a URL.

    :param PipRequirement requirement: URL requirement from a requirements.txt file
    :param RootedPath pip_deps_dir: The deps/pip directory in an application request bundle
    :param set[str] trusted_hosts: If host (or host:port) is trusted, do not verify SSL

    :return: Dict with package name, download path, original URL and URL with hash
    """
    url = urlparse.urlparse(requirement.url)

    download_to = pip_deps_dir.join_within_root(_get_external_requirement_filepath(requirement))
    download_to.path.parent.mkdir(exist_ok=True, parents=True)

    if url.port is not None and f"{url.hostname}:{url.port}" in trusted_hosts:
        log.debug("Disabling SSL verification, %s:%s is a --trusted-host", url.hostname, url.port)
        insecure = True
    elif url.hostname in trusted_hosts:
        log.debug("Disabling SSL verification, %s is a --trusted-host", url.hostname)
        insecure = True
    else:
        insecure = False

    download_binary_file(requirement.url, download_to.path, insecure=insecure)

    if "cachito_hash" in requirement.qualifiers:
        url_with_hash = requirement.url
    else:
        url_with_hash = _add_cachito_hash_to_url(url, requirement.hashes[0])

    return {
        "package": requirement.package,
        "path": download_to.path,
        "original_url": requirement.url,
        "url_with_hash": url_with_hash,
    }


def _add_cachito_hash_to_url(parsed_url: urlparse.ParseResult, hash_spec: str) -> str:
    """
    Add the #cachito_hash fragment to URL.

    :param urllib.urlparse.ParseResult parsed_url: A parsed URL with no cachito_hash in fragment
    :param str hash_spec: A hash specifier - "algorithm:digest", e.g. "sha256:123456"
    :return: Original URL + cachito_hash in fragment
    :rtype: str
    """
    new_fragment = f"cachito_hash={hash_spec}"
    if parsed_url.fragment:
        new_fragment = f"{parsed_url.fragment}&{new_fragment}"
    return parsed_url._replace(fragment=new_fragment).geturl()


def _download_from_requirement_files(
    output_dir: RootedPath,
    files: list[RootedPath],
    binary_filters: PipBinaryFilters | None = None,
) -> list[dict[str, Any]]:
    """
    Download dependencies listed in the requirement files.

    :param output_dir: the root output directory for this request
    :param files: list of absolute paths to pip requirements files
    :param binary_filters: process wheels?
    :return: Info about downloaded packages; see download_dependencies return docs for further
        reference
    :raises PackageRejected: If requirement file does not exist
    """
    requirements: list[dict[str, Any]] = []
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
) -> dict[str, Any]:
    """
    Resolve and fetch pip dependencies for the given pip application.

    :param app_path: the full path to the application source code
    :param output_dir: the root output directory for this request
    :param list requirement_files: a list of str representing paths to the Python requirement files
        to be used to compile a list of dependencies to be fetched
    :param list build_requirement_files: a list of str representing paths to the Python build
        requirement files to be used to compile a list of build dependencies to be fetched
    :param binary_filters: process wheels?
    :return: a dictionary that has the following keys:
        ``package`` which is the dict representing the main Package,
        ``dependencies`` which is a list of dicts representing the package Dependencies
        ``requirements`` which is a list of absolute paths for the processed requirement files
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
    resolved_build_req_files = resolve_req_files(build_requirement_files, True)

    requires = _download_from_requirement_files(output_dir, resolved_req_files, binary_filters)
    build_requires = _download_from_requirement_files(
        output_dir, resolved_build_req_files, binary_filters
    )

    if get_config().pip.ignore_dependencies_crates:
        packages_containing_rust_code = []
    else:
        packages_containing_rust_code = filter_packages_with_rust_code(requires + build_requires)

    # Mark all build dependencies as such
    for dependency in build_requires:
        dependency["build_dependency"] = True

    def _version(dep: dict[str, Any]) -> str:
        if dep["kind"] == "pypi":
            version = dep["version"]
        elif dep["kind"] == "vcs":
            # Version is "git+" followed by the URL used to to fetch from git
            version = f"git+{dep['url']}@{dep['ref']}"
        else:
            # Version is the original URL with #cachito_hash added if it was not present
            version = dep["url_with_hash"]
        return version

    dependencies = [
        {
            "name": dep["package"],
            "version": _version(dep),
            "index_url": dep.get("index_url"),
            "type": "pip",
            "build_dependency": dep.get("build_dependency", False),
            "kind": dep["kind"],
            "requirement_file": dep["requirement_file"],
            "missing_req_file_checksum": dep["missing_req_file_checksum"],
            "package_type": dep["package_type"],
        }
        for dep in (requires + build_requires)
    ]

    return {
        "package": {"name": pkg_name, "version": pkg_version, "type": "pip"},
        "dependencies": dependencies,
        "requirements": [*resolved_req_files, *resolved_build_req_files],
        "packages_containing_rust_code": packages_containing_rust_code,
    }


def _get_external_requirement_filepath(requirement: PipRequirement) -> Path:
    """Get the relative path under deps/pip/ where a URL or VCS requirement should be placed."""
    if requirement.kind == "url":
        package = requirement.package
        hashes = requirement.hashes
        hash_spec = hashes[0] if hashes else requirement.qualifiers["cachito_hash"]
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
        raise PackageRejected(
            f"Cannot open {sdist_path} as a Tar file. Error: {str(e)}", solution=None
        )
    except zipfile.BadZipFile as e:
        raise PackageRejected(
            f"Cannot open {sdist_path} as a Zip file. Error: {str(e)}", solution=None
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
