# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import logging
import ssl
from os import PathLike
from typing import Any, Optional, Union
from urllib.parse import urlparse

import aiohttp
import aiohttp_retry
import requests
from requests.auth import AuthBase

from hermeto.core.config import get_config
from hermeto.core.errors import FetchError
from hermeto.core.http_requests import (
    DEFAULT_RETRY_OPTIONS,
    SAFE_REQUEST_METHODS,
    get_requests_session,
)

pkg_requests_session = get_requests_session(retry_options={"allowed_methods": SAFE_REQUEST_METHODS})

log = logging.getLogger(__name__)


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
    except requests.RequestException as e:
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


async def _async_download_oci_file(
    oci_url: str,
    download_path: Union[str, PathLike[str]],
    checksum: Optional[str] = None,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> None:
    """
    Download a binary file from an OCI registry.

    :param str oci_url: OCI URL in format oci://registry/repo:tag
    :param str download_path: File path location
    :param str checksum: Expected checksum of the layer to download
    :param ssl.SSLContext ssl_context: SSL context for secure connections
    :raise FetchError: If download failed
    """
    try:
        # Parse the OCI URL
        # oci://registry.example.com/rpms/aardvark-dns:1.14.0
        if not oci_url.startswith("oci://"):
            raise ValueError(f"Invalid OCI URL format: {oci_url}")
        
        # Remove oci:// prefix and parse
        registry_path = oci_url[6:]  # Remove "oci://"
        
        # Split registry from repository:tag
        if "/" not in registry_path:
            raise ValueError(f"Invalid OCI URL format - missing repository: {oci_url}")
        
        registry_host = registry_path.split("/", 1)[0]
        repo_tag = registry_path.split("/", 1)[1]
        
        # Split repository from tag
        if ":" not in repo_tag:
            repo = repo_tag
            tag = "latest"
        else:
            repo, tag = repo_tag.rsplit(":", 1)
        
        full_image_name = f"{registry_host}/{repo}:{tag}"
        
        log.debug(f"Fetching OCI manifest for: {full_image_name}")
        
        # Use OCI Registry API to fetch manifest and download specific layer
        registry_url = f"https://{registry_host}"
        manifest_url = f"{registry_url}/v2/{repo}/manifests/{tag}"
        
        # Get Docker authentication if available
        import json
        import os
        from pathlib import Path
        
        auth = None
        docker_config_path = Path.home() / ".docker" / "config.json"
        if docker_config_path.exists():
            try:
                with open(docker_config_path) as f:
                    docker_config = json.load(f)
                    auths = docker_config.get("auths", {})
                    if registry_host in auths:
                        auth_data = auths[registry_host].get("auth", "")
                        if auth_data:
                            import base64
                            decoded = base64.b64decode(auth_data).decode()
                            username, password = decoded.split(":", 1)
                            auth = aiohttp.BasicAuth(username, password)
            except Exception as e:
                log.debug(f"Could not read Docker config: {e}")
        
        # Set up aiohttp session for OCI registry calls
        timeout = aiohttp.ClientTimeout(total=get_config().requests_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Fetch the image manifest
            headers = {"Accept": "application/vnd.docker.distribution.manifest.v2+json"}
            
            async with session.get(manifest_url, headers=headers, auth=auth, ssl=ssl_context) as resp:
                if resp.status != 200:
                    raise FetchError(f"Failed to fetch manifest from {manifest_url}: HTTP {resp.status}")
                
                manifest = await resp.json()
                
                # Find the layer matching the checksum
                target_layer = None
                if checksum:
                    # Extract algorithm and digest from checksum (e.g., "sha256:deadbeef...")
                    if ":" in checksum:
                        alg, digest = checksum.split(":", 1)
                        target_digest = f"{alg}:{digest}"
                        
                        # Look for matching layer in manifest
                        for layer in manifest.get("layers", []):
                            if layer.get("digest") == target_digest:
                                target_layer = layer
                                break
                
                if not target_layer:
                    # If no checksum provided or no match found, use the last layer (commonly the application layer)
                    layers = manifest.get("layers", [])
                    if not layers:
                        raise FetchError(f"No layers found in manifest for {oci_url}")
                    target_layer = layers[-1]
                    log.warning(f"No matching layer found for checksum {checksum}, using last layer")
                
                # Download the target layer blob
                layer_digest = target_layer["digest"]
                blob_url = f"{registry_url}/v2/{repo}/blobs/{layer_digest}"
                
                log.debug(f"Downloading layer blob: {layer_digest}")
                
                async with session.get(blob_url, auth=auth, ssl=ssl_context) as blob_resp:
                    if blob_resp.status != 200:
                        raise FetchError(f"Failed to download layer blob from {blob_url}: HTTP {blob_resp.status}")
                    
                    # Write the blob directly to the download path
                    with open(download_path, 'wb') as f:
                        async for chunk in blob_resp.content.iter_chunked(8192):
                            f.write(chunk)
                    
                    log.debug(f"OCI download completed - {oci_url}")
                    return
            
    except Exception as exception:
        log.error(f"OCI download failed: {oci_url}")
        raise FetchError(
            f"OCI download failed - exception_name: {exception.__class__.__name__}, "
            f"details: {exception}"
        ) from None


async def async_download_files(
    files_to_download: dict[str, Union[str, PathLike[str]]],
    concurrency_limit: int,
    ssl_context: Optional[ssl.SSLContext] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Asynchronous function to download files.

    :param files_to_download: Dict of files to download with file paths
    :param concurrency_limit: Max number of concurrent tasks (downloads).
    :param metadata: Optional metadata dict indexed by file path, containing checksum info.
    """
    trace_config = aiohttp.TraceConfig()
    num_attempts: int = int(DEFAULT_RETRY_OPTIONS["total"])
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
            if len(tasks) >= concurrency_limit:
                # Wait for some download to finish before adding a new one
                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                # Check for exceptions
                try:
                    await asyncio.gather(*done)
                except FetchError:
                    # Close retry_client if any request fails (other tasks can be running,
                    # if a task is closed with the client open, an Warning is raised).
                    await retry_client.close()
                    for t in tasks:
                        t.cancel()
                    raise

            # Route to appropriate download function based on URL scheme
            if url.startswith("oci://"):
                # Get checksum from metadata if available
                file_metadata = metadata.get(download_path, {}) if metadata else {}
                checksum = file_metadata.get("checksum")
                task = _async_download_oci_file(url, download_path, checksum, ssl_context=ssl_context)
            else:
                task = _async_download_binary_file(
                    session, url, download_path, ssl_context=ssl_context
                )
            
            tasks.add(asyncio.create_task(task))

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
