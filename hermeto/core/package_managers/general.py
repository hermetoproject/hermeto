# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import logging
import ssl
import types
from os import PathLike
from typing import Any, Optional, Union
from urllib.parse import urlparse

import aiohttp
import aiohttp_retry
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
    session: aiohttp_retry.RetryClient,
    url: str,
    download_path: Union[str, PathLike[str]],
    auth: Optional[aiohttp.BasicAuth] = None,
    ssl_context: Optional[ssl.SSLContext] = None,
    chunk_size: int = 8192,
) -> None:
    """
    Download a binary file (such as a TAR archive) from a URL using asyncio.

    :param aiohttp_retry.RetryClient session: Aiohttp interface for making HTTP requests.
    :param str url: URL for file download
    :param str download_path: File path location
    :param aiohttp.BasicAuth auth: Authentication for the URL
    :param int chunk_size: Chunk size param for Response.content.read()
    :raise FetchError: If download failed
    """
    try:
        timeout = aiohttp.ClientTimeout(total=get_config().requests_timeout)

        log.debug(
            f"aiohttp.ClientSession.get(url: {url}, timeout: {timeout}, raise_for_status: True)"
        )
        async with session.get(
            url, timeout=timeout, auth=auth, raise_for_status=True, ssl=ssl_context
        ) as resp:
            with open(download_path, "wb") as f:
                while True:
                    chunk = await resp.content.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)

    except Exception as exception:
        log.error(f"Unsuccessful download: {url}")
        # "from None" since we have the exception context in the logs
        raise FetchError(
            f"exception_name: {exception.__class__.__name__}, " f"details: {exception}"
        ) from None

    log.debug(f"Download completed - {url}")


async def async_download_files(
    files_to_download: dict[str, Union[str, PathLike[str]]],
    ssl_context: Optional[ssl.SSLContext] = None,
) -> None:
    """Asynchronous function to download files.

    :param files_to_download: Dict of files to download with file paths
    :param concurrency_limit: Max number of concurrent tasks (downloads).
    """

    async def on_request_start(
        session: aiohttp.ClientSession,
        trace_config_ctx: types.SimpleNamespace,
        params: aiohttp.TraceRequestStartParams,
    ) -> None:
        current_attempt = trace_config_ctx.trace_request_ctx["current_attempt"]
        if current_attempt > 1:
            file_name = params.url.path.split("/")[-1]
            log.debug(f"Attempt {current_attempt}/{retry_options.attempts} - {file_name}")

    trace_config = aiohttp.TraceConfig()
    trace_config.on_request_start.append(on_request_start)
    num_attempts: int = 5
    retry_options = aiohttp_retry.JitterRetry(attempts=num_attempts, retry_all_server_errors=True)
    retry_client = aiohttp_retry.RetryClient(
        retry_options=retry_options,
        trace_configs=[trace_config],
        # respect proxy settings and .netrc
        trust_env=True,
    )

    async with retry_client as session:
        tasks: set[asyncio.Task] = set()

        for url, download_path in files_to_download.items():
            tasks.add(
                asyncio.create_task(
                    _async_download_binary_file(
                        session, url, download_path, ssl_context=ssl_context
                    )
                )
            )

        await asyncio.gather(*tasks)


def extract_git_info(vcs_url: str) -> dict[str, Any]:
    """
    Extract important info from a VCS requirement URL.

    Given a URL such as git+https://user:pass@host:port/namespace/repo.git@123456?foo=bar#egg=spam
    this function will extract:
    - the "clean" URL: https://user:pass@host:port/namespace/repo.git
    - the git ref: 123456
    - the host, namespace and repo: host:port, namespace, repo

    The clean URL and ref can be passed straight to scm.Git to fetch the repo.
    The host, namespace and repo will be used to construct the file path under deps/pip.

    :param str vcs_url: The URL of a VCS requirement, must be valid (have git ref in path)
    :return: Dict with url, ref, host, namespace and repo keys
    """
    # If scheme is git+protocol://, keep only protocol://
    # Do this before parsing URL, otherwise urllib may not extract URL params
    if vcs_url.startswith("git+"):
        vcs_url = vcs_url[len("git+") :]

    url = urlparse(vcs_url)

    ref = url.path[-40:]  # Take the last 40 characters (the git ref)
    clean_path = url.path[:-41]  # Drop the last 41 characters ('@' + git ref)

    # Note: despite starting with an underscore, the namedtuple._replace() method is public
    clean_url = url._replace(path=clean_path, params="", query="", fragment="")

    # Assume everything up to the last '@' is user:pass. This should be kept in the
    # clean URL used for fetching, but should not be considered part of the host.
    _, _, clean_netloc = url.netloc.rpartition("@")

    namespace_repo = clean_path.strip("/")
    if namespace_repo.endswith(".git"):
        namespace_repo = namespace_repo[: -len(".git")]

    # Everything up to the last '/' is namespace, the rest is repo
    namespace, _, repo = namespace_repo.rpartition("/")

    return {
        "url": clean_url.geturl(),
        "ref": ref.lower(),
        "host": clean_netloc,
        "namespace": namespace,
        "repo": repo,
    }
