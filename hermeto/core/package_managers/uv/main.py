# SPDX-License-Identifier: GPL-3.0-only
import logging
from pathlib import Path
from urllib.parse import unquote, urlparse

from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.models.input import Request
from hermeto.core.models.output import RequestOutput
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.general import download_binary_file
from hermeto.core.package_managers.uv.lockfile import parse_uv_lockfile

log = logging.getLogger(__name__)


def fetch_uv_source(request: Request) -> RequestOutput:
    """Resolve and fetch dependencies for the experimental uv backend."""
    components: list[Component] = []

    deps_dir = request.output_dir.re_root("deps/uv")
    downloaded_urls: set[str] = set()

    for package in request.uv_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        lockfile = parse_uv_lockfile(package_dir)

        for package_entry in lockfile.get("package", []):
            package_name = package_entry.get("name")
            package_version = package_entry.get("version")

            if isinstance(package_name, str):
                purl = PackageURL(type="pypi", name=package_name, version=package_version).to_string()
                components.append(
                    Component(name=package_name, version=package_version, purl=purl)
                )

            for artifact in _iter_remote_artifacts(package_entry):
                if artifact["url"] in downloaded_urls:
                    continue

                filename = _artifact_filename(artifact["url"])
                destination = deps_dir.join_within_root(filename).path

                log.info("Downloading uv artifact %s", artifact["url"])
                download_binary_file(artifact["url"], destination)

                if artifact["hash"]:
                    must_match_any_checksum(destination, [ChecksumInfo.from_hash(artifact["hash"])])

                downloaded_urls.add(artifact["url"])

    annotations = []
    if backend_annotation := create_backend_annotation(components, "x-uv"):
        annotations.append(backend_annotation)

    return RequestOutput.from_obj_list(components=components, annotations=annotations)


def _iter_remote_artifacts(package_entry: dict) -> list[dict[str, str | None]]:
    artifacts: list[dict[str, str | None]] = []

    sdist = package_entry.get("sdist")
    if isinstance(sdist, dict):
        url = sdist.get("url")
        hash_ = sdist.get("hash")
        if isinstance(url, str):
            artifacts.append({"url": url, "hash": hash_ if isinstance(hash_, str) else None})

    wheels = package_entry.get("wheels")
    if isinstance(wheels, list):
        for wheel in wheels:
            if not isinstance(wheel, dict):
                continue
            url = wheel.get("url")
            hash_ = wheel.get("hash")
            if isinstance(url, str):
                artifacts.append({"url": url, "hash": hash_ if isinstance(hash_, str) else None})

    return artifacts


def _artifact_filename(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name)
    if name:
        return name

    return "artifact.bin"
