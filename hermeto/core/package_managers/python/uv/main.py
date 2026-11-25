# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import logging
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, cast

import aiohttp
import more_itertools
import pypi_simple
import requests
import tomlkit
from packageurl import PackageURL
from tomlkit.items import InlineTable
from typing_extensions import assert_never

from hermeto.core.checksum import must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import (
    NotAGitRepo,
    PackageRejected,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import (
    PROXY_COMMENT,
    PROXY_REF_TYPE,
    Annotation,
    Component,
    ExternalReference,
    create_backend_annotation,
)
from hermeto.core.package_managers.general import async_download_files, get_vcs_qualifiers
from hermeto.core.package_managers.python.common import (
    get_project_packages_from,
    make_proxy_override,
)
from hermeto.core.package_managers.python.pip.project_files import PyProjectTOML
from hermeto.core.package_managers.python.uv.build_deps import (
    download_build_dependencies,
    to_component,
)
from hermeto.core.package_managers.python.uv.models import (
    _WHEEL_LOCATIONS,
    ArtifactWheel,
    PackageArtifact,
    PackageSourceGit,
    PackageSourceLocal,
    PackageSourceRegistry,
    PackageSourceUrl,
    UvLock,
    UvPackage,
    load_lockfile_document,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE_NAME = "uv.lock"

# string.Template placeholder resolved by ProjectFile.resolve_content at inject-files time
_TEMPLATED_DEPS_DIR = "${output_dir}/deps/uv"

Url = str


class _DownloadItem(NamedTuple):
    package: UvPackage
    artifact: PackageArtifact
    target: Path


@dataclass
class UvResolutionResult:
    """Everything fetch-deps produced for a single uv project."""

    name: str
    version: str | None
    components: list[Component]
    rewritten_lockfile: ProjectFile | None


def fetch_uv_source(request: Request) -> RequestOutput:
    """Resolve and fetch uv dependencies for the given request."""
    annotations: list[Annotation] = []
    components: list[Component] = []
    project_files: list[ProjectFile] = []

    for package in request.uv_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        resolution_result = _resolve_uv(package_dir, request.output_dir)
        components.extend(resolution_result.components)
        if resolution_result.rewritten_lockfile is not None:
            project_files.append(resolution_result.rewritten_lockfile)

    if backend_annotation := create_backend_annotation(components, "x-uv"):
        annotations.append(backend_annotation)

    environment_variables = _generate_environment_variables()

    return RequestOutput.from_obj_list(
        components, environment_variables, project_files, annotations=annotations
    )


# NOTE: it is assumed that this function is used only when authentication is necessary,
#       it knows nothing about that and will always provide credentials when possible.
def make_auth_object(maker: Callable) -> Any | None:
    proxy_override = make_proxy_override("uv")
    if proxy_override is None or proxy_override.proxy_login is None:
        return None
    return maker(proxy_override.proxy_login, proxy_override.proxy_password)


def get_requests_auth() -> requests.auth.HTTPBasicAuth | None:
    return make_auth_object(requests.auth.HTTPBasicAuth)


def get_aiohttp_auth() -> str | None:
    return make_auth_object(aiohttp.encode_basic_auth)


def get_packages_available_on_proxy(
    name: str, version: str | None
) -> list[pypi_simple.DistributionPackage]:
    config = get_config()
    proxy_url = str(config.uv.proxy_url)
    if config.uv.proxy_login:
        return list(get_project_packages_from(proxy_url, name, version or "", get_requests_auth()))
    return list(get_project_packages_from(proxy_url, name, version or ""))


# NOTE: this function mutates external objects.
def remove_artifact_from_package(artifact: PackageArtifact, package: UvPackage) -> None:
    """Removes an artifact from a package."""

    if artifact in package.wheels:
        package.wheels.remove(artifact)
    elif artifact == package.sdist:
        package.sdist = None


# NOTE: this function mutates external objects.
def adjust_source_urls_to_proxy(packages_needing_proxy: Iterable[UvPackage]) -> None:
    config = get_config()
    if config.uv.proxy_url is None:  # nothing to adjust.
        return
    proxy_url = str(config.uv.proxy_url)
    for package in packages_needing_proxy:
        available_packages = get_packages_available_on_proxy(package.name, package.version)
        uv_artifacts = [a for a in [package.sdist] + package.wheels if a is not None]
        proxied_urls = {f"{k}:{v}": p.url for p in available_packages for k, v in p.digests.items()}

        for art in uv_artifacts:
            if art.hash in proxied_urls:
                art.url = proxied_urls[art.hash]
                package._proxy_url = proxy_url
            else:
                log.debug(f"Could not find {art} on proxy {proxy_url}")
                # It is technically possible to set up a proxy in such a way that
                # some artifacts won't be present. This could result in
                # credentials being sent to a wrong server, thus it is
                # necessary to remove artifacts which are missing on the
                # configured proxy from the package.
                remove_artifact_from_package(art, package)


def partition(predicate: Callable, iterable: Iterable) -> list[list]:
    return [list(e) for e in more_itertools.partition(predicate, iterable)]


def _resolve_uv(package_dir: RootedPath, output_dir: RootedPath) -> UvResolutionResult:
    pyproject = package_dir.join_within_root("pyproject.toml")
    if not pyproject.path.exists():
        raise PackageRejected(
            reason=(
                f"{pyproject.subpath_from_root} not found; "
                "a uv project requires one next to uv.lock"
            ),
        )

    name, version = _get_pyproject_metadata(package_dir)
    project_vcs_qualifiers = _get_project_vcs_qualifiers(package_dir)
    main_purl = _generate_purl_main_package(name, version, package_dir, project_vcs_qualifiers)

    lockfile_doc = load_lockfile_document(package_dir)
    lock = UvLock.from_toml(lockfile_doc, package_dir.join_within_root(DEFAULT_LOCKFILE_NAME).path)

    of_default_location = lambda p: p.source.location.startswith("https://pypi.org/simple")
    from_default_registry = lambda p: p.source.kind == "registry" and of_default_location(p)
    other_packages, packages_needing_proxy = partition(from_default_registry, lock.packages)
    adjust_source_urls_to_proxy(packages_needing_proxy)

    log.debug("Parsed %d packages from %s", len(lock.packages), DEFAULT_LOCKFILE_NAME)

    if packages_needing_proxy:
        _download_dependencies(output_dir, packages_needing_proxy, get_aiohttp_auth())
    if other_packages:
        _download_dependencies(output_dir, other_packages)

    dependency_components = _generate_dependency_components(
        lock.packages, package_dir, project_vcs_qualifiers
    )

    build_deps = download_build_dependencies(package_dir, output_dir)
    components = [Component(name=name, version=version, purl=main_purl)]
    components.extend(dependency_components)
    components.extend(to_component(dep) for dep in build_deps)

    return UvResolutionResult(
        name=name,
        version=version,
        components=components,
        rewritten_lockfile=_rewrite_lockfile(lockfile_doc, package_dir, lock),
    )


def make_auth_headers(files_to_download: dict[Url, Path], auth: str | None) -> dict | None:
    if auth is not None:
        headers = {}
        for url, _ in files_to_download.items():
            headers[url] = {"Authorization": auth}
        return headers
    return None


def _download_dependencies(
    output_dir: RootedPath, packages: Iterable[UvPackage] | list, auth: str | None = None
) -> None:
    """Fetch every remote artifact recorded in uv.lock into a flat deps/uv directory."""
    deps_dir = output_dir.join_within_root("deps", "uv")
    deps_dir.path.mkdir(parents=True, exist_ok=True)

    items: list[_DownloadItem] = []
    files_to_download: dict[Url, Path] = {}
    for package in packages:
        if isinstance(package.source, PackageSourceGit):
            _download_git_package(package, package.source, deps_dir)
        else:
            # everything else lands here: registry, url and the four local kinds
            for artifact in package.artifacts_to_download:
                if artifact.url is None:
                    # should not happen
                    raise RuntimeError(f"artifact for {package.name} has no download URL")
                target = deps_dir.join_within_root(
                    artifact.get_target_filename(package.source)
                ).path
                items.append(_DownloadItem(package, artifact, target))
                if not target.exists():
                    files_to_download[artifact.url] = target

    if files_to_download:
        log.info("Downloading %d artifacts from %s", len(files_to_download), DEFAULT_LOCKFILE_NAME)
        concurrency_limit = get_config().runtime.concurrency_limit
        headers = make_auth_headers(files_to_download, auth)
        asyncio.run(async_download_files(files_to_download, concurrency_limit, headers=headers))

    for package, artifact, target in items:
        if artifact.checksum_info is not None:
            must_match_any_checksum(target, [artifact.checksum_info])
        else:
            log.warning("Missing checksum for %s==%s", package.name, package.version)


def _is_local_package(package: UvPackage) -> bool:
    """Whether the package's files are already in the project tree, so nothing is fetched."""
    return isinstance(package.source, PackageSourceLocal)


def _is_binary_package(package: UvPackage, artifacts: list[PackageArtifact]) -> bool:
    """Whether what hermeto fetched for the package is a wheel.

    Nothing is fetched for a local source, so its own entry is what says.
    """
    if _is_local_package(package):
        return isinstance(package.sole_artifact, ArtifactWheel)
    return any(isinstance(artifact, ArtifactWheel) for artifact in artifacts)


def _generate_dependency_component(
    package: UvPackage,
    artifacts: list[PackageArtifact],
    package_dir: RootedPath,
    project_vcs_qualifiers: dict[str, str] | None,
    lockfile_relpath: str,
) -> Component:
    """Build the SBOM component for a single uv.lock package."""
    missing_hash: frozenset[str] = (
        frozenset({lockfile_relpath}) if package.records_no_checksum else frozenset()
    )
    if package._proxy_url is None:
        external_references = None
    else:
        t = dict(type=PROXY_REF_TYPE, comment=PROXY_COMMENT)
        external_references = [ExternalReference(url=str(package._proxy_url), **t)]

    return Component(
        name=package.name,
        version=package.version,
        purl=package.purl(package_dir, project_vcs_qualifiers),
        properties=PropertySet(
            missing_hash_in_file=missing_hash,
            uv_package_binary=_is_binary_package(package, artifacts),
        ).to_properties(),
        external_references=external_references,
    )


def _is_root_project_entry(package: UvPackage) -> bool:
    """Whether the package is the uv project itself rather than one of its dependencies.

    uv locks the project under the directory it lives in, so its own entry is
    the local source pointing at the project directory.
    """
    return _is_local_package(package) and package.source.location == "."


def _generate_dependency_components(
    packages: list[UvPackage],
    package_dir: RootedPath,
    project_vcs_qualifiers: dict[str, str] | None,
) -> list[Component]:
    """Build one Component per uv.lock package, excluding the root project's own entry."""
    lockfile_relpath = str(package_dir.join_within_root(DEFAULT_LOCKFILE_NAME).subpath_from_root)
    gdc = partial(
        _generate_dependency_component,
        package_dir=package_dir,
        project_vcs_qualifiers=project_vcs_qualifiers,
        lockfile_relpath=lockfile_relpath,
    )
    return [gdc(p, p.artifacts_to_download) for p in packages if not _is_root_project_entry(p)]


def _git_tarball_filename(package: UvPackage, source: PackageSourceGit) -> str:
    """Name of the tarball under deps/uv for a git package.

    Both the download and the lockfile rewrite go through this, so the
    rewritten source path always resolves to the tarball that was downloaded.
    """
    return f"{package.name}-gitcommit-{source.commit}.tar.gz"


def _download_git_package(
    package: UvPackage, source: PackageSourceGit, deps_dir: RootedPath
) -> None:
    """Clone a git package at its resolved commit and archive it as a tarball."""
    commit = source.commit
    tarball = deps_dir.join_within_root(_git_tarball_filename(package, source))
    if tarball.path.exists():
        log.debug("%s already exists, skipping clone", tarball.path.name)
        return

    log.info("Cloning git repository for %s==%s", package.name, package.version)
    clone_as_tarball(source.clone_url, commit, to_path=tarball.path)


def _replace_source_with_path(raw_package: Any, filename: str) -> None:
    """Swap a remote source for the local path it was downloaded to."""
    # tomlkit.inline_table() would write `source = {path = "..."}`; uv writes
    # `source = { registry = "..." }`. Parsing a snippet keeps uv's brace padding,
    # so rewritten entries do not stand out from the ones left untouched.
    source = cast(InlineTable, tomlkit.value('{ path = "" }'))
    source["path"] = f"{_TEMPLATED_DEPS_DIR}/{filename}"
    raw_package["source"] = source


def meets_proxied_environment_expectations(raw_wheel: dict, wheel: ArtifactWheel) -> bool:
    must_match = set(_WHEEL_LOCATIONS) - set(("url",))
    uv_proxy_is_defined = get_config().uv.proxy_url is not None
    all_keys_except_url_match = all(raw_wheel.get(k) == getattr(wheel, k) for k in must_match)
    return uv_proxy_is_defined and all_keys_except_url_match


def reject_package_if_requested_wheels_were_missing_on_proxy(
    raw_package: dict, package: UvPackage
) -> None:
    raw_wheels = raw_package.get("wheels", [])
    if len(raw_wheels) != len(package.wheels) and get_config().uv.proxy_url is not None:
        hashes_of_present_wheels = [w.hash for w in package.wheels]
        missing_wheels = []
        for w in raw_wheels:
            if w.get("hash", "") not in hashes_of_present_wheels:
                missing_wheels.append(w.get("url", "/<unknown>").rsplit("/", 1)[1])
        raise PackageRejected(
            "Some of the requested wheels were not found on the proxy: "
            f"{missing_wheels}. "
            "Please make sure you use correct proxy and it is set up correctly.",
            solution=None,
        )


# This situation is very unlikely, but at the same time very grave, the only solution
# is to fail immediately.
def halt_if_collected_wheels_diverged_from_expectations(
    raw_package: dict, package: UvPackage
) -> None:
    raw_wheels = raw_package.get("wheels", [])
    for raw_wheel, wheel in zip(raw_wheels, package.wheels, strict=True):
        if any(raw_wheel.get(key) != getattr(wheel, key) for key in _WHEEL_LOCATIONS):
            # should not happen: as above, one file parsed two ways
            # This could happen for proxied registries, in that case it is expected
            if meets_proxied_environment_expectations(raw_wheel, wheel):
                pass
            else:
                raise RuntimeError(
                    f"uv.lock wheel for {package.name} does not match the parsed entry"
                )


# This situation is very unlikely, but at the same time very grave, the only solution
# is to fail immediately.
def halt_if_packages_got_misaligned(raw_package: dict, processed_package: UvPackage) -> None:
    expected_state = (raw_package["name"], raw_package.get("version"))
    obtained_state = (processed_package.name, processed_package.version)
    if expected_state != obtained_state:
        # should not happen: both views parse the same file, in order
        raise RuntimeError(
            f"uv.lock entry {raw_package['name']} does not match "
            f"the parsed {processed_package.name}"
        )


def adjust_sdist_entry_if_needed(raw_package: dict, processed_package: UvPackage) -> None:
    """Adjust lock file to point to sdist on file system if sdist was downloaded."""
    if processed_package.sdist is not None:
        filename = processed_package.sdist.get_target_filename(processed_package.source)
        raw_package["sdist"]["url"] = f"file://{_TEMPLATED_DEPS_DIR}/{filename}"


def adjust_wheels_entries(raw_package: dict, processed_package: UvPackage) -> None:
    """Adjust lock file to point to wheels on file system if any were downloaded."""
    raw_wheels = raw_package.get("wheels", [])
    for raw_wheel, wheel in zip(raw_wheels, processed_package.wheels, strict=True):
        filename = wheel.get_target_filename(processed_package.source)
        raw_wheel["url"] = f"file://{_TEMPLATED_DEPS_DIR}/{filename}"


def extract_sole_artifact(processed_package: UvPackage) -> PackageArtifact:
    result = processed_package.sole_artifact
    if result is None:
        # should not happen: artifacts_to_download rejects such a processed_package
        raise RuntimeError(f"{processed_package.name} records no sdist and no wheels in uv.lock")
    return result


def _rewrite_a_single_package(raw_package: dict, processed_package: UvPackage) -> None:
    """Modify a single entry into the raw document based on lockfile processing results.

    Modifies `raw_package` in enclosing scope!"""
    match processed_package.source:
        case PackageSourceRegistry():
            reject_package_if_requested_wheels_were_missing_on_proxy(raw_package, processed_package)
            halt_if_collected_wheels_diverged_from_expectations(raw_package, processed_package)

            adjust_sdist_entry_if_needed(raw_package, processed_package)
            adjust_wheels_entries(raw_package, processed_package)

        case PackageSourceGit() as source:
            _replace_source_with_path(raw_package, _git_tarball_filename(processed_package, source))

        case PackageSourceUrl():
            recorded = extract_sole_artifact(processed_package)
            _replace_source_with_path(
                raw_package, recorded.get_target_filename(processed_package.source)
            )
        case PackageSourceLocal():
            # nothing was fetched for these, so the entries already point at the files
            pass
        case _:
            assert_never(processed_package.source)


def _rewrite_lockfile(
    doc: tomlkit.TOMLDocument, package_dir: RootedPath, lock: UvLock
) -> ProjectFile | None:
    """Redirect every remote artifact reference in uv.lock into deps/uv.

    `uv sync` fetches the URLs recorded in uv.lock verbatim and cannot fall
    back to UV_FIND_LINKS, so the lockfile itself must point at the prefetched
    artifacts. The rewrite edits the raw TOML document in place (callers must
    not reuse it): the UvLock model is deliberately lossy, while tomlkit
    preserves every field and the original formatting.

    Returns None if the lockfile references no remote artifacts.
    """
    if all(_is_local_package(package) for package in lock.packages):
        return None

    for raw_package, processed_package in zip(doc.get("package", []), lock.packages, strict=True):
        halt_if_packages_got_misaligned(raw_package, processed_package)
        _rewrite_a_single_package(raw_package, processed_package)

    lockfile_path = package_dir.join_within_root(DEFAULT_LOCKFILE_NAME)
    return ProjectFile(abspath=lockfile_path.path, template=tomlkit.dumps(doc))


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


def _get_project_vcs_qualifiers(package_dir: RootedPath) -> dict[str, str] | None:
    """Get vcs_url qualifiers for the repo containing package_dir, or None if unavailable.

    Shared by the main project's purl and every path/directory/editable/virtual
    dependency purl, since local dependencies live in the same repo and a
    separate repo lookup per dependency would be redundant.
    """
    try:
        return get_vcs_qualifiers(package_dir.root)
    except NotAGitRepo:
        if get_config().mode == Mode.PERMISSIVE:
            return None
        raise


def _generate_purl_main_package(
    name: str,
    version: str | None,
    package_dir: RootedPath,
    project_vcs_qualifiers: dict[str, str] | None,
) -> str:
    """Get the purl for the uv project itself."""
    if package_dir.subpath_from_root != Path("."):
        subpath = package_dir.subpath_from_root.as_posix()
    else:
        subpath = None

    purl = PackageURL(
        type="pypi",
        name=name,
        version=version,
        qualifiers=project_vcs_qualifiers,
        subpath=subpath,
    )
    return purl.to_string()


def _generate_environment_variables() -> list[EnvironmentVariable]:
    return [
        EnvironmentVariable(name="UV_OFFLINE", value="true"),
        EnvironmentVariable(name="UV_FIND_LINKS", value="${output_dir}/deps/uv"),
        EnvironmentVariable(name="UV_FROZEN", value="true"),
        EnvironmentVariable(name="UV_NO_BINARY", value="true"),
    ]
