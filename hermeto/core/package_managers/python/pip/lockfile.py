# SPDX-License-Identifier: GPL-3.0-only
"""Lockfile abstraction and shared download pipeline for pip packages."""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import cached_property, partial
from pathlib import Path
from typing import Any, ClassVar, Literal, NamedTuple
from urllib import parse as urlparse

import aiohttp
import pypi_simple
import requests.auth
import tomlkit
from packaging.utils import canonicalize_name

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import get_config
from hermeto.core.errors import (
    InvalidInput,
    LockfileNotFound,
    PackageRejected,
    PathOutsideRoot,
    UnsupportedFeature,
)
from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.models.output import ProjectFile
from hermeto.core.package_managers.general import (
    async_download_files,
    extract_git_info,
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
from hermeto.core.package_managers.python.pylock.models import (
    ArchiveArtifact,
    DirectorySource,
    Hashes,
    IndexUrl,
    Pylock,
    PylockPackage,
    RemoteSourceUrl,
    SdistArtifact,
    VCSSource,
    WheelArtifact,
)
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


class _ArtifactInfo(NamedTuple):
    package_type: Literal["sdist", "wheel"]
    url: RemoteSourceUrl
    hashes: Hashes


def _artifact_info(artifact: SdistArtifact | WheelArtifact) -> _ArtifactInfo:
    if isinstance(artifact, SdistArtifact):
        return _ArtifactInfo("sdist", artifact.url, artifact.hashes)
    return _ArtifactInfo("wheel", artifact.url, artifact.hashes)


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
    def from_file(cls, path: RootedPath, package_path: RootedPath) -> "PipLockfile":
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

    @abstractmethod
    def prepare_rewrite(self, dependencies: list[PipPackage]) -> ProjectFile | None:
        """Stage a lockfile rewrite so external URLs point at pre-fetched local files.

        Returns a ProjectFile with the rewritten content (not written to disk),
        or None when nothing needs rewriting.
        """


class RequirementsLockfile(PipLockfile):
    """Requirements.txt lockfile format."""

    default_file = "requirements.txt"
    default_build_file = "requirements-build.txt"

    def __init__(self, requirements_file: PipRequirementsFile, package_path: RootedPath) -> None:
        self._requirements_file = requirements_file
        self._package_path = package_path

    @classmethod
    def from_file(cls, path: RootedPath, package_path: RootedPath) -> "RequirementsLockfile":
        """Create lockfile from a requirements.txt file."""
        return cls(PipRequirementsFile(path), package_path)

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

    def prepare_rewrite(self, _: Any) -> ProjectFile | None:
        """Stage a rewrite of url/vcs requirement URLs to local file:// paths."""

        def maybe_replace(requirement: PipRequirement) -> PipRequirement:
            if requirement.kind in ("url", "vcs"):
                digest = requirement.hashes[0].partition(":")[2] if requirement.hashes else ""
                path = get_external_requirement_filepath(
                    requirement.kind,
                    RemoteSourceUrl(requirement.direct_access_url),
                    requirement.package,
                    digest,
                )
                templated_abspath = Path("${output_dir}", "deps", "pip", path)
                return requirement.update(url=f"file://{templated_abspath}")
            return requirement

        requirements = [maybe_replace(req) for req in self._requirements_file.requirements]
        if requirements == self._requirements_file.requirements:
            return None

        replaced_file = PipRequirementsFile.from_requirements_and_options(
            requirements, self._requirements_file.options
        )
        return ProjectFile(
            abspath=Path(self._requirements_file.file_path).resolve(),
            template=replaced_file.generate_file_content(),
        )

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
            return PylockLockfile
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
    package_path: RootedPath,
    deps_dir_name: str = "pip",
) -> tuple[list[PipPackage], list[ProjectFile]]:
    """Parse, validate, download dependencies from, and prepare rewrites for each lockfile.

    :param lockfile_type: concrete ``PipLockfile`` subclass used to parse each file
    :param files: lockfile paths to process (must exist under the request root)
    :param output_dir: request output directory; downloads go under ``deps/<deps_dir_name>``
    :param binary_filters: optional wheel selection filters, or ``None`` for source-only
    :param package_path: directory of the Hermeto package being processed
    :param deps_dir_name: subdirectory name under ``deps/`` for downloaded artifacts
    :returns: downloaded packages and staged lockfile rewrites (not written to disk)
    :raises LockfileNotFound: if any path in ``files`` does not exist
    """
    pip_deps_dir = output_dir.join_within_root("deps", deps_dir_name)
    pip_deps_dir.mkdir(parents=True, exist_ok=True)

    packages: list[PipPackage] = []
    project_files: list[ProjectFile] = []
    for file_path in files:
        if not file_path.exists():
            raise LockfileNotFound(
                files=file_path.path,
                solution="Please check that you have specified correct lockfile paths",
            )
        lockfile = lockfile_type.from_file(file_path, package_path)
        lockfile.validate()
        deps = lockfile.dependencies(binary_filters, pip_deps_dir)
        downloaded = _download_dependencies(deps, pip_deps_dir)
        packages.extend(downloaded)
        rewritten = lockfile.prepare_rewrite(downloaded)
        if rewritten is not None:
            project_files.append(rewritten)

    return packages, project_files


class PylockLockfile(PipLockfile):
    """PEP 751 pylock.toml lockfile format."""

    default_file = "pylock.toml"
    default_build_file = "pylock.build.toml"

    def __init__(self, pylock: Pylock, path: RootedPath, package_path: RootedPath) -> None:
        self._pylock = pylock
        self._path = path
        self._package_path = package_path

    @classmethod
    def from_file(cls, path: RootedPath, package_path: RootedPath) -> "PylockLockfile":
        """Create lockfile from a pylock.toml file."""
        return cls(Pylock.from_file(path), path, package_path)

    @property
    def path(self) -> RootedPath:
        """Return the pylock file path."""
        return self._path

    @property
    def _requirement_file(self) -> str:
        return str(self._path.subpath_from_root)

    def dependencies(
        self, binary_filters: PipBinaryFilters | None, _pip_deps_dir: RootedPath
    ) -> list[PipPackage]:
        """Extract dependencies from pylock.toml (no download, just build dep objects).

        ``_pip_deps_dir`` is unused: pylock records an explicit artifact URL for
        every dependency, so no index resolution (which is what needs the deps
        dir) happens here. For the same reason the configured pip proxy is not
        applied -- the proxy stands in for the PyPI index during resolution, and
        pylock never resolves. Artifacts are fetched directly from the pinned
        URLs, consistent with the ``archive``/``vcs`` sources below.
        """
        wheels_filter = WheelsFilter(binary_filters) if binary_filters is not None else None
        return [
            dep
            for package in self._pylock.packages
            for dep in self._packages_for(package, wheels_filter)
        ]

    def _packages_for(
        self, package: PylockPackage, wheels_filter: WheelsFilter | None
    ) -> list[PipPackage]:
        """Convert one pylock package entry into zero or more fetchable PipPackage objects."""
        name = canonicalize_name(package.name)

        if package.directory is not None:
            self._reject_unless_own_project(name, package.directory)
            return []
        if package.vcs is not None:
            return [self._vcs_package(name, package.vcs)]
        if package.archive is not None:
            return [self._archive_package(name, package.archive)]
        return self._index_packages(name, package, wheels_filter)

    def _reject_unless_own_project(self, name: str, directory: DirectorySource) -> None:
        """Skip the package being processed; reject every other local directory."""
        try:
            lockfile_dir = RootedPath(self._path.root).join_within_root(
                self._path.path.parent.relative_to(self._path.root)
            )
            resolved = lockfile_dir.join_within_root(directory.path)
        except PathOutsideRoot:
            resolved = None

        if resolved is None or resolved.path != self._package_path.path:
            raise PackageRejected(
                f"Package '{name}' is a local directory dependency, which Hermeto cannot fetch.",
                solution="Depend on a released version from an index or a URL instead.",
            )

    def _vcs_package(self, name: str, vcs: VCSSource) -> VCSPackage:
        return VCSPackage(
            name=name,
            requirement_file=self._requirement_file,
            missing_req_file_checksum=True,
            package_type="",
            url=vcs.url,
            ref=vcs.commit_id,
        )

    def _archive_package(self, name: str, archive: ArchiveArtifact) -> URLPackage:
        archive_hashes = list(archive.hashes.items())
        checksum = f"{archive_hashes[0][0]}:{archive_hashes[0][1]}" if archive_hashes else ""
        archive_path = archive.url.path or ""
        return URLPackage(
            name=name,
            requirement_file=self._requirement_file,
            missing_req_file_checksum=False,
            package_type=("wheel" if archive_path.endswith(WHEEL_FILE_EXTENSION) else ""),
            url=archive.url,
            checksum=checksum,
            insecure=False,
            checksums_to_match={ChecksumInfo(algo, digest) for algo, digest in archive_hashes},
        )

    def _select_index_artifacts(
        self,
        name: str,
        version: str,
        package: PylockPackage,
        wheels_filter: WheelsFilter | None,
    ) -> list[_ArtifactInfo]:
        """Pick the sdist/wheel artifacts to fetch for an index package."""
        sdist = _artifact_info(package.sdist) if package.sdist is not None else None
        policy = (
            wheels_filter.package_policy(name)
            if wheels_filter is not None
            else WheelsFilter.PackageFetchPolicy.NO_BINARY
        )

        if wheels_filter is None or policy is WheelsFilter.PackageFetchPolicy.NO_BINARY:
            return [sdist] if sdist is not None else []

        shims = [
            pypi_simple.DistributionPackage(
                filename=Path(wheel.url.path or "").name,
                url=str(wheel.url),
                project=None,
                version=version,
                package_type="wheel",
                digests={},
                requires_python=None,
                has_sig=None,
                is_yanked=False,
            )
            for wheel in package.wheel_artifacts
        ]
        matched_wheel_urls = {p.url for p in wheels_filter.filter(shims)}
        artifacts = [
            _artifact_info(wheel)
            for wheel in package.wheel_artifacts
            if str(wheel.url) in matched_wheel_urls
        ]

        if policy is WheelsFilter.PackageFetchPolicy.ONLY_BINARY:
            # Binary-only packages require a matching wheel.
            if not artifacts:
                raise PackageRejected(
                    f"No wheels for '{name}=={version}' match the requested binary filters.",
                    solution="Adjust the binary filters or add a matching wheel to the lockfile.",
                )
            return artifacts

        # Fall back to the sdist when wheels are preferred.
        if sdist is not None:
            artifacts.append(sdist)
        return artifacts

    def _index_packages(
        self,
        name: str,
        package: PylockPackage,
        wheels_filter: WheelsFilter | None,
    ) -> list[PipPackage]:
        version = package.version or ""
        index_url = (
            str(package.index) if package.index is not None else pypi_simple.PYPI_SIMPLE_ENDPOINT
        )
        artifacts = self._select_index_artifacts(name, version, package, wheels_filter)

        if not artifacts:
            raise UnsupportedFeature(
                f"Package '{name}' provides no source distribution (sdist), which Hermeto "
                "needs to fetch it hermetically.",
                solution="Regenerate the lockfile with an sdist, or request wheels via the "
                "binary/allow_binary option.",
            )

        return [
            PyPIPackage(
                name=name,
                requirement_file=self._requirement_file,
                missing_req_file_checksum=not hashes,
                package_type=package_type,
                version=version,
                index_url=index_url,
                url=url,
                checksums_to_match={ChecksumInfo(algo, digest) for algo, digest in hashes.items()},
            )
            for package_type, url, hashes in artifacts
        ]

    def prepare_rewrite(self, dependencies: list[PipPackage]) -> ProjectFile | None:
        """Stage a rewrite of vcs/archive source URLs to local file:// paths.

        Index (sdist/wheels) and source-less packages are left unchanged: index
        artifacts are installed via PIP_FIND_LINKS, not by their recorded URL.
        """

        def vcs_download_path(name: str, url: RemoteSourceUrl | str, ref: str) -> Path:
            """Relative path under deps/pip/ for a VCS package, matching VCSPackage.download."""
            return get_external_requirement_filepath(
                "vcs", RemoteSourceUrl(f"git+{url}@{ref}"), name, ""
            )

        def index_vcs_packages_by_download_path() -> dict[Path, VCSPackage]:
            indexed: dict[Path, VCSPackage] = {}
            for dependency in dependencies:
                if isinstance(dependency, VCSPackage):
                    path = vcs_download_path(dependency.name, dependency.url, dependency.ref)
                    indexed[path] = dependency
            return indexed

        data = self._pylock.document
        modified = False
        vcs_dependencies = index_vcs_packages_by_download_path()

        for pkg in data.get("packages", []):
            name = canonicalize_name(pkg["name"])
            if "vcs" in pkg:
                vcs = pkg["vcs"]
                path = vcs_download_path(name, vcs["url"], vcs["commit-id"])
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
                path = get_external_requirement_filepath(
                    "url", RemoteSourceUrl(archive["url"]), name, digest
                )
                archive["url"] = f"file://{Path('${output_dir}', 'deps', 'pip', path)}"
                modified = True

        if not modified:
            return None

        return ProjectFile(
            abspath=Path(self._path).resolve(),
            template=tomlkit.dumps(data),
        )


LOCKFILE_TYPES: dict[PythonPackagingTool, type[PipLockfile]] = {
    PythonPackagingTool.REQUIREMENTS: RequirementsLockfile,
    PythonPackagingTool.PYLOCK: PylockLockfile,
}
