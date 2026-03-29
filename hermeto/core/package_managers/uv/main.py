# SPDX-License-Identifier: GPL-3.0-only
import logging
from pathlib import Path
from typing import TypedDict
from urllib.parse import unquote, urlparse

from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.errors import UnsupportedFeature
from hermeto.core.models.input import Mode, Request
from hermeto.core.models.output import RequestOutput
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.general import download_binary_file
from hermeto.core.package_managers.uv.lockfile import parse_uv_lockfile

log = logging.getLogger(__name__)


class _ArtifactRecord(TypedDict):
    url: str
    hash: str | None


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
            source_kind = _source_kind(package_entry)

            if source_kind not in {"registry", "virtual"}:
                if _should_skip_unsupported_source(
                    source_kind=source_kind,
                    package_name=package_name if isinstance(package_name, str) else "<unknown>",
                    mode=request.mode,
                ):
                    continue

            if isinstance(package_name, str):
                purl = PackageURL(type="pypi", name=package_name, version=package_version).to_string()
                components.append(
                    Component(name=package_name, version=package_version, purl=purl)
                )

            if source_kind == "registry":
                for artifact in _iter_remote_artifacts(package_entry):
                    if artifact["url"] in downloaded_urls:
                        continue

                    filename = _artifact_filename(artifact["url"])
                    destination = deps_dir.join_within_root(filename).path

                    log.info("Downloading uv artifact %s", artifact["url"])
                    download_binary_file(artifact["url"], destination)

                    if artifact["hash"]:
                        must_match_any_checksum(
                            destination, [ChecksumInfo.from_hash(artifact["hash"])]
                        )

                    downloaded_urls.add(artifact["url"])

    annotations = []
    if backend_annotation := create_backend_annotation(components, "x-uv"):
        annotations.append(backend_annotation)

    return RequestOutput.from_obj_list(components=components, annotations=annotations)

def _source_kind(package_entry: dict) -> str:
    source = package_entry.get("source")
    if not isinstance(source, dict):
        return "unknown"

    for kind in ["registry", "virtual", "git", "path", "editable", "directory"]:
        if kind in source:
            return kind

    return "unknown"


def _should_skip_unsupported_source(source_kind: str, package_name: str, mode: Mode) -> bool:
    reason = (
        f"uv package '{package_name}' uses source kind '{source_kind}', "
        "which is not supported yet by the experimental x-uv backend"
    )

    if mode == Mode.PERMISSIVE:
        log.warning("%s; continuing due to permissive mode", reason)
        return True

    raise UnsupportedFeature(
        reason,
        solution=(
            "Use permissive mode to continue without this dependency, or update input to use "
            "registry-backed dependencies only for now."
        ),
    )


def _iter_remote_artifacts(package_entry: dict) -> list[_ArtifactRecord]:
    artifacts: list[_ArtifactRecord] = []

    def _append_remote_artifact(data: object) -> None:
        if not isinstance(data, dict):
            return

        url = data.get("url")
        if not isinstance(url, str):
            return

        hash_ = data.get("hash")
        artifacts.append({"url": url, "hash": hash_ if isinstance(hash_, str) else None})

    _append_remote_artifact(package_entry.get("sdist"))

    wheels = package_entry.get("wheels")
    if isinstance(wheels, list):
        for wheel in wheels:
            _append_remote_artifact(wheel)

    return artifacts


def _artifact_filename(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name)
    if name and name not in ("/", "\\"):
        return name

    return "artifact.bin"
