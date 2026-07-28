# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import aiohttp
import aiohttp_retry

from hermeto.core.config import get_config
from hermeto.core.errors import FetchError
from hermeto.core.package_managers.oci.models import OciImage, OciPlatform

log = logging.getLogger(__name__)

BACKOFF_FACTOR = 1.3
STATUS_FORCELIST = (429, 500, 502, 503, 504)

INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)

MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)

ACCEPT_HEADER = ", ".join(sorted(INDEX_MEDIA_TYPES | MANIFEST_MEDIA_TYPES))


def _scheme_for(registry: str) -> str:
    """Return 'http' for localhost registries, 'https' otherwise."""
    if registry == "localhost" or registry.startswith("localhost:"):
        return "http"
    return "https"


class _TokenExpiredError(FetchError):
    """A registry returned 401, indicating the cached token may have expired."""


class OciRegistryClient:
    """HTTP client for the OCI Distribution API."""

    def __init__(self, session: aiohttp_retry.RetryClient) -> None:
        """Initialize the client with an aiohttp retry session."""
        self._session = session
        self._tokens: dict[str, str] = {}
        self._auth_locks: dict[str, asyncio.Lock] = {}

    async def _authenticate(
        self,
        registry: str,
        repo_path: str,
        auth_headers: dict[str, str] | None,
        *,
        force: bool = False,
    ) -> str | None:
        """Obtain a bearer token via the Docker token auth flow.

        Returns the token string, or None if the registry does not require auth.
        """
        cache_key = f"{registry}/{repo_path}"
        if not force and cache_key in self._tokens:
            return self._tokens[cache_key]

        if cache_key not in self._auth_locks:
            self._auth_locks[cache_key] = asyncio.Lock()

        async with self._auth_locks[cache_key]:
            if not force and cache_key in self._tokens:
                return self._tokens[cache_key]

            scheme = _scheme_for(registry)

            ping_url = f"{scheme}://{registry}/v2/"
            async with self._session.get(ping_url) as resp:
                if resp.status == 200:
                    return None
                if resp.status != 401:
                    raise FetchError(
                        f"Unexpected status {resp.status} from {ping_url}. "
                        "Verify the registry URL is correct."
                    )
                www_auth = resp.headers.get("WWW-Authenticate", "")

            realm, service, scope = parse_www_authenticate(www_auth, repo_path)

            params: dict[str, str] = {}
            if service:
                params["service"] = service
            params["scope"] = scope

            headers: dict[str, str] = {}
            if auth_headers:
                headers.update(auth_headers)

            async with self._session.get(realm, params=params, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise FetchError(
                        f"Failed to authenticate with {registry} for {repo_path}: "
                        f"HTTP {resp.status}: {body}. "
                        "Verify your registry credentials are correct."
                    )
                data = await resp.json()

            token = data.get("token") or data.get("access_token")
            if not token:
                raise FetchError(
                    f"Token endpoint at {realm} did not return a token. "
                    "This may indicate an unsupported registry authentication flow."
                )

            self._tokens[cache_key] = token
            return token

    async def fetch_manifest(
        self,
        image: OciImage,
    ) -> tuple[bytes, dict[str, Any]]:
        """Fetch and verify a manifest from a registry.

        Returns (raw_bytes, parsed_json).
        """
        auth_headers = image.auth.get_headers() if image.auth else None

        scheme = _scheme_for(image.api_host)

        url = f"{scheme}://{image.api_host}/v2/{image.repo_path}/manifests/{image.digest}"

        for attempt in range(2):
            token = await self._authenticate(
                image.api_host, image.repo_path, auth_headers, force=(attempt > 0)
            )
            headers: dict[str, str] = {"Accept": ACCEPT_HEADER}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            async with self._session.get(url, headers=headers) as resp:
                if resp.status == 401 and attempt == 0 and token is not None:
                    continue
                if resp.status != 200:
                    body = await resp.text()
                    raise FetchError(
                        f"Failed to fetch manifest for {image.repository}@{image.digest}: "
                        f"HTTP {resp.status}: {body}. "
                        "Verify the digest exists in the registry and that you have pull access."
                    )
                manifest_bytes = await resp.read()
            break

        actual_digest = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
        if actual_digest != image.digest:
            raise FetchError(
                f"Manifest digest mismatch for {image.repository}: "
                f"expected {image.digest}, got {actual_digest}. "
                "The registry may have returned unexpected content."
            )

        manifest = json.loads(manifest_bytes)
        return manifest_bytes, manifest

    async def resolve_manifest(
        self,
        image: OciImage,
    ) -> tuple[bytes, dict[str, Any], str, str]:
        """Fetch a manifest and resolve image indexes to a platform-specific manifest.

        Returns (manifest_bytes, manifest_json, resolved_digest, resolved_media_type).
        For direct manifests, resolved values equal the image's own digest and media_type.
        """
        manifest_bytes, manifest = await self.fetch_manifest(image)

        if image.media_type not in INDEX_MEDIA_TYPES:
            return manifest_bytes, manifest, image.digest, image.media_type

        if not image.platform:
            raise FetchError(
                f"Image {image.repository}@{image.digest} is a multi-arch image index, "
                "but no 'platform' was specified in the lockfile. "
                "Please add a platform selector (e.g. platform: {os: linux, architecture: amd64})."
            )

        platform_digest, platform_media_type = select_platform(
            manifest, image.platform, image.repository
        )

        platform_image = OciImage(
            repository=image.repository,
            digest=platform_digest,
            media_type=platform_media_type,
            auth=image.auth,
        )
        platform_bytes, platform_manifest = await self.fetch_manifest(platform_image)
        return platform_bytes, platform_manifest, platform_digest, platform_media_type

    async def download_blobs(
        self,
        image: OciImage,
        manifest: dict[str, Any],
        output_dir: Path,
        sem: asyncio.Semaphore,
    ) -> None:
        """Download all blobs (config + layers) for a manifest to the output directory."""
        descriptors = extract_blob_descriptors(manifest)
        auth_headers = image.auth.get_headers() if image.auth else None

        scheme = _scheme_for(image.api_host)

        for attempt in range(2):
            token = await self._authenticate(
                image.api_host, image.repo_path, auth_headers, force=(attempt > 0)
            )

            downloads: dict[str, tuple[Path, str]] = {}
            headers_map: dict[str, dict[str, str]] = {}

            for desc in descriptors:
                digest = desc["digest"]
                algorithm, hex_digest = digest.split(":", 1)
                blobs_dir = output_dir / "blobs" / algorithm
                blobs_dir.mkdir(parents=True, exist_ok=True)
                blob_path = blobs_dir / hex_digest

                if blob_path.exists():
                    continue

                url = f"{scheme}://{image.api_host}/v2/{image.repo_path}/blobs/{digest}"
                downloads[url] = (blob_path, digest)

                if token:
                    headers_map[url] = {"Authorization": f"Bearer {token}"}

            if not downloads:
                return

            try:
                await _download_and_verify_blobs(self._session, downloads, headers_map, sem)
                break
            except _TokenExpiredError:
                if attempt > 0:
                    raise


async def _download_and_verify_blobs(
    session: aiohttp_retry.RetryClient,
    downloads: dict[str, tuple[Path, str]],
    headers_map: dict[str, dict[str, str]],
    sem: asyncio.Semaphore,
) -> None:
    """Download blobs concurrently and verify their checksums."""

    async def _download_one(url: str, path: Path, expected_digest: str) -> None:
        async with sem:
            headers = headers_map.get(url, {})
            async with session.get(url, headers=headers) as resp:
                if resp.status == 401:
                    raise _TokenExpiredError(f"Token expired while downloading blob {url}")
                if resp.status != 200:
                    body = await resp.text()
                    raise FetchError(
                        f"Failed to download blob {url}: HTTP {resp.status}: {body}. "
                        "Check registry connectivity and rate limits."
                    )

                algorithm = expected_digest.split(":", 1)[0]
                hasher = hashlib.new(algorithm)
                tmp_path = path.with_suffix(".tmp")
                with tmp_path.open("wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        f.write(chunk)
                        hasher.update(chunk)

            actual = f"{algorithm}:{hasher.hexdigest()}"
            if actual != expected_digest:
                tmp_path.unlink(missing_ok=True)
                raise FetchError(
                    f"Blob checksum mismatch: expected {expected_digest}, got {actual}"
                )

            tmp_path.rename(path)

    async_tasks = [
        asyncio.create_task(_download_one(url, path, digest))
        for url, (path, digest) in downloads.items()
    ]
    try:
        await asyncio.gather(*async_tasks)
    except BaseException:
        for task in async_tasks:
            task.cancel()
        await asyncio.gather(*async_tasks, return_exceptions=True)
        for path, _ in downloads.values():
            path.with_suffix(".tmp").unlink(missing_ok=True)
        raise


def parse_www_authenticate(header: str, repo_path: str) -> tuple[str, str, str]:
    """Parse a WWW-Authenticate Bearer challenge header.

    Returns (realm, service, scope).
    """
    if not header.lower().startswith("bearer "):
        raise FetchError(
            f"Unsupported WWW-Authenticate scheme: '{header}'. "
            "Only Bearer authentication is supported."
        )

    params_str = header[len("bearer ") :]
    params: dict[str, str] = {}
    for match in re.finditer(r'(\w+)="([^"]*)"', params_str):
        params[match.group(1)] = match.group(2)

    realm = params.get("realm", "")
    if not realm:
        raise FetchError("Registry WWW-Authenticate header is missing the 'realm' parameter.")

    service = params.get("service", "")
    scope = params.get("scope", f"repository:{repo_path}:pull")

    return realm, service, scope


def select_platform(
    index: dict[str, Any],
    platform: OciPlatform,
    repository: str,
) -> tuple[str, str]:
    """Select a platform-specific manifest from an image index.

    Returns (digest, mediaType).
    """
    manifests = index.get("manifests", [])

    for entry in manifests:
        entry_platform = entry.get("platform", {})
        if platform.matches(entry_platform):
            return entry["digest"], entry["mediaType"]

    available = [
        f"{m.get('platform', {}).get('os', '?')}/{m.get('platform', {}).get('architecture', '?')}"
        for m in manifests
    ]
    raise FetchError(
        f"No manifest found for platform {platform.os}/{platform.architecture} "
        f"in image index {repository}. "
        f"Available platforms: {', '.join(available)}"
    )


def extract_blob_descriptors(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract all blob descriptors (config + layers) from a manifest."""
    descriptors: list[dict[str, Any]] = []

    config = manifest.get("config")
    if config:
        descriptors.append(config)

    for layer in manifest.get("layers", []):
        descriptors.append(layer)

    return descriptors


def create_retry_client() -> aiohttp_retry.RetryClient:
    """Create an aiohttp retry client with standard hermeto settings."""
    max_retries = get_config().http.max_retries + 1
    retry_options = aiohttp_retry.JitterRetry(
        start_timeout=BACKOFF_FACTOR,
        attempts=max_retries,
        statuses=set(STATUS_FORCELIST),
        exceptions={
            aiohttp.ClientConnectionError,
            aiohttp.ClientPayloadError,
        },
    )

    return aiohttp_retry.RetryClient(
        retry_options=retry_options,
        trust_env=True,
        requote_redirect_url=False,
    )
