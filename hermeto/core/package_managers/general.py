# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import logging
import ssl
from os import PathLike
from pathlib import Path
from typing import Optional, Union

import httpx
from requests import RequestException, Session
from requests.adapters import HTTPAdapter, Retry
from requests.auth import AuthBase

from hermeto.core.config import get_config
from hermeto.core.errors import FetchError

log = logging.getLogger(__name__)


def get_requests_session() -> Session:
    """Create a requests session with various retry options."""
    session = Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            connect=5,
            total=5,
            read=5,
            allowed_methods=["GET", "HEAD", "OPTIONS", "TRACE"],
            status_forcelist=(500, 502, 503, 504),
            backoff_factor=1.3,
        )
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


pkg_requests_session = get_requests_session()


def download_binary_file(
    url: str,
    download_path: Union[str, PathLike[str]],
    auth: Optional[AuthBase] = None,
    insecure: bool = False,
    chunk_size: int = 8192,
) -> None:
    """
    Download a binary file (such as a TAR archive) from a URL.

    :param str url: URL for file download
    :param (str | PathLike) download_path: Path to download file to
    :param requests.auth.AuthBase auth: Authentication for the URL
    :param bool insecure: Do not verify SSL for the URL
    :param int chunk_size: Chunk size param for Response.iter_content()
    :raise FetchError: If download failed
    """
    timeout = get_config().requests_timeout
    try:
        resp = pkg_requests_session.get(
            url, stream=True, verify=not insecure, auth=auth, timeout=timeout
        )
        resp.raise_for_status()
    except RequestException as e:
        raise FetchError(f"Could not download {url}: {e}")

    with open(download_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            f.write(chunk)


async def _async_download_binary_file(
    client: httpx.AsyncClient,
    url: str,
    download_path: Union[str, PathLike[str]],
) -> None:
    """Download a file from the given URL and save it."""
    timeout = get_config().requests_timeout

    try:
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        Path(download_path).write_bytes(response.content)

    except Exception as e:
        log.error(f"Unsuccessful download: {url}")
        raise FetchError(str(e)) from None

    log.debug(f"Download completed - {url}")


async def async_download_files(
    files_to_download: dict[str, Union[str, PathLike[str]]],
    ssl_context: Optional[ssl.SSLContext] = None,
) -> None:
    """Download multiple files asynchronously."""
    tasks: set[asyncio.Task] = set()

    verify = ssl_context if ssl_context is not None else True
    # check if .netrc file exists for authentication
    try:
        auth = httpx.NetRCAuth()
    except Exception:
        log.debug("No .netrc file found in the home directory.")
        auth = None

    async with httpx.AsyncClient(auth=auth, trust_env=True, verify=verify) as client:
        for url, download_path in files_to_download.items():
            tasks.add(asyncio.create_task(_async_download_binary_file(client, url, download_path)))

        await asyncio.gather(*tasks)
