import asyncio
import copy
import fnmatch
import functools
import json
import logging
from functools import partial
from pathlib import Path
from typing import Any, Literal, NewType, TypedDict
from urllib.parse import urlparse

import aiohttp
from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import ProxyUrl, get_config
from hermeto.core.errors import (
    LockfileNotFound,
    MissingChecksum,
    PackageRejected,
    UnexpectedFormat,
    UnsupportedFeature,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import ProjectFile, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import (
    PROXY_COMMENT,
    PROXY_REF_TYPE,
    Component,
    ExternalReference,
    create_backend_annotation,
)
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID, clone_as_tarball, get_repo_id

DEPENDENCY_TYPES = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)
log = logging.getLogger(__name__)

# Known CNAMEs for the official npm registry server.
# In rare cases, package-lock.json may contain resolved urls with the yarn CNAME.
# This most likely happens when converting a yarn.lock to package-lock.json
# ("importing" one with npm or "exporting" with yarn).
NPM_REGISTRY_CNAMES = ("registry.npmjs.org", "registry.yarnpkg.com")


class NpmComponentInfo(TypedDict):
    """Contains the data needed to generate an npm SBOM component."""

    name: str
    purl: str
    version: str | None
    dev: bool
    bundled: bool
    external_refs: list[ExternalReference] | None
    missing_hash_in_file: Path | None


class ResolvedNpmPackage(TypedDict):
    """Contains all of the data for a resolved npm package."""

    package: NpmComponentInfo
    dependencies: list[NpmComponentInfo]
    projectfiles: list[ProjectFile]


class Package:
    """A npm package."""

    def __init__(self, name: str, path: str, package_dict: dict[str, Any]) -> None:
        """Initialize a Package.

        :param name: the package name, which should correspond to the name in it's package.json
        :param path: the relative path to the package from the root project dir.
        :param package_dict: the raw dict for a package-lock.json `package`
        """
        self.name = name
        self.path = path
        self._package_dict = package_dict

    @property
    def package_dict(self) -> dict[str, Any]:
        """Get the package dictionary with additional info."""
        return self._package_dict

    @property
    def integrity(self) -> str | None:
        """Get the package integrity."""
        return self._package_dict.get("integrity")

    @integrity.setter
    def integrity(self, integrity: str) -> None:
        """Set the package integrity."""
        self._package_dict["integrity"] = integrity

    @property
    def version(self) -> str | None:
        """Get the package version.

        This will be a semver from the package.json file.
        https://docs.npmjs.com/cli/v7/configuring-npm/package-lock-json#packages
        """
        return self._package_dict.get("version")

    @property
    def resolved_url(self) -> str | None:
        """Get the location where the package was resolved from.

        For package-lock.json `packages`, this will be the "resolved" key
        unless it is a file dep, in which case it will be the path to the file.

        For bundled dependencies, this will be None. Such dependencies are included
        in the tarball of a different dependency (the dependency that bundles them).
        """
        if "resolved" not in self._package_dict:
            # indirect bundled dependency, does not have a resolved url
            if self._package_dict.get("inBundle"):
                return None
            # file dependency (or a workspace)
            else:
                return f"file:{self.path}"

        return self._package_dict["resolved"]

    @resolved_url.setter
    def resolved_url(self, resolved_url: str) -> None:
        """Set the location where the package should be resolved from."""
        self._package_dict["resolved"] = resolved_url

    @property
    def bundled(self) -> bool:
        """Return True if this package is bundled."""
        return (
            self._package_dict.get("inBundle", False)
            # In v2+ lockfiles, direct dependencies do have "inBundle": true if they are to be
            # bundled. They will get bundled if the package is uploaded to the npm registry, but
            # aren't bundled yet. These have a resolved url and shouldn't be considered bundled.
            and "resolved" not in self._package_dict
        )

    @property
    def dev(self) -> bool:
        """Return True if this package is a dev dependency."""
        return self._package_dict.get("dev", False)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Package):
            return (
                self.name == other.name
                and self.path == other.path
                and self._package_dict == other._package_dict
            )
        return False


class PackageLock:
    """A npm package-lock.json file."""

    def __init__(self, lockfile_path: RootedPath, lockfile_data: dict[str, Any]) -> None:
        """Initialize a PackageLock."""
        self._workspaces: list[str] = []
        self._lockfile_path = lockfile_path
        self._lockfile_data = lockfile_data
        self._main_package, self._packages = self._get_packages()

    @property
    def packages(self) -> list[Package]:
        """Get list of packages loaded from package-lock.json."""
        return self._packages

    @property
    def main_package(self) -> Package:
        """Return main package info stored as Package instance."""
        return self._main_package

    @property
    def lockfile_data(self) -> dict[str, Any]:
        """Get content of package-lock.json stored in Dictionary."""
        return self._lockfile_data

    def _check_if_package_is_workspace(self, resolved_url: str) -> bool:
        """Test if package is workspace based on main package workspaces."""
        if (
            "packages" not in self._lockfile_data
            or "" not in self._lockfile_data["packages"]
            or "workspaces" not in self._lockfile_data["packages"][""]
        ):
            return False

        main_package_workspaces = self._lockfile_data["packages"][""]["workspaces"]

        for main_package_workspace in main_package_workspaces:
            if fnmatch.fnmatch(resolved_url, Path(main_package_workspace).as_posix()):
                return True
        return False

    @functools.cached_property
    def _purlifier(self) -> "_Purlifier":
        pkg_path = self._lockfile_path.join_within_root("..")
        return _Purlifier(pkg_path)

    @classmethod
    def from_file(cls, lockfile_path: RootedPath) -> "PackageLock":
        """Create a PackageLock from a package-lock.json file."""
        with lockfile_path.path.open("r") as f:
            lockfile_data = json.load(f)

        lockfile_version = lockfile_data.get("lockfileVersion")
        if lockfile_version not in (2, 3):
            raise UnsupportedFeature(
                f"lockfileVersion {lockfile_version} from {lockfile_path} is not supported. lockfileVersion 1 was deprecated.",
                solution="Please use a supported lockfileVersion, which are versions 2 and 3",
            )

        return cls(lockfile_path, lockfile_data)

    def get_project_file(self) -> ProjectFile:
        """Return a ProjectFile for the npm package-lock.json data."""
        return ProjectFile(
            abspath=self._lockfile_path.path.resolve(),
            template=json.dumps(self._lockfile_data, indent=2) + "\n",
        )

    def _get_packages(self) -> tuple[Package, list[Package]]:
        """Return a flat list of Packages from a package-lock.json file."""

        def get_package_name_from_path(package_path: str) -> str:
            """Get the package name from the path in package-lock.json file."""
            path = Path(package_path)
            parent_name = Path(package_path).parents[0].name
            is_scoped = parent_name.startswith("@")
            return (Path(parent_name) / path.name).as_posix() if is_scoped else path.name

        main_package = Package("", "", {})
        packages = []
        for package_path, package_data in self._lockfile_data.get("packages", {}).items():
            # ignore links and the main package, since they're already accounted
            # for elsewhere in the lockfile
            if package_data.get("link"):
                if self._check_if_package_is_workspace(package_data.get("resolved")):
                    self._workspaces.append(package_data.get("resolved"))
                continue

            if package_path == "":
                # Store main package as Package instance
                main_package = Package(package_data.get("name"), package_path, package_data)
                continue

            # if there is no name key, derive it from the package path
            if not (package_name := package_data.get("name")):
                package_name = get_package_name_from_path(package_path)

            packages.append(Package(package_name, package_path, package_data))

        return main_package, packages

    def get_main_package(self) -> NpmComponentInfo:
        """Return a dict with sbom component data for the main package."""
        name = self._lockfile_data["name"]
        version = self._lockfile_data["version"]
        purl = self._purlifier.get_purl(name, version, "file:.", integrity=None)
        return {
            "name": name,
            "version": version,
            "purl": purl.to_string(),
            "dev": False,
            "bundled": False,
            "missing_hash_in_file": None,
            "external_refs": None,
        }

    def get_sbom_components(self) -> list[NpmComponentInfo]:
        """Return a list of dicts with sbom component data."""
        packages = self._packages
        proxy_common = dict(type=PROXY_REF_TYPE, comment=PROXY_COMMENT)
        proxy_url = get_config().npm.proxy_url

        def to_component(package: Package) -> NpmComponentInfo:
            purl = self._purlifier.get_purl(
                package.name, package.version, package.resolved_url, package.integrity
            ).to_string()

            missing_hash_in_file = None
            external_refs = None
            if package.resolved_url:  # dependency is not bundled
                resolved_url = _normalize_resolved_url(package.resolved_url)
                dep_type = _classify_resolved_url(resolved_url)

                if not package.integrity:
                    if dep_type in ("registry", "https"):
                        missing_hash_in_file = Path(self._lockfile_path.subpath_from_root)
                if dep_type == "registry" and proxy_url is not None:
                    external_refs = [ExternalReference(url=str(proxy_url), **proxy_common)]

            component: NpmComponentInfo = {
                "name": package.name,
                "version": package.version,
                "purl": purl,
                "dev": package.dev,
                "bundled": package.bundled,
                "missing_hash_in_file": missing_hash_in_file,
                "external_refs": external_refs,
            }

            return component

        return list(map(to_component, packages))

    def get_dependencies_to_download(self) -> dict[str, dict[str, str | None]]:
        """Return a Dict of URL dependencies to download."""
        packages = self._packages
        return {
            resolved_url: {
                "version": package.version,
                "name": package.name,
                "integrity": package.integrity,
            }
            for package in packages
            if (resolved_url := package.resolved_url) and not resolved_url.startswith("file:")
        }

    @property
    def workspaces(self) -> list:
        """Return list of workspaces."""
        return self._workspaces


class _Purlifier:
    """Generates purls for npm packages."""

    def __init__(self, pkg_path: RootedPath) -> None:
        """Init a purlifier for the package at pkg_path."""
        self._pkg_path = pkg_path

    @functools.cached_property
    def _repo_id(self) -> RepoID:
        return get_repo_id(self._pkg_path.root)

    def get_purl(
        self,
        name: str,
        version: str | None,
        resolved_url: str | None,
        integrity: str | None,
    ) -> PackageURL:
        """Get the purl for an npm package.

        https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#npm
        """
        if not resolved_url:
            # bundled dependency, same purl as a registry dependency
            # (differentiation between bundled and registry should be done elsewhere)
            return PackageURL(type="npm", name=name.lower(), version=version)

        qualifiers: dict[str, str] | None = None
        subpath: str | None = None

        resolved_url = _normalize_resolved_url(resolved_url)
        dep_type = _classify_resolved_url(resolved_url)

        if dep_type == "registry":
            pass
        elif dep_type == "git":
            info = _extract_git_info_npm(resolved_url)
            repo_id = RepoID(origin_url=info["url"], commit_id=info["ref"])
            qualifiers = {"vcs_url": repo_id.as_vcs_url_qualifier()}
        elif dep_type == "file":
            qualifiers = {"vcs_url": self._repo_id.as_vcs_url_qualifier()}
            path = urlparse(resolved_url).path
            subpath_from_root = self._pkg_path.join_within_root(path).subpath_from_root
            if subpath_from_root != Path():
                subpath = subpath_from_root.as_posix()
        else:  # dep_type == "https"
            qualifiers = {"download_url": resolved_url}
            if integrity:
                qualifiers["checksum"] = str(ChecksumInfo.from_sri(integrity))

        return PackageURL(
            type="npm",
            name=name.lower(),
            version=version,
            qualifiers=qualifiers,
            subpath=subpath,
        )


NormalizedUrl = NewType("NormalizedUrl", str)


def _normalize_resolved_url(resolved_url: str) -> NormalizedUrl:
    if resolved_url.startswith(("github:", "gitlab:", "bitbucket:")):
        resolved_url = _update_vcs_url_with_full_hostname(resolved_url)
    return NormalizedUrl(resolved_url)


def _classify_resolved_url(
    resolved_url: NormalizedUrl,
) -> Literal["registry", "git", "file", "https"]:
    url = urlparse(resolved_url)
    if url.hostname in NPM_REGISTRY_CNAMES:
        return "registry"
    if url.scheme == "git" or url.scheme.startswith("git+"):
        return "git"
    if url.scheme == "file":
        return "file"
    return "https"


def _update_vcs_url_with_full_hostname(vcs: str) -> str:
    """Update VCS URL with full hostname.

    Transform github:kevva/is-positive#97edff6
    into git+ssh://github.com/kevva/is-positive.git#97edff6

    :param vcs: VCS URL to be modified with full hostname and file extension
    :return: Updated VCS URL
    """
    host, _, path = vcs.partition(":")
    namespace_repo, _, ref = path.partition("#")
    suffix_domain = "org" if host == "bitbucket" else "com"

    vcs = f"git+ssh://git@{host}.{suffix_domain}/{namespace_repo}.git"
    if ref:
        vcs = f"{vcs}#{ref}"
    return vcs


def _extract_git_info_npm(vcs_url: NormalizedUrl) -> dict[str, str]:
    """
    Extract important info from a VCS requirement URL.

    Given a URL such as git+ssh://user@host/namespace/repo.git#9e164b970

    this function will extract:
    - the "clean" URL: ssh://user@host/namespace/repo.git
    - the git ref: 9e164b970

    The clean URL and ref can be passed straight to scm.Git to fetch the repo.
    The host, namespace and repo will be used to construct the file path under deps/npm.

    :param vcs_url: The URL of a VCS requirement, must be valid (have git ref in path)
    :return: Dict with url, ref, host, namespace and repo keys
    """
    clean_url, _, ref = vcs_url.partition("#")
    # if scheme is git+protocol://, keep only protocol://
    clean_url = clean_url.removeprefix("git+")

    url = urlparse(clean_url)
    namespace_repo = url.path.strip("/").removesuffix(".git")

    # Everything up to the last '/' is namespace, the rest is repo
    namespace, _, repo = namespace_repo.partition("/")

    vcs_url_info = {
        "url": clean_url,
        "ref": ref.lower(),
        "namespace": namespace,
        "repo": repo,
    }

    for key, value in vcs_url_info.items():
        if not value:
            raise UnexpectedFormat(f"{vcs_url} is not valid VCS url. {key} is missing.")

    if url.hostname:
        vcs_url_info["host"] = url.hostname
    else:
        raise UnexpectedFormat(f"{vcs_url} is not valid VCS url. Host is missing.")

    return vcs_url_info


def _clone_repo_pack_archive(
    vcs: NormalizedUrl,
    download_dir: RootedPath,
) -> RootedPath:
    """
    Clone a repository and pack its content as tar.

    :param url: URL for file download
    :param download_dir: Output folder where dependencies will be downloaded
    :raise FetchError: If download failed
    """
    info = _extract_git_info_npm(vcs)
    download_path = download_dir.join_within_root(
        info["host"],  # host
        info["namespace"],
        info["repo"],
        f"{info['repo']}-external-gitcommit-{info['ref']}.tgz",
    )

    # Create missing directories
    directory = Path(download_path).parent
    directory.mkdir(parents=True, exist_ok=True)
    clone_as_tarball(info["url"], info["ref"], download_path.path)

    return download_path


def _patch_url_to_point_to_a_proxy(url: NormalizedUrl, proxy_url: ProxyUrl) -> NormalizedUrl:
    # Convert 'https://registry.npmjs.org/accepts/-/accepts-1.3.8.tgz'
    # to '<proxyaddress>/accepts/-/accepts-1.3.8.tgz'.
    s_proxy_url = str(proxy_url)  # mypy becomes really upset when "proxy_url" gets reused here
    s_proxy_url = s_proxy_url if s_proxy_url[-1] == "/" else s_proxy_url + "/"
    url_path = urlparse(url).path[1:]  # Don't need the leading / anymore
    return NormalizedUrl(s_proxy_url + url_path)


async def _async_download_tar(files_to_download_list: list[dict[str, dict[str, Any]]]) -> None:
    ftdl = [e for e in files_to_download_list if e]
    if not ftdl:
        return
    # NOTE: when present proxy auth is the same for all packages accessible
    # through a proxy.
    auth = lambda ftd: next(iter(ftd.values()))["proxy_auth"]
    ftd = lambda ftd: {it["fetch_url"]: it["download_path"] for it in ftd.values()}
    adf = partial(async_download_files, concurrency_limit=get_config().runtime.concurrency_limit)

    await asyncio.gather(*[adf(files_to_download=ftd(f), auth=auth(f)) for f in ftdl])


def _get_npm_dependencies(
    download_dir: RootedPath, deps_to_download: dict[str, dict[str, str | None]]
) -> dict[NormalizedUrl, RootedPath]:
    """
    Download npm dependencies.

    Receives the destination directory (download_dir)
    and the dependencies to be downloaded (deps_to_download).

    :param download_dir: Destination directory path where deps will be downloaded
    :param deps_to_download: Dict of dependencies to be downloaded.
    :return: Dictionary of Resolved URL dependencies with downloaded paths
    """
    files_to_download: dict[str, dict[str, Any]] = {}
    download_paths = {}
    config = get_config()

    for url, info in deps_to_download.items():
        url = _normalize_resolved_url(url)
        fetch_url = url
        dep_type = _classify_resolved_url(url)
        proxy_auth = None

        if dep_type == "file":
            continue
        elif dep_type == "git":
            download_paths[url] = _clone_repo_pack_archive(url, download_dir)
        else:
            if dep_type == "registry":
                archive_name = f"{info['name']}-{info['version']}.tgz".removeprefix("@").replace(
                    "/", "-"
                )
                download_paths[url] = download_dir.join_within_root(archive_name)
                if config.npm.proxy_url is not None:
                    fetch_url = _patch_url_to_point_to_a_proxy(url, config.npm.proxy_url)
                    if config.npm.proxy_login and config.npm.proxy_password:
                        proxy_auth = aiohttp.BasicAuth(
                            config.npm.proxy_login,
                            config.npm.proxy_password,
                        )
            else:  # dep_type == "https"
                if info["integrity"]:
                    algorithm, digest = ChecksumInfo.from_sri(info["integrity"])
                else:
                    raise MissingChecksum(
                        f"{info['name']}",
                        solution="Checksum is mandatory for https dependencies. "
                        "Please double-check provided package-lock.json that "
                        "your dependencies specify integrity. Try to "
                        "rerun `npm install` on your repository.",
                    )
                download_paths[url] = download_dir.join_within_root(
                    f"external-{info['name']}",
                    f"{info['name']}-external-{algorithm}-{digest}.tgz",
                )

                # Create missing directories
                directory = Path(download_paths[url]).parent
                directory.mkdir(parents=True, exist_ok=True)

            files_to_download[url] = {
                "fetch_url": fetch_url,
                "download_path": download_paths[url],
                "integrity": info["integrity"],
                "proxy_auth": proxy_auth,
            }

    files_with_auth = {k: v for k, v in files_to_download.items() if v["proxy_auth"] is not None}
    files_without_auth = {k: v for k, v in files_to_download.items() if v["proxy_auth"] is None}

    asyncio.run(_async_download_tar([files_with_auth, files_without_auth]))

    # Check integrity of downloaded packages
    for url, item in files_to_download.items():
        if item["integrity"]:
            must_match_any_checksum(
                item["download_path"], [ChecksumInfo.from_sri(str(item["integrity"]))]
            )
        else:
            log.warning("Missing integrity for %s, integrity check skipped.", url)

    return download_paths


def _should_replace_dependency(dependency_version: str) -> bool:
    """Check if dependency must be updated in package(-lock).json.

    package(-lock).json files require to replace dependency URLs for
    empty string in git and https dependencies.
    """
    url = urlparse(dependency_version)
    if url.scheme == "file" or url.scheme == "npm":
        return False
    return url.scheme != "" or "/" in dependency_version


def _update_package_lock_with_local_paths(
    download_paths: dict[NormalizedUrl, RootedPath],
    package_lock: PackageLock,
) -> None:
    """Replace packages resolved URLs with local paths.

    Update package-lock.json file in a way it can be used in isolated environment (container)
    without internet connection. All package resolved URLs will be replaced for
    local paths to downloaded dependencies.

    :param download_paths:
    :param package_lock: PackageLock instance which holds package-lock.json content
    """
    for package in package_lock.packages + [package_lock.main_package]:
        for dep_type in DEPENDENCY_TYPES:
            if package.package_dict.get(dep_type):
                for dependency, dependency_version in package.package_dict[dep_type].items():
                    if _should_replace_dependency(dependency_version):
                        package.package_dict[dep_type].update({dependency: ""})

        if package.path and package.resolved_url:
            url = _normalize_resolved_url(str(package.resolved_url))
        else:
            continue

        # Remove integrity for git sources, their integrity checksum will change when
        # constructing tar archive from cloned repository
        if _classify_resolved_url(url) == "git":
            if package.integrity:
                package.integrity = ""

        # Replace the resolved_url of all packages, unless it's already a file url:
        if _classify_resolved_url(url) != "file":
            templated_abspath = Path("${output_dir}", download_paths[url].subpath_from_root)
            package.resolved_url = f"file://{templated_abspath}"


def _update_package_json_files(
    workspaces: list[str],
    pkg_dir: RootedPath,
) -> list[ProjectFile]:
    """Set dependencies to empty string in package.json files.

    :param workspaces: list of workspaces paths
    :param pkg_dir: Package subdirectory
    """
    package_json_paths = []
    for workspace in workspaces:
        package_json_paths.append(pkg_dir.join_within_root(workspace, "package.json"))
    package_json_paths.append(pkg_dir.join_within_root("package.json"))

    package_json_projectfiles = []
    for package_json_path in package_json_paths:
        with open(package_json_path) as f:
            package_json_content = json.load(f)

        for dep_type in DEPENDENCY_TYPES:
            if package_json_content.get(dep_type):
                for dependency, url in package_json_content[dep_type].items():
                    if _should_replace_dependency(url):
                        package_json_content[dep_type].update({dependency: ""})

        package_json_projectfiles.append(
            ProjectFile(
                abspath=package_json_path.path,
                template=json.dumps(package_json_content, indent=2) + "\n",
            )
        )
    return package_json_projectfiles


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
