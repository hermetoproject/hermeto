# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import copy
import json
import logging
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import ProxyUrl, get_config
from hermeto.core.errors import LockfileNotFound, MissingChecksum, PackageRejected
from hermeto.core.models.output import ProjectFile
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.package_managers.npm.project import (
    PackageLock,
    ResolvedNpmPackage,
    _load_json_file,
)
from hermeto.core.package_managers.npm.utils import (
    NormalizedUrl,
    classify_resolved_url,
    extract_git_info_npm,
    normalize_resolved_url,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball

DEPENDENCY_TYPES = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)
log = logging.getLogger(__name__)


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
    info = extract_git_info_npm(vcs)
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
        url = normalize_resolved_url(url)
        fetch_url = url
        dep_type = classify_resolved_url(url)
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
            url = normalize_resolved_url(str(package.resolved_url))
        else:
            continue

        # Remove integrity for git sources, their integrity checksum will change when
        # constructing tar archive from cloned repository
        if classify_resolved_url(url) == "git":
            if package.integrity:
                package.integrity = ""

        # Replace the resolved_url of all packages, unless it's already a file url:
        if classify_resolved_url(url) != "file":
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
        package_json_content = _load_json_file(package_json_path.path)

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
