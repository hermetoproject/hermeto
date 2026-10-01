# SPDX-License-Identifier: GPL-3.0-only
"""Prefetch Ansible Galaxy collections from ansible.lock.yaml."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from hermeto.core.checksum import must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound, PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import ProjectFile, RequestOutput
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.ansible.models import (
    AnsibleCollectionLockEntry,
    AnsibleLockfile,
    find_ansible_cfg,
    parse_galaxy_servers,
)
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE_NAME = "ansible.lock.yaml"
DEFAULT_DEPS_DIR = "deps/ansible"

REQUIREMENTS_CANDIDATES = (
    "requirements.yml",
    "requirements.yaml",
    "collections/requirements.yml",
    "collections/requirements.yaml",
)

Url = str
AuthHeaders = dict[str, str]


def fetch_ansible_source(request: Request) -> RequestOutput:
    """Resolve and fetch Ansible collection dependencies for a request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []

    for package in request.ansible_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        lockfile_path = _resolve_lockfile_path(
            request.source_dir,
            package.path,
            package.lockfile,
        )
        resolved = _resolve_ansible_lockfile(
            lockfile_path=lockfile_path,
            output_dir=request.output_dir,
            package_dir=package_dir.path,
            source_root=request.source_dir.path,
        )
        components.extend(resolved["components"])
        project_files.extend(
            _rewrite_requirements_files(
                package_dir=package_dir,
                collections=resolved["collections"],
                filename_by_fqcn=resolved["filename_by_fqcn"],
            )
        )

    annotations = []
    if backend_annotation := create_backend_annotation(components, "x-ansible"):
        annotations.append(backend_annotation)

    return RequestOutput.from_obj_list(
        components=components,
        project_files=project_files,
        annotations=annotations,
    )


def _resolve_lockfile_path(
    source_dir: RootedPath,
    package_path: Path,
    lockfile_path: Path | None,
) -> Path:
    if lockfile_path and lockfile_path.is_absolute():
        return lockfile_path

    path = source_dir.join_within_root(package_path)
    lockfile_name = lockfile_path or DEFAULT_LOCKFILE_NAME
    lockfile = path.join_within_root(lockfile_name).path

    if not lockfile.is_relative_to(path.path):
        raise PackageRejected(
            f"Supplied ansible lockfile path '{lockfile_name}' must be inside the package "
            f"path '{package_path}'.",
            solution="Use a lockfile path located within the package path.",
        )
    return lockfile


def _resolve_ansible_lockfile(
    *,
    lockfile_path: Path,
    output_dir: RootedPath,
    package_dir: Path,
    source_root: Path,
) -> dict[str, Any]:
    if not lockfile_path.exists():
        raise LockfileNotFound(files=lockfile_path)

    deps_dir = output_dir.re_root(DEFAULT_DEPS_DIR)
    log.info("Reading ansible lockfile: %s", lockfile_path)
    lockfile = _load_lockfile(lockfile_path)

    cfg_path = find_ansible_cfg(package_dir, source_root)
    servers = parse_galaxy_servers(cfg_path) if cfg_path else {}
    if cfg_path:
        log.info("Using ansible.cfg %s for galaxy auth", cfg_path)

    to_download: dict[Url, str | os.PathLike[str]] = {}
    auth_headers: dict[Url, AuthHeaders] = {}
    filename_by_fqcn: dict[str, str] = {}
    collections: list[AnsibleCollectionLockEntry] = []

    for collection in lockfile.collections:
        dest_name = collection.artifact_filename
        dest_path = deps_dir.join_within_root(dest_name).path
        filename_by_fqcn[collection.name] = dest_name

        Path.mkdir(dest_path.parent, parents=True, exist_ok=True)
        url = collection.url
        to_download[url] = dest_path
        headers = _auth_headers_for_collection(collection, servers)
        if headers:
            auth_headers[url] = headers
        collections.append(collection)

    if to_download:
        asyncio.run(
            async_download_files(
                to_download,
                get_config().runtime.concurrency_limit,
                headers=auth_headers or None,
            )
        )

    for collection in collections:
        dest_path = deps_dir.join_within_root(collection.artifact_filename).path
        must_match_any_checksum(dest_path, [collection.formatted_checksum])

    components = [c.get_sbom_component() for c in collections]
    return {
        "components": components,
        "collections": collections,
        "filename_by_fqcn": filename_by_fqcn,
    }


def _auth_headers_for_collection(
    collection: AnsibleCollectionLockEntry,
    servers: dict[str, Any],
) -> AuthHeaders | None:
    if not collection.server or collection.server not in servers:
        return None
    return servers[collection.server].authorization_header()


def _load_lockfile(lockfile_path: Path) -> AnsibleLockfile:
    with open(lockfile_path) as fh:
        try:
            data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise InvalidLockfileFormat(
                lockfile_path=lockfile_path,
                err_details=str(exc),
                solution="Check correct 'yaml' syntax in the lockfile.",
            ) from exc

    if not data:
        raise InvalidLockfileFormat(
            lockfile_path=lockfile_path,
            err_details="Lockfile is empty",
            solution="Ensure the lockfile contains collections entries.",
        )

    try:
        return AnsibleLockfile.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise InvalidLockfileFormat(
            lockfile_path=lockfile_path,
            err_details=f"{err.get('loc')}: {err.get('msg')}",
            solution="Check the ansible.lock.yaml schema (name, version, url, checksum).",
        ) from exc


def _rewrite_requirements_files(
    *,
    package_dir: RootedPath,
    collections: list[AnsibleCollectionLockEntry],
    filename_by_fqcn: dict[str, str],
) -> list[ProjectFile]:
    """Rewrite requirements files so collection names point at cached tarballs."""
    if not filename_by_fqcn:
        return []

    # Map artifact basename and FQCN for matching path-style entries.
    basename_to_fqcn = {fn: fqcn for fqcn, fn in filename_by_fqcn.items()}
    project_files: list[ProjectFile] = []

    for rel in REQUIREMENTS_CANDIDATES:
        req_path = package_dir.join_within_root(rel).path
        if not req_path.is_file():
            continue
        rewritten = _rewrite_one_requirements_file(
            req_path, filename_by_fqcn, basename_to_fqcn
        )
        if rewritten is not None:
            project_files.append(rewritten)

    # Also search one level of subdirs commonly used by vendor trees (ee-supported/).
    for child in sorted(package_dir.path.iterdir()) if package_dir.path.is_dir() else []:
        if not child.is_dir() or child.name.startswith("."):
            continue
        for name in ("requirements.yml", "requirements.yaml"):
            req_path = child / name
            if not req_path.is_file():
                continue
            rewritten = _rewrite_one_requirements_file(
                req_path, filename_by_fqcn, basename_to_fqcn
            )
            if rewritten is not None:
                project_files.append(rewritten)

    return project_files


def _rewrite_one_requirements_file(
    req_path: Path,
    filename_by_fqcn: dict[str, str],
    basename_to_fqcn: dict[str, str],
) -> ProjectFile | None:
    with req_path.open(encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh)
        except yaml.YAMLError:
            log.warning("Skipping unreadable requirements file %s", req_path)
            return None

    if not isinstance(data, dict) or not isinstance(data.get("collections"), list):
        return None

    changed = False
    new_collections: list[Any] = []
    for entry in data["collections"]:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict) or "name" not in entry:
            new_collections.append(entry)
            continue

        raw_name = str(entry["name"])
        fqcn = None
        if raw_name in filename_by_fqcn:
            fqcn = raw_name
        else:
            base = Path(raw_name).name
            if base in basename_to_fqcn:
                fqcn = basename_to_fqcn[base]

        if fqcn is None:
            new_collections.append(entry)
            continue

        artifact = filename_by_fqcn[fqcn]
        templated = f"${{output_dir}}/{DEFAULT_DEPS_DIR}/{artifact}"
        new_entry = {"name": templated}
        # Drop version/source when pointing at a local archive.
        changed = True
        new_collections.append(new_entry)

    if not changed:
        return None

    new_data = dict(data)
    new_data["collections"] = new_collections
    content = yaml.safe_dump(new_data, default_flow_style=False, sort_keys=False)
    log.info("Rewriting %s to use cached ansible collections", req_path)
    return ProjectFile(abspath=req_path.resolve(), template=content)
