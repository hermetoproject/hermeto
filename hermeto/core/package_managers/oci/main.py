# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import json
import logging
from pathlib import Path

from packageurl import PackageURL

from hermeto.core.config import get_config
from hermeto.core.models.input import Request
from hermeto.core.models.output import Component, EnvironmentVariable, RequestOutput
from hermeto.core.models.sbom import Annotation, create_backend_annotation
from hermeto.core.package_managers.oci.models import DEFAULT_LOCKFILE, OciImage, OciLockfile
from hermeto.core.package_managers.oci.oci_client import OciRegistryClient, create_retry_client

log = logging.getLogger(__name__)

OCI_IMAGE_LAYOUT = json.dumps({"imageLayoutVersion": "1.0.0"})


def fetch_oci_source(request: Request) -> RequestOutput:
    """Resolve and fetch OCI image dependencies for the given request."""
    annotations: list[Annotation] = []
    components: list[Component] = []

    deps_dir = request.output_dir.join_within_root("deps", "x-oci")
    deps_dir.path.mkdir(parents=True, exist_ok=True)

    for package in request.oci_packages:
        project_dir = request.source_dir.join_within_root(package.path)
        lockfile_path = project_dir.path / DEFAULT_LOCKFILE
        lockfile = OciLockfile.from_file(lockfile_path)

        components.extend(_fetch_oci_images(lockfile, deps_dir.path))

    if backend_annotation := create_backend_annotation(components, "x-oci"):
        annotations.append(backend_annotation)

    return RequestOutput.from_obj_list(
        annotations=annotations,
        components=components,
        environment_variables=[
            EnvironmentVariable(
                name="HERMETO_OCI_LAYOUT_DIR",
                value="${output_dir}/deps/x-oci",
            ),
        ],
    )


def _fetch_oci_images(lockfile: OciLockfile, deps_dir: Path) -> list[Component]:
    """Fetch all images declared in a lockfile and return SBOM components."""

    async def _fetch_all() -> list[Component]:
        concurrency = get_config().runtime.concurrency_limit
        sem = asyncio.Semaphore(concurrency)
        client_session = create_retry_client()
        async with client_session as session:
            client = OciRegistryClient(session)
            tasks = [_fetch_single_image(client, image, deps_dir, sem) for image in lockfile.images]
            return list(await asyncio.gather(*tasks))

    return asyncio.run(_fetch_all())


async def _fetch_single_image(
    client: OciRegistryClient,
    image: OciImage,
    deps_dir: Path,
    sem: asyncio.Semaphore,
) -> Component:
    """Fetch a single OCI image and write it as an OCI Image Layout."""
    log.info("Fetching OCI image %s@%s", image.repository, image.digest)

    manifest_bytes, manifest, resolved_digest, resolved_media_type = await client.resolve_manifest(
        image
    )
    image_dir = deps_dir / image.sanitized_name
    image_dir.mkdir(parents=True, exist_ok=True)

    _write_oci_layout(image_dir)
    _write_manifest_blob(image_dir, manifest_bytes, resolved_digest)
    _write_index_json(image_dir, resolved_digest, manifest_bytes, resolved_media_type)

    await client.download_blobs(image, manifest, image_dir, sem)

    log.info("Fetched OCI image %s@%s", image.repository, image.digest)
    return _create_sbom_component(image)


def _write_oci_layout(image_dir: Path) -> None:
    """Write the oci-layout marker file."""
    layout_file = image_dir / "oci-layout"
    layout_file.write_text(OCI_IMAGE_LAYOUT + "\n")


def _write_manifest_blob(image_dir: Path, manifest_bytes: bytes, digest: str) -> None:
    """Write the manifest as a blob."""
    algorithm, hex_digest = digest.split(":", 1)
    blobs_dir = image_dir / "blobs" / algorithm
    blobs_dir.mkdir(parents=True, exist_ok=True)
    (blobs_dir / hex_digest).write_bytes(manifest_bytes)


def _write_index_json(
    image_dir: Path, manifest_digest: str, manifest_bytes: bytes, media_type: str
) -> None:
    """Write the top-level index.json referencing the fetched manifest."""
    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {
                "mediaType": media_type,
                "digest": manifest_digest,
                "size": len(manifest_bytes),
            }
        ],
    }
    (image_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n")


def _create_sbom_component(image: OciImage) -> Component:
    """Create an SBOM component for a fetched OCI image."""
    name = image.repo_path.rsplit("/", 1)[-1]

    qualifiers: dict[str, str] = {
        "repository_url": f"{image.registry}/{image.repo_path}",
    }
    if image.tag:
        qualifiers["tag"] = image.tag

    purl = PackageURL(
        type="oci",
        name=name,
        version=image.digest,
        qualifiers=qualifiers,
    )

    return Component(
        name=name,
        purl=purl.to_string(),
        version=image.digest,
    )
