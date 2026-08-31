# SPDX-License-Identifier: GPL-3.0-only
"""Lockfile abstraction and shared download pipeline for pip packages."""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import cached_property, partial
from pathlib import Path
from typing import Any, ClassVar, NamedTuple
from urllib import parse as urlparse

import aiohttp
import pypi_simple
import requests.auth

from hermeto.core.config import get_config
from hermeto.core.errors import InvalidInput, LockfileNotFound
from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.package_managers.general import async_download_files, extract_git_info
from hermeto.core.package_managers.python.packaging_tool import PythonPackagingTool
from hermeto.core.package_managers.python.pip.package_distributions import (
    DistributionPackageInfo,
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
    process_requirements_options,
    validate_requirements,
    validate_requirements_hashes,
)
from hermeto.core.package_managers.python.pylock.models import (
    IndexUrl,
    RemoteSourceUrl,
)
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


class _ProxyAccess(NamedTuple):
    """How to reach a PyPI index, including optional proxy credentials."""

    query_url: str
    proxy_url: str | None
    auth: requests.auth.HTTPBasicAuth | None

    @property
    def auth_header(self) -> str | None:
        if self.auth is None:
            return None
        return aiohttp.encode_basic_auth(str(self.auth.username), str(self.auth.password))

    def proxy_to_report(self, index_url: str) -> str | None:
        # If a standard PyPI index is used with proxy URL then proxy URL must be reported,
        # if a custom index is used then proxy URL must not be reported even if set.
        # https://github.com/hermetoproject/hermeto/issues/1834
        is_standard = bool(index_url) and index_url == pypi_simple.PYPI_SIMPLE_ENDPOINT
        if self.proxy_url is not None and is_standard:
            return self.proxy_url
        return None


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

    other_processed = _download_other_deps(other_deps, pip_deps_dir)
    pypi_processed = _download_pypi_deps(pypi_deps, pip_deps_dir)
    return other_processed + pypi_processed


def _download_other_deps(
    other_deps: list[PipPackage], pip_deps_dir: RootedPath
) -> list[PipPackage]:
    processed: list[PipPackage] = []

    for dep in other_deps:
        log.info("-- Processing %s", dep.name)
        dep.download(pip_deps_dir)
        if dep.verify():
            processed.append(dep)
        log.info("-- Finished processing %s", dep.name)

    return processed


def _download_pypi_deps(
    pypi_deps: list[PyPIPackage], pip_deps_dir: RootedPath
) -> list[PyPIPackage]:
    files_to_download: dict[str, Path] = {}
    headers_by_url: dict[str, dict[str, str]] = {}

    for pypi_dep in pypi_deps:
        download_loc = pypi_dep.download_location(pip_deps_dir)
        if not download_loc.exists():
            files_to_download[pypi_dep.remote_location] = download_loc.path
            if pypi_dep.auth_header:
                headers_by_url[pypi_dep.remote_location] = {"Authorization": pypi_dep.auth_header}
        pypi_dep.path = download_loc.path

    if files_to_download:
        log.info("Downloading %d PyPI artifacts", len(files_to_download))
        asyncio.run(
            async_download_files(
                files_to_download,
                get_config().runtime.concurrency_limit,
                headers=headers_by_url,
            )
        )

    return [p for p in pypi_deps if p.verify()]


class PipLockfile(ABC):
    """Abstract base for pip lockfile formats."""

    default_file: ClassVar[str]
    default_build_file: ClassVar[str]

    @classmethod
    @abstractmethod
    def from_file(cls, path: RootedPath) -> "PipLockfile":
        """Create a lockfile instance from a file."""

    @property
    @abstractmethod
    def path(self) -> RootedPath:
        """Return the lockfile path."""

    @abstractmethod
    def dependencies(
        self, binary_filters: PipBinaryFilters | None, pip_deps_dir: RootedPath
    ) -> list[PipPackage]:
        """Extract dependencies as PipPackage objects (not downloaded yet)."""

    def validate(self) -> None:
        """Validate the lockfile before downloading. No-op by default."""


class RequirementsLockfile(PipLockfile):
    """Requirements.txt lockfile format."""

    default_file = "requirements.txt"
    default_build_file = "requirements-build.txt"

    def __init__(self, requirements_file: PipRequirementsFile) -> None:
        self._requirements_file = requirements_file

    @classmethod
    def from_file(cls, path: RootedPath) -> "RequirementsLockfile":
        """Create lockfile from a requirements.txt file."""
        return cls(PipRequirementsFile(path))

    @property
    def path(self) -> RootedPath:
        """Return the requirements file path."""
        return self._requirements_file.file_path

    @cached_property
    def _options(self) -> dict[str, Any]:
        return process_requirements_options(self._requirements_file.options)

    @property
    def trusted_hosts(self) -> set[str]:
        """Return trusted hosts from --trusted-host options."""
        return set(self._options["trusted_hosts"])

    @cached_property
    def _deps(self) -> tuple[list[PipRequirement], list[PipPackage]]:
        """Return a tuple of (pypi requirements, other requirements)."""
        requirement_file = str(self.path.subpath_from_root)

        pypi_deps = []
        other_deps: list[PipPackage] = []
        for req in self._requirements_file.requirements:
            if req.kind == "pypi":
                pypi_deps.append(req)
            elif req.kind == "vcs":
                git_info = extract_git_info(req.direct_access_url)
                other_deps.append(
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
                    and f"{parsed_url.hostname}:{parsed_url.port}" in self.trusted_hosts
                ) or parsed_url.hostname in self.trusted_hosts

                other_deps.append(
                    URLPackage(
                        name=req.package,
                        requirement_file=requirement_file,
                        missing_req_file_checksum=not bool(req.hashes),
                        package_type="wheel"
                        if parsed_url.path.endswith(WHEEL_FILE_EXTENSION)
                        else "",
                        url=RemoteSourceUrl(req.direct_access_url),
                        checksum=req.hashes[0] if req.hashes else "",
                        insecure=insecure,
                    )
                )

        return pypi_deps, other_deps

    def validate(self) -> None:
        """Validate requirements and hashes."""
        require_hashes = False
        if self._options["require_hashes"]:
            log.info("Global --require-hashes option used, will require hashes")
            require_hashes = True
        elif any(req.hashes for req in self._requirements_file.requirements):
            log.info("At least one dependency uses the --hash option, will require hashes")
            require_hashes = True
        else:
            log.info(
                "No hash options used, will not require hashes unless HTTP(S) dependencies are present."
            )

        validate_requirements(self._requirements_file.requirements)
        validate_requirements_hashes(self._requirements_file.requirements, require_hashes)

    def dependencies(
        self, binary_filters: PipBinaryFilters | None, pip_deps_dir: RootedPath
    ) -> list[PipPackage]:
        """Extract dependencies from requirements.txt (no download, just build dep objects)."""
        pypi_deps, deps = self._deps
        deps += self._resolve_pypi_deps(pypi_deps, pip_deps_dir, binary_filters)
        return deps

    def _get_index_url(self) -> IndexUrl:
        if self._options["index_url"]:
            return IndexUrl.from_config(self._options["index_url"], "--index-url")
        elif pip_index_url := os.environ.get("PIP_INDEX_URL", "").strip():
            index_url = IndexUrl.from_config(pip_index_url, "PIP_INDEX_URL")
            log.info(
                "Using PIP_INDEX_URL='%s' (no --index-url in requirements file)",
                pip_index_url,
            )
            return index_url
        return IndexUrl.model_validate(pypi_simple.PYPI_SIMPLE_ENDPOINT)

    def _get_proxy_access(self, index_url: IndexUrl) -> _ProxyAccess:
        config = get_config()
        proxy_url = str(config.pip.proxy_url) if config.pip.proxy_url is not None else None
        # A proxy must be used only if an index is the standard one, custom indices must
        # be preserved. A custom index would always override proxy.
        index_url_str = str(index_url)
        is_standard = index_url_str == pypi_simple.PYPI_SIMPLE_ENDPOINT
        query_url = proxy_url if (proxy_url is not None and is_standard) else index_url_str
        if query_url == proxy_url:
            log.info("Using registry proxy %s for registry dependencies", proxy_url)
        auth = None
        # No attempt should be made at logging into custom indices, if query URL ends up pointing
        # to a custom index then authorization must be skipped.
        if config.pip.proxy_login and config.pip.proxy_password and (query_url == proxy_url):
            auth = requests.auth.HTTPBasicAuth(
                config.pip.proxy_login, config.pip.proxy_password.get_secret_value()
            )

        return _ProxyAccess(query_url, proxy_url, auth)

    def _resolve_pypi_deps(
        self,
        pypi_deps: list[PipRequirement],
        pip_deps_dir: RootedPath,
        binary_filters: PipBinaryFilters | None,
    ) -> list[PyPIPackage]:
        index_url = self._get_index_url()

        if not pypi_deps:
            return []

        requirement_file = str(self.path.subpath_from_root)
        proxy = self._get_proxy_access(index_url)

        resolve_callback = partial(
            process_package_distributions,
            pip_deps_dir=pip_deps_dir,
            binary_filters=binary_filters,
            index_url=proxy.query_url,
            auth=proxy.auth,
        )
        pypi_dpis = asyncio.run(_resolve_pypi_distributions(pypi_deps, resolve_callback))
        index_url_str = str(index_url)

        return [
            PyPIPackage(
                name=dpi.name,
                requirement_file=requirement_file,
                missing_req_file_checksum=not bool(dpi.req_file_checksums),
                package_type=dpi.package_type,
                version=dpi.version,
                index_url=index_url_str,
                url=RemoteSourceUrl(dpi.url),
                checksums_to_match=dpi.checksums_to_match,
                auth_header=proxy.auth_header,
                proxy_url=proxy.proxy_to_report(index_url_str),
            )
            for dpis in pypi_dpis
            for dpi in dpis
        ]


def infer_lockfile_type(
    packaging_tool: PythonPackagingTool | None, lockfile: Path | None
) -> type[PipLockfile] | None:
    """Pick the lockfile format from explicit input or the lockfile filename.

    An explicit ``packaging_tool`` always wins; otherwise the format is inferred
    from the ``lockfile`` filename, and everything else falls back to requirements.
    """
    if packaging_tool is not None:
        return LOCKFILE_TYPES.get(packaging_tool)
    if lockfile is not None:
        name = lockfile.name
        if name.endswith(".txt"):
            return RequirementsLockfile
        if name.startswith("pylock.") and name.endswith(".toml"):
            return None
        raise InvalidInput(
            f"Cannot determine the lockfile format from the filename {name!r}",
            solution="Name the lockfile 'requirements.txt' or 'pylock.toml', or set "
            "'packaging_tool' explicitly (e.g. 'requirements' or 'pylock').",
        )
    return RequirementsLockfile


def resolve_lockfile_paths(
    package_path: RootedPath, explicit_paths: list[Path] | None, default_filename: str
) -> list[RootedPath]:
    """Resolve explicit lockfile paths, or auto-discover the default file if present."""
    if explicit_paths is not None:
        return [package_path.join_within_root(p) for p in explicit_paths]
    default = package_path.join_within_root(default_filename)
    return [default] if default.is_file() else []


def process_lockfiles(
    lockfile_type: type[PipLockfile],
    files: list[RootedPath],
    output_dir: RootedPath,
    binary_filters: PipBinaryFilters | None,
    deps_dir_name: str = "pip",
) -> list[PipPackage]:
    """Parse, validate, download dependencies from, and prepare rewrites for each lockfile."""
    pip_deps_dir = output_dir.join_within_root("deps", deps_dir_name)
    pip_deps_dir.mkdir(parents=True, exist_ok=True)

    packages: list[PipPackage] = []
    for file_path in files:
        if not file_path.exists():
            raise LockfileNotFound(
                files=file_path.path,
                solution="Please check that you have specified correct lockfile paths",
            )
        lockfile = lockfile_type.from_file(file_path)
        lockfile.validate()
        deps = lockfile.dependencies(binary_filters, pip_deps_dir)
        downloaded = _download_dependencies(deps, pip_deps_dir)
        packages.extend(downloaded)

    return packages


LOCKFILE_TYPES: dict[PythonPackagingTool, type[PipLockfile]] = {
    PythonPackagingTool.REQUIREMENTS: RequirementsLockfile
}
