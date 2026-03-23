# SPDX-License-Identifier: GPL-3.0-only
"""UV package manager backend for Hermeto.

This backend reads uv.lock files directly using tomllib, classifies packages
by source type, downloads artifacts, verifies checksums, and generates SBOM components.
No uv binary is required at runtime.
"""
import asyncio
import logging
import os
import re
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
from pathlib import Path
from urllib.parse import urlparse

from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import FetchError, PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, RequestOutput
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.general import async_download_files, download_binary_file
from hermeto.core.package_managers.uv.lockfile import (
    SourceType,
    UvLockfile,
    UvPackage,
    parse_uv_lockfile,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE_NAME = "uv.lock"
DEFAULT_DEPS_DIR = "deps/uv"


def fetch_uv_source(request: Request) -> RequestOutput:
    """Resolve and fetch uv dependencies for a given request.

    Main entry point for the uv backend. Called by the resolver.

    :param request: the request to process
    :return: RequestOutput with components, env vars, and annotations
    """
    components: list[Component] = []

    for package in request.uv_packages:
        info = _process_uv_package(
            source_dir=request.source_dir,
            output_dir=request.output_dir,
            package_path=package.path,
            lockfile_path=package.lockfile,
            include_dev=package.include_dev,
        )
        components.extend(info)

    annotations = []
    if backend_annotation := create_backend_annotation(components, "uv"):
        annotations.append(backend_annotation)

    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=[
            EnvironmentVariable(name="UV_FIND_LINKS", value="${output_dir}/" + DEFAULT_DEPS_DIR),
        ],
        annotations=annotations,
    )


def _process_uv_package(
    source_dir: RootedPath,
    output_dir: RootedPath,
    package_path: Path,
    lockfile_path: Path | None,
    include_dev: bool,
) -> list[Component]:
    """Process a single uv package input.

    :param source_dir: root source directory
    :param output_dir: output directory for downloaded artifacts
    :param package_path: relative path to the uv project within source_dir
    :param lockfile_path: optional override path to uv.lock
    :param include_dev: whether to include dev dependencies
    :return: list of SBOM components
    """
    project_dir = source_dir.join_within_root(package_path)

    # Resolve lockfile path
    if lockfile_path:
        resolved_lockfile = project_dir.join_within_root(lockfile_path).path
    else:
        resolved_lockfile = project_dir.join_within_root(DEFAULT_LOCKFILE_NAME).path

    # Parse the lockfile
    lockfile = parse_uv_lockfile(resolved_lockfile)

    # Validate lockfile against pyproject.toml
    pyproject_path = project_dir.join_within_root("pyproject.toml").path
    if pyproject_path.exists():
        _validate_lockfile_freshness(pyproject_path, lockfile)

    # Filter packages
    packages_to_process = _filter_packages(lockfile, include_dev)

    # Set up output directory
    deps_dir = output_dir.re_root(DEFAULT_DEPS_DIR)

    # Process by source type
    components = _process_all_packages(packages_to_process, deps_dir, lockfile)

    return components


def _validate_lockfile_freshness(pyproject_path: Path, lockfile: UvLockfile) -> None:
    """Pure-Python staleness check: compare pyproject.toml deps against lockfile.

    This avoids running 'uv lock --check' which can execute arbitrary code.

    :param pyproject_path: path to pyproject.toml
    :param lockfile: parsed lockfile
    :raises PackageRejected: if lockfile appears stale
    """
    try:
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        log.warning("Could not parse pyproject.toml for staleness check, skipping.")
        return

    # Collect declared dependency names from pyproject.toml
    declared_deps: set[str] = set()

    # [project.dependencies]
    for dep in pyproject.get("project", {}).get("dependencies", []):
        # Extract package name from PEP 508 string (e.g., "requests>=2.32" -> "requests")
        name = re.split(r"[>=<!\[;@ ]", dep)[0].strip().lower()
        if name:
            declared_deps.add(name)

    # [dependency-groups] (PEP 735)
    for group, deps in pyproject.get("dependency-groups", {}).items():
        for dep in deps:
            if isinstance(dep, str):
                name = re.split(r"[>=<!\[;@ ]", dep)[0].strip().lower()
                if name:
                    declared_deps.add(name)

    if not declared_deps:
        return

    # Collect package names from lockfile
    locked_names = {pkg.name.lower() for pkg in lockfile.packages}

    # Check that every declared dep exists in lockfile
    missing = declared_deps - locked_names
    if missing:
        raise PackageRejected(
            f"Lockfile appears out of date. The following dependencies from pyproject.toml "
            f"are not in uv.lock: {', '.join(sorted(missing))}",
            solution="Run 'uv lock' to update the lockfile.",
        )


def _filter_packages(lockfile: UvLockfile, include_dev: bool) -> list[UvPackage]:
    """Filter packages based on source type and dev inclusion.

    :param lockfile: parsed lockfile
    :param include_dev: whether to include dev-only packages
    :return: filtered list of packages to process
    """
    # Identify dev-only packages if needed
    dev_only_names: set[str] = set()
    if not include_dev:
        dev_only_names = _get_dev_only_packages(lockfile)

    result = []
    for pkg in lockfile.packages:
        # Skip editable/virtual packages (project itself)
        if pkg.is_editable:
            log.debug("Skipping editable/virtual package: %s", pkg.name)
            continue

        # Reject path dependencies
        if pkg.is_path:
            raise PackageRejected(
                f"Path dependency '{pkg.name}' is not supported. "
                f"Path dependencies are non-reproducible and may execute arbitrary code.",
                solution=(
                    "Convert the path dependency to a registry (PyPI) dependency "
                    "or a git source. See uv documentation for alternatives."
                ),
            )

        # Skip dev-only packages
        if pkg.name.lower() in dev_only_names:
            log.debug("Skipping dev-only package: %s", pkg.name)
            continue

        result.append(pkg)

    return result


def _get_dev_only_packages(lockfile: UvLockfile) -> set[str]:
    """Walk dependency graph to find packages reachable ONLY through dev paths.

    A package is dev-only if it's NOT reachable through any production dependency path.

    :param lockfile: parsed lockfile
    :return: set of package names that are dev-only
    """
    # Find the project's own package (editable/virtual)
    project_pkg = None
    for pkg in lockfile.packages:
        if pkg.is_editable and pkg.dev_dependencies:
            project_pkg = pkg
            break

    if not project_pkg:
        return set()

    # Build a lookup from name to package
    pkg_by_name: dict[str, UvPackage] = {pkg.name.lower(): pkg for pkg in lockfile.packages}

    # Collect direct prod dependency names
    prod_direct = {dep.name.lower() for dep in project_pkg.dependencies}

    # Collect direct dev dependency names
    dev_direct: set[str] = set()
    for group_deps in project_pkg.dev_dependencies.values():
        for dep in group_deps:
            dev_direct.add(dep.name.lower())

    # Walk transitive prod deps (BFS)
    prod_reachable: set[str] = set()
    queue = list(prod_direct)
    while queue:
        name = queue.pop(0)
        if name in prod_reachable:
            continue
        prod_reachable.add(name)
        pkg = pkg_by_name.get(name)
        if pkg:
            for dep in pkg.dependencies:
                if dep.name.lower() not in prod_reachable:
                    queue.append(dep.name.lower())

    # Walk transitive dev deps (BFS)
    dev_reachable: set[str] = set()
    queue = list(dev_direct)
    while queue:
        name = queue.pop(0)
        if name in dev_reachable:
            continue
        dev_reachable.add(name)
        pkg = pkg_by_name.get(name)
        if pkg:
            for dep in pkg.dependencies:
                if dep.name.lower() not in dev_reachable:
                    queue.append(dep.name.lower())

    # Dev-only = reachable through dev paths but NOT through prod paths
    dev_only = dev_reachable - prod_reachable
    log.debug("Dev-only packages: %s", dev_only)
    return dev_only


def _process_all_packages(
    packages: list[UvPackage],
    deps_dir: RootedPath,
    lockfile: UvLockfile,
) -> list[Component]:
    """Route each package to the correct download handler by source type.

    :param packages: packages to process
    :param deps_dir: output directory for downloads
    :param lockfile: full lockfile (for context)
    :return: list of SBOM components
    """
    components: list[Component] = []

    # Batch registry downloads for concurrency
    registry_packages: list[UvPackage] = []
    to_download: dict[str, str | os.PathLike[str]] = {}

    for pkg in packages:
        if pkg.is_registry:
            registry_packages.append(pkg)
            _prepare_registry_download(pkg, deps_dir, to_download)
        elif pkg.is_git:
            component = _process_git_package(pkg, deps_dir)
            components.append(component)
        elif pkg.is_url:
            component = _process_url_package(pkg, deps_dir)
            components.append(component)

    # Execute batch download for all registry packages
    if to_download:
        log.info("Downloading %d registry artifacts...", len(to_download))
        asyncio.run(
            async_download_files(to_download, get_config().runtime.concurrency_limit)
        )

        # Verify checksums and create components for registry packages
        for pkg in registry_packages:
            pkg_components = _verify_and_create_registry_components(pkg, deps_dir)
            components.extend(pkg_components)

    return components


def _prepare_registry_download(
    pkg: UvPackage,
    deps_dir: RootedPath,
    to_download: dict[str, str | os.PathLike[str]],
) -> None:
    """Prepare download entries for a registry package's artifacts.

    By default downloads sdists. All wheels are also collected for download
    since Hermeto does platform-agnostic prefetching.

    :param pkg: the registry package
    :param deps_dir: output directory
    :param to_download: dict to accumulate {url: output_path} entries
    """
    # Download sdist if available
    if pkg.sdist and pkg.sdist.url:
        filename = _url_to_filename(pkg.sdist.url)
        output_path = deps_dir.join_within_root(filename).path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        to_download[pkg.sdist.url] = str(output_path)

    # Download all wheels (platform-agnostic prefetch)
    for wheel in pkg.wheels:
        if wheel.url:
            filename = _url_to_filename(wheel.url)
            output_path = deps_dir.join_within_root(filename).path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            to_download[wheel.url] = str(output_path)


def _verify_and_create_registry_components(
    pkg: UvPackage,
    deps_dir: RootedPath,
) -> list[Component]:
    """Verify checksums and create SBOM components for a registry package.

    :param pkg: the registry package
    :param deps_dir: output directory where files were downloaded
    :return: list of components (one per package)
    """
    # Verify sdist checksum
    if pkg.sdist and pkg.sdist.url and pkg.sdist.hash:
        filename = _url_to_filename(pkg.sdist.url)
        filepath = deps_dir.join_within_root(filename).path
        if filepath.exists():
            checksum = _parse_hash(pkg.sdist.hash)
            must_match_any_checksum(filepath, [checksum])

    # Verify wheel checksums
    for wheel in pkg.wheels:
        if wheel.url and wheel.hash:
            filename = _url_to_filename(wheel.url)
            filepath = deps_dir.join_within_root(filename).path
            if filepath.exists():
                checksum = _parse_hash(wheel.hash)
                must_match_any_checksum(filepath, [checksum])

    # Create component
    purl = PackageURL(
        type="pypi",
        name=pkg.name,
        version=pkg.version,
    ).to_string()

    return [Component(name=pkg.name, version=pkg.version, purl=purl)]


def _process_git_package(pkg: UvPackage, deps_dir: RootedPath) -> Component:
    """Process a git dependency: clone at pinned commit and create tarball.

    :param pkg: the git package
    :param deps_dir: output directory
    :return: SBOM component
    """
    git_url = pkg.source.url or ""

    # Parse uv's git URL format: https://github.com/org/repo.git?tag=X#commitsha
    repo_url, commit_ref = _parse_git_source_url(git_url)

    tarball_name = f"{pkg.name}-{pkg.version}.tar.gz"
    tarball_path = deps_dir.join_within_root(tarball_name).path
    tarball_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("Cloning git dependency: %s @ %s", repo_url, commit_ref)
    clone_as_tarball(repo_url, commit_ref, tarball_path)

    # Generate PURL with vcs_url qualifier
    purl = PackageURL(
        type="pypi",
        name=pkg.name,
        version=pkg.version,
        qualifiers={"vcs_url": f"git+{repo_url}@{commit_ref}"},
    ).to_string()

    return Component(name=pkg.name, version=pkg.version, purl=purl)


def _process_url_package(pkg: UvPackage, deps_dir: RootedPath) -> Component:
    """Process a direct URL dependency: download and verify.

    :param pkg: the URL package
    :param deps_dir: output directory
    :return: SBOM component
    """
    url = pkg.source.url or ""
    filename = _url_to_filename(url)
    download_path = deps_dir.join_within_root(filename).path
    download_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("Downloading URL dependency: %s", url)
    download_binary_file(url, download_path)

    # Verify checksum if available (URL deps have hashes in wheels section)
    for wheel in pkg.wheels:
        if wheel.hash:
            checksum = _parse_hash(wheel.hash)
            must_match_any_checksum(download_path, [checksum])
            break

    # Generate PURL with download_url qualifier
    purl = PackageURL(
        type="pypi",
        name=pkg.name,
        version=pkg.version,
        qualifiers={"download_url": url},
    ).to_string()

    return Component(name=pkg.name, version=pkg.version, purl=purl)


# --- Utility functions ---


def _parse_hash(hash_str: str) -> ChecksumInfo:
    """Parse a uv.lock hash string (e.g., 'sha256:abc123') into ChecksumInfo."""
    return ChecksumInfo.from_hash(hash_str)


def _url_to_filename(url: str) -> str:
    """Extract filename from a URL."""
    parsed = urlparse(url)
    return Path(parsed.path).name


def _parse_git_source_url(git_url: str) -> tuple[str, str]:
    """Parse uv's git source URL format into (repo_url, commit_ref).

    Format: https://github.com/org/repo.git?tag=X#commitsha

    :param git_url: the raw git URL from uv.lock
    :return: (repo_url, commit_ref)
    """
    # Split on # to get commit hash fragment
    if "#" in git_url:
        base_url, commit_ref = git_url.rsplit("#", 1)
    else:
        base_url = git_url
        commit_ref = "HEAD"

    # Remove query params (?tag=X, ?rev=X, ?branch=X)
    if "?" in base_url:
        repo_url = base_url.split("?")[0]
    else:
        repo_url = base_url

    return repo_url, commit_ref
