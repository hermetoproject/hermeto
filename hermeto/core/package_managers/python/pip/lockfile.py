# SPDX-License-Identifier: GPL-3.0-only
"""Lockfile abstraction and shared download pipeline for pip packages."""

import asyncio
import functools
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar
from urllib import parse as urlparse

import aiohttp
import pypi_simple
import requests.auth
import tomlkit
from packaging.utils import canonicalize_name

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import get_config
from hermeto.core.errors import InvalidInput, LockfileNotFound, PackageRejected, UnsupportedFeature
from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.models.output import ProjectFile
from hermeto.core.package_managers.general import (
    async_download_files,
    extract_git_info,
    patch_url_to_point_to_proxy,
)
from hermeto.core.package_managers.python.packaging_tool import PythonPackagingTool
from hermeto.core.package_managers.python.pip.package_distributions import (
    DistributionPackageInfo,
    WheelsFilter,
    process_package_distributions,
)
from hermeto.core.package_managers.python.pip.packages import (
    PipPackage,
    PyPIPackage,
    URLPackage,
    VCSPackage,
)
from hermeto.core.package_managers.python.pip.requirements import (
    WHEEL_FILE_EXTENSION,
    PipRequirement,
    PipRequirementsFile,
    get_external_requirement_filepath,
    process_requirements_options,
    validate_requirements,
    validate_requirements_hashes,
)
from hermeto.core.package_managers.python.pylock.models import Pylock
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


def _validate_index_url(url: str, source: str) -> None:
    """Validate a PyPI index URL regardless of where it was configured.

    :param url: the index URL to validate
    :param source: human-readable origin for error messages (e.g. "--index-url", "PIP_INDEX_URL")
    :raises PackageRejected: if the URL is invalid
    """
    parsed = urlparse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidInput(
            f"{source} must use http or https scheme, got: {parsed.scheme or 'none'}",
            solution=f"Set {source} to a valid HTTP(S) URL (e.g., https://pypi.org/simple/)",
        )
    if not parsed.netloc:
        raise InvalidInput(
            f"{source} must include a host",
            solution=f"Set {source} to a valid HTTP(S) URL (e.g., https://pypi.org/simple/)",
        )
    if parsed.username or parsed.password:
        raise InvalidInput(
            f"{source} must not contain embedded credentials",
            solution="Use a .netrc file for authentication instead of embedding "
            "credentials in the URL",
        )


async def _resolve_pypi_distributions(
    reqs: list[PipRequirement],
    resolve_callback: Callable[[PipRequirement], list[DistributionPackageInfo]],
) -> list[list[DistributionPackageInfo]]:
    """Resolve PyPI distributions for all requirements concurrently."""
    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, resolve_callback, req) for req in reqs]
    return await asyncio.gather(*tasks)


def _download_dependencies(
    deps: list[PipPackage],
    pip_deps_dir: RootedPath,
) -> list[PipPackage]:
    """Download all dependencies (VCS, URL, and PyPI packages).

    This is the shared download function used by all lockfile formats.
    It partitions deps by type, downloads VCS/URL individually, batches PyPI downloads,
    then verifies everything and returns only the kept packages.
    """
    pypi_deps: list[PyPIPackage] = []
    other_deps: list[PipPackage] = []

    for dep in deps:
        if isinstance(dep, PyPIPackage):
            pypi_deps.append(dep)
        else:
            other_deps.append(dep)

    processed: list[PipPackage] = []

    for dep in other_deps:
        log.info("-- Processing %s", dep.name)
        dep.download(pip_deps_dir)
        if dep.verify():
            processed.append(dep)
        log.info("-- Finished processing %s", dep.name)

    if pypi_deps:
        files_to_download: dict[str, Path] = {}
        headers_by_url: dict[str, dict[str, str]] = {}

        for pypi_dep in pypi_deps:
            download_loc = pypi_dep.download_location(pip_deps_dir)
            if not download_loc.path.exists():
                files_to_download[pypi_dep.remote_location] = download_loc.path
                if pypi_dep.auth_header:
                    headers_by_url[pypi_dep.remote_location] = {
                        "Authorization": pypi_dep.auth_header
                    }
            pypi_dep.path = download_loc.path

        if files_to_download:
            log.info("Downloading %d PyPI artifacts", len(files_to_download))
            asyncio.run(
                async_download_files(
                    files_to_download,
                    get_config().runtime.concurrency_limit,
                    headers=headers_by_url or None,
                )
            )

        for pypi_dep in pypi_deps:
            if pypi_dep.verify():
                processed.append(pypi_dep)

    return processed


class PipLockfile(ABC):
    """Abstract base for pip lockfile formats."""

    default_file: ClassVar[str]
    default_build_file: ClassVar[str]

    @classmethod
    @abstractmethod
    def from_file(cls, file_path: RootedPath) -> "PipLockfile":
        """Create a lockfile instance from a file."""
        ...

    @property
    @abstractmethod
    def file_path(self) -> RootedPath:
        """Return the lockfile path."""
        ...

    @abstractmethod
    def dependencies(
        self, binary_filters: PipBinaryFilters | None, pip_deps_dir: RootedPath
    ) -> list[PipPackage]:
        """Extract dependencies as PipPackage objects (not downloaded yet)."""
        ...

    def validate(self) -> None:
        """Validate the lockfile before downloading. No-op by default."""
        ...

    @abstractmethod
    def rewrite(self, dependencies: list[PipPackage]) -> ProjectFile | None:
        """Rewrite the lockfile so external URLs point at pre-fetched local files.

        Returns a ProjectFile with the rewritten content, or None when nothing
        needs rewriting.
        """
        ...


class RequirementsLockfile(PipLockfile):
    """Requirements.txt lockfile format."""

    default_file = "requirements.txt"
    default_build_file = "requirements-build.txt"

    def __init__(self, requirements_file: PipRequirementsFile) -> None:
        self._file = requirements_file

    @classmethod
    def from_file(cls, file_path: RootedPath) -> "RequirementsLockfile":
        """Create lockfile from a requirements.txt file."""
        return cls(PipRequirementsFile(file_path))

    @property
    def file_path(self) -> RootedPath:
        """Return the requirements file path."""
        return self._file.file_path

    @functools.cached_property
    def _options(self) -> dict[str, Any]:
        return process_requirements_options(self._file.options)

    @property
    def trusted_hosts(self) -> set[str]:
        """Return trusted hosts from --trusted-host options."""
        return set(self._options["trusted_hosts"])

    def validate(self) -> None:
        """Validate requirements and hashes."""
        require_hashes = False
        if self._options["require_hashes"]:
            log.info("Global --require-hashes option used, will require hashes")
            require_hashes = True
        elif any(req.hashes for req in self._file.requirements):
            log.info("At least one dependency uses the --hash option, will require hashes")
            require_hashes = True
        else:
            log.info(
                "No hash options used, will not require hashes unless HTTP(S) dependencies are present."
            )

        validate_requirements(self._file.requirements)
        validate_requirements_hashes(self._file.requirements, require_hashes)

    def dependencies(
        self, binary_filters: PipBinaryFilters | None, pip_deps_dir: RootedPath
    ) -> list[PipPackage]:
        """Extract dependencies from requirements.txt (no download, just build dep objects)."""
        deps: list[PipPackage] = []
        trusted_hosts = self.trusted_hosts
        requirement_file = str(self.file_path.subpath_from_root)

        pypi_reqs = []
        for req in self._file.requirements:
            if req.kind == "pypi":
                pypi_reqs.append(req)
            elif req.kind == "vcs":
                git_info = extract_git_info(req.direct_access_url)
                deps.append(
                    VCSPackage(
                        name=req.package,
                        requirement_file=requirement_file,
                        missing_req_file_checksum=True,
                        package_type="",
                        url=git_info["url"],
                        ref=git_info["ref"],
                    )
                )
            elif req.kind == "url":
                parsed_url = urlparse.urlparse(req.direct_access_url)
                insecure = (
                    parsed_url.port is not None
                    and f"{parsed_url.hostname}:{parsed_url.port}" in trusted_hosts
                ) or parsed_url.hostname in trusted_hosts

                deps.append(
                    URLPackage(
                        name=req.package,
                        requirement_file=requirement_file,
                        missing_req_file_checksum=not bool(req.hashes),
                        package_type="wheel"
                        if parsed_url.path.endswith(WHEEL_FILE_EXTENSION)
                        else "",
                        original_url=req.direct_access_url,
                        checksum=req.hashes[0] if req.hashes else "",
                        insecure=insecure,
                    )
                )

        if pypi_reqs:
            if self._options["index_url"]:
                _validate_index_url(self._options["index_url"], "--index-url")
                index_url = self._options["index_url"]
            elif pip_index_url := os.environ.get("PIP_INDEX_URL", "").strip():
                _validate_index_url(pip_index_url, "PIP_INDEX_URL")
                index_url = pip_index_url
                log.info(
                    "Using PIP_INDEX_URL='%s' (no --index-url in requirements file)",
                    pip_index_url,
                )
            else:
                index_url = pypi_simple.PYPI_SIMPLE_ENDPOINT

            config = get_config()
            proxy_url = str(config.pip.proxy_url) if config.pip.proxy_url is not None else None
            is_standard = lambda idx: idx and idx == pypi_simple.PYPI_SIMPLE_ENDPOINT
            query_url = (
                proxy_url if (proxy_url is not None and is_standard(index_url)) else index_url
            )
            requests_auth = None
            aiohttp_auth = None
            if config.pip.proxy_login and config.pip.proxy_password and (query_url == proxy_url):
                proxy_password = config.pip.proxy_password.get_secret_value()
                requests_auth = requests.auth.HTTPBasicAuth(config.pip.proxy_login, proxy_password)
                aiohttp_auth = aiohttp.encode_basic_auth(config.pip.proxy_login, proxy_password)

            resolve_callback = functools.partial(
                process_package_distributions,
                pip_deps_dir=pip_deps_dir,
                binary_filters=binary_filters,
                index_url=query_url,
                auth=requests_auth,
            )
            pypi_dpis = asyncio.run(_resolve_pypi_distributions(pypi_reqs, resolve_callback))

            proxy_to_report = (
                proxy_url if (proxy_url is not None and (proxy_url != index_url)) else None
            )
            for req, dpis in zip(pypi_reqs, pypi_dpis):
                for dpi in dpis:
                    missing_req_file_checksum = not bool(dpi.req_file_checksums)
                    deps.append(
                        PyPIPackage(
                            name=dpi.name,
                            requirement_file=requirement_file,
                            missing_req_file_checksum=missing_req_file_checksum,
                            package_type=dpi.package_type,
                            version=dpi.version,
                            index_url=index_url,
                            url=dpi.url,
                            checksums_to_match=dpi.checksums_to_match,
                            auth_header=aiohttp_auth,
                            proxy_url=proxy_to_report,
                        )
                    )

        return deps

    def rewrite(self, _dependencies: list[PipPackage]) -> ProjectFile | None:
        """Rewrite url/vcs requirement URLs to local file:// paths."""

        def maybe_replace(requirement: PipRequirement) -> PipRequirement | None:
            if requirement.kind in ("url", "vcs"):
                digest = requirement.hashes[0].partition(":")[2] if requirement.hashes else ""
                path = get_external_requirement_filepath(
                    requirement.kind, requirement.direct_access_url, requirement.package, digest
                )
                templated_abspath = Path("${output_dir}", "deps", "pip", path)
                return requirement.update(url=f"file://{templated_abspath}")
            return None

        replaced = [maybe_replace(req) for req in self._file.requirements]
        if not any(replaced):
            return None

        requirements = [new or original for new, original in zip(replaced, self._file.requirements)]
        replaced_file = PipRequirementsFile.from_requirements_and_options(
            requirements, self._file.options
        )
        return ProjectFile(
            abspath=Path(self._file.file_path).resolve(),
            template=replaced_file.generate_file_content(),
        )


def _infer_packaging_tool(
    packaging_tool: PythonPackagingTool | None, lockfile: Path | None
) -> PythonPackagingTool:
    """Pick the lockfile format from explicit input or the lockfile filename.

    An explicit ``packaging_tool`` always wins; otherwise the format is inferred
    from the ``lockfile`` filename, and everything else falls back to requirements.
    """
    if packaging_tool is not None:
        return packaging_tool
    if lockfile is not None:
        name = lockfile.name
        if name.endswith(".txt"):
            return PythonPackagingTool.REQUIREMENTS
        if name == "pylock.toml" or (name.startswith("pylock.") and name.endswith(".toml")):
            return PythonPackagingTool.PYLOCK
        raise InvalidInput(
            f"Cannot determine the lockfile format from the filename {name!r}",
            solution="Name the lockfile 'requirements.txt' or 'pylock.toml', or set "
            "'packaging_tool' explicitly (e.g. 'requirements' or 'pylock').",
        )
    return PythonPackagingTool.REQUIREMENTS


def _resolve_lockfile_paths(
    package_path: RootedPath, explicit: list[Path] | None, default_filename: str
) -> list[RootedPath]:
    """Resolve explicit lockfile paths, or auto-discover the default file if present."""
    if explicit is not None:
        return [package_path.join_within_root(p) for p in explicit]
    default = package_path.join_within_root(default_filename)
    return [default] if default.path.is_file() else []


def _download_lockfiles(
    lockfile_type: type[PipLockfile],
    files: list[RootedPath],
    output_dir: RootedPath,
    binary_filters: PipBinaryFilters | None,
) -> tuple[list[PipPackage], list[ProjectFile]]:
    """Parse, validate, download, and rewrite each lockfile.

    Returns the downloaded packages and any rewritten lockfiles (project files
    that redirect external URLs to the pre-fetched local artifacts).
    """
    pip_deps_dir = output_dir.join_within_root("deps", "pip")
    pip_deps_dir.path.mkdir(parents=True, exist_ok=True)

    packages: list[PipPackage] = []
    project_files: list[ProjectFile] = []
    for file_path in files:
        if not file_path.path.exists():
            raise LockfileNotFound(
                files=file_path.path,
                solution="Please check that you have specified correct lockfile paths",
            )
        lockfile = lockfile_type.from_file(file_path)
        lockfile.validate()
        deps = lockfile.dependencies(binary_filters, pip_deps_dir)
        downloaded = _download_dependencies(deps, pip_deps_dir)
        packages.extend(downloaded)
        if project_file := lockfile.rewrite(downloaded):
            project_files.append(project_file)

    return packages, project_files


class PylockLockfile(PipLockfile):
    """PEP 751 pylock.toml lockfile format."""

    default_file = "pylock.toml"
    default_build_file = "pylock.build.toml"

    def __init__(self, pylock_file: Pylock, file_path: RootedPath) -> None:
        self._pylock = pylock_file
        self._file_path = file_path

    @classmethod
    def from_file(cls, file_path: RootedPath) -> "PylockLockfile":
        """Create lockfile from a pylock.toml file."""
        return cls(Pylock.from_file(file_path), file_path)

    @property
    def file_path(self) -> RootedPath:
        """Return the pylock file path."""
        return self._file_path

    def dependencies(
        self, binary_filters: PipBinaryFilters | None, _pip_deps_dir: RootedPath
    ) -> list[PipPackage]:
        """Extract dependencies from pylock.toml (no download, just build dep objects).

        ``_pip_deps_dir`` is unused: pylock records artifact URLs directly, so no
        index resolution (which is what needs the deps dir) happens here.
        """
        requirement_file = str(self._file_path.subpath_from_root)
        wheels_filter = WheelsFilter(binary_filters) if binary_filters is not None else None
        deps: list[PipPackage] = []

        # Rewrite pinned PyPI artifact URLs through the configured proxy.
        config = get_config()
        proxy_url = str(config.pip.proxy_url) if config.pip.proxy_url is not None else None
        proxy_auth_header = None
        if proxy_url is not None and config.pip.proxy_login and config.pip.proxy_password:
            proxy_auth_header = aiohttp.encode_basic_auth(
                config.pip.proxy_login, config.pip.proxy_password.get_secret_value()
            )

        for package in self._pylock.packages:
            name = canonicalize_name(package.name)

            if package.kind == "directory":
                # The lockfile's own "." directory is already the main component.
                continue

            if package.vcs is not None:
                deps.append(
                    VCSPackage(
                        name=name,
                        requirement_file=requirement_file,
                        missing_req_file_checksum=True,
                        package_type="",
                        url=package.vcs.url,
                        ref=package.vcs.commit_id,
                    )
                )
                continue

            if package.archive is not None:
                archive_hashes = list(package.archive.hashes.items())
                checksum = (
                    f"{archive_hashes[0][0]}:{archive_hashes[0][1]}" if archive_hashes else ""
                )
                archive_path = urlparse.urlparse(package.archive.url).path
                deps.append(
                    URLPackage(
                        name=name,
                        requirement_file=requirement_file,
                        missing_req_file_checksum=False,
                        package_type=(
                            "wheel" if archive_path.endswith(WHEEL_FILE_EXTENSION) else ""
                        ),
                        original_url=package.archive.url,
                        checksum=checksum,
                        insecure=False,
                        checksums_to_match={
                            ChecksumInfo(algo, digest) for algo, digest in archive_hashes
                        },
                    )
                )
                continue

            version = package.version or ""
            index_url = package.index or pypi_simple.PYPI_SIMPLE_ENDPOINT
            standard_index = pypi_simple.PYPI_SIMPLE_ENDPOINT.rstrip("/")
            use_proxy = proxy_url is not None and index_url.rstrip("/") == standard_index
            sdist = (
                ("sdist", package.sdist.url, package.sdist.hashes)
                if package.sdist is not None
                else None
            )

            artifacts: list[tuple[str, str, dict[str, str]]] = []
            if wheels_filter is not None and (
                wheels_filter.packages is None or name in wheels_filter.packages
            ):
                shims = [
                    pypi_simple.DistributionPackage(
                        filename=Path(urlparse.urlparse(wheel.url).path).name,
                        url=wheel.url,
                        project=None,
                        version=version,
                        package_type="wheel",
                        digests={},
                        requires_python=None,
                        has_sig=None,
                        is_yanked=False,
                    )
                    for wheel in package.wheels or []
                ]
                matched = {p.url for p in wheels_filter.filter(shims)} if shims else set()
                artifacts = [
                    ("wheel", wheel.url, wheel.hashes)
                    for wheel in package.wheels or []
                    if wheel.url in matched
                ]
                if wheels_filter.packages is not None:
                    # Binary-only packages require a matching wheel.
                    if not artifacts:
                        raise PackageRejected(
                            f"No wheels for '{name}=={version}' match the requested binary filters.",
                            solution="Adjust the binary filters or add a matching wheel to the lockfile.",
                        )
                elif sdist is not None:
                    # Fall back to the sdist when wheels are preferred.
                    artifacts.append(sdist)
            elif sdist is not None:
                # Use the sdist for source-only selections.
                artifacts = [sdist]

            if not artifacts:
                raise UnsupportedFeature(
                    f"Package '{name}' provides no source distribution (sdist), which Hermeto "
                    "needs to fetch it hermetically.",
                    solution="Regenerate the lockfile with an sdist, or request wheels via the "
                    "binary/allow_binary option.",
                )

            deps.extend(
                PyPIPackage(
                    name=name,
                    requirement_file=requirement_file,
                    missing_req_file_checksum=not hashes,
                    package_type=package_type,
                    version=version,
                    index_url=index_url,
                    url=(
                        patch_url_to_point_to_proxy(url, config.pip.proxy_url) if use_proxy else url
                    ),
                    checksums_to_match={
                        ChecksumInfo(algo, digest) for algo, digest in hashes.items()
                    },
                    auth_header=proxy_auth_header if use_proxy else None,
                    proxy_url=proxy_url if use_proxy else None,
                )
                for package_type, url, hashes in artifacts
            )

        return deps

    def rewrite(self, dependencies: list[PipPackage]) -> ProjectFile | None:
        """Rewrite vcs/archive source URLs to local file:// paths.

        Index (sdist/wheels) and source-less packages are left unchanged: index
        artifacts are installed via PIP_FIND_LINKS, not by their recorded URL.
        """
        data = self._pylock.document
        modified = False
        vcs_dependencies = {
            get_external_requirement_filepath(
                "vcs", f"git+{dependency.url}@{dependency.ref}", dependency.name, ""
            ): dependency
            for dependency in dependencies
            if isinstance(dependency, VCSPackage)
        }

        for pkg in data.get("packages", []):
            name = canonicalize_name(pkg["name"])
            if "vcs" in pkg:
                vcs = pkg["vcs"]
                path = get_external_requirement_filepath(
                    "vcs", f"git+{vcs['url']}@{vcs['commit-id']}", name, ""
                )
                vcs_dependency = vcs_dependencies.get(path)
                if vcs_dependency is None:
                    raise PackageRejected(
                        f"Downloaded VCS archive for '{name}' was not found.",
                        solution="Retry the dependency fetch so the lockfile can be rewritten.",
                    )
                archive = tomlkit.table()
                archive["url"] = f"file://{Path('${output_dir}', 'deps', 'pip', path)}"
                if subdirectory := vcs.get("subdirectory"):
                    archive["subdirectory"] = subdirectory
                pkg["archive"] = archive
                del pkg["vcs"]
                modified = True
            elif "archive" in pkg:
                archive = pkg["archive"]
                digest = next(iter(archive.get("hashes", {}).values()), "")
                path = get_external_requirement_filepath("url", archive["url"], name, digest)
                archive["url"] = f"file://{Path('${output_dir}', 'deps', 'pip', path)}"
                modified = True

        if not modified:
            return None

        return ProjectFile(
            abspath=Path(self._file_path).resolve(),
            template=tomlkit.dumps(data),
        )


_LOCKFILE_TYPES: dict[PythonPackagingTool, type[PipLockfile]] = {
    PythonPackagingTool.REQUIREMENTS: RequirementsLockfile,
    PythonPackagingTool.PYLOCK: PylockLockfile,
}
