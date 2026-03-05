# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import aiohttp

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import ProxyUrl, get_config
from hermeto.core.errors import MissingChecksum
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball

if TYPE_CHECKING:
    from hermeto.core.package_managers.npm._npm_legacy import NormalizedUrl

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
    from hermeto.core.package_managers.npm._npm_legacy import _extract_git_info_npm

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
    from hermeto.core.package_managers.npm._npm_legacy import NormalizedUrl

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
    from hermeto.core.package_managers.npm._npm_legacy import (
        _classify_resolved_url,
        _normalize_resolved_url,
    )

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
