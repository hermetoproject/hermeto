# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import base64
import copy
import hashlib
import json
import tarfile
from pathlib import Path

import aiohttp
import yaml

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.models.input import Request
from hermeto.core.models.output import (
    Annotation,
    Component,
    EnvironmentVariable,
    ProjectFile,
    RequestOutput,
)
from hermeto.core.models.sbom import create_backend_annotation
from hermeto.core.package_managers.javascript.npm import (
    NPM_REGISTRY_URL,
    async_download_with_auth,
    patch_url_to_point_to_proxy,
)
from hermeto.core.package_managers.javascript.pnpm.project import (
    PnpmLock,
    PnpmPackage,
    parse_packages,
)
from hermeto.core.package_managers.javascript.pnpm.resolver import generate_sbom_components

# pnpm keys each package's store index file by the first 32 bytes (64 hex chars) of
# the SHA-512 digest carried in the package integrity SRI: the first 2 hex chars name
# the subdirectory, the remaining 62 form the filename prefix.
_PNPM_STORE_INDEX_HEX_LEN = 64


def fetch_pnpm_source(request: Request) -> RequestOutput:
    """Process all pnpm source directories in the given request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []
    annotations: list[Annotation] = []

    deps_dir = request.output_dir.path.joinpath("deps", "pnpm")
    deps_dir.mkdir(parents=True, exist_ok=True)

    # A content-addressable store lets pnpm resolve packages offline by their integrity
    # hash, so the lockfile keeps its registry-style resolutions untouched. pnpm
    # >=10.34.2 rejects a registry dependency whose resolution was rewritten to a local
    # "tarball: file://" (ERR_PNPM_RESOLUTION_SHAPE_MISMATCH); the store sidesteps that.
    store_dir = request.output_dir.path.joinpath("pnpm-store")

    for package in request.pnpm_packages:
        project_dir = request.source_dir.join_within_root(package.path)
        lockfile = PnpmLock.from_dir(project_dir.path)
        packages, updated_lockfile = _resolve_pnpm_project(deps_dir, store_dir, lockfile)
        project_files.append(updated_lockfile)
        components.extend(generate_sbom_components(project_dir, packages, lockfile))

    if backend_annotation := create_backend_annotation(components, "x-pnpm"):
        annotations.append(backend_annotation)

    return RequestOutput.from_obj_list(
        components=components,
        project_files=project_files,
        environment_variables=[
            # pnpm reads the store location from the npm-style NPM_CONFIG_STORE_DIR;
            # with "pnpm install --offline" it then resolves from the store with no
            # network access and no lockfile edits.
            EnvironmentVariable(name="NPM_CONFIG_STORE_DIR", value="${output_dir}/pnpm-store"),
        ],
        annotations=annotations,
    )


def _resolve_pnpm_project(
    deps_dir: Path, store_dir: Path, lockfile: PnpmLock
) -> tuple[list[PnpmPackage], ProjectFile]:
    """Resolve a pnpm project."""
    packages = parse_packages(lockfile)
    non_local = [p for p in packages if not p.url.startswith("file:")]
    _download_resolved_packages(non_local, deps_dir)

    # pnpm >=10.34.2 rejects only a *registry-shaped* lockfile key (name@<semver>) whose
    # resolution was rewritten to a local "tarball: file://"
    # (ERR_PNPM_RESOLUTION_SHAPE_MISMATCH), so those are served from the content-addressable
    # store with their resolution left untouched. Non-registry keys (git/URL deps) are exempt
    # from that check and have no integrity-addressed store slot to populate, so they keep the
    # file:// resolution rewrite that has always worked for them.
    store_packages = [p for p in non_local if p.registry_shaped]
    rewritten_packages = [p for p in non_local if not p.registry_shaped]

    for package in store_packages:
        if package.integrity is not None:
            _add_tarball_to_pnpm_store(
                tarball_path=deps_dir / package.tarball_filename,
                store_dir=store_dir,
                name=package.name,
                scope=package.scope,
                version=package.version,
                pkg_integrity=package.integrity,
            )
    return packages, _prepare_lockfile_for_hermetic_build(lockfile, rewritten_packages)


def _download_resolved_packages(packages: list[PnpmPackage], deps_dir: Path) -> None:
    config = get_config()
    proxy_url = config.pnpm.proxy_url
    proxy_login = config.pnpm.proxy_login
    proxy_password = config.pnpm.proxy_password

    auth = None
    if proxy_login is not None and proxy_password is not None:
        auth = aiohttp.encode_basic_auth(login=proxy_login, password=proxy_password)

    files_with_auth = {}
    files_without_auth = {}
    for package in packages:
        tarball_path = deps_dir / package.tarball_filename

        # non-registry packages, or no proxy is configured
        if not package.url.startswith(NPM_REGISTRY_URL) or proxy_url is None:
            files_without_auth[package.url] = tarball_path
            continue

        actual_url = patch_url_to_point_to_proxy(package.url, proxy_url)
        if auth is not None:
            files_with_auth[actual_url] = tarball_path
        else:
            files_without_auth[actual_url] = tarball_path

    asyncio.run(
        async_download_with_auth(
            files_without_auth=files_without_auth, files_with_auth=files_with_auth, auth=auth
        )
    )

    for package in packages:
        if package.integrity is not None:
            must_match_any_checksum(
                file_path=deps_dir / package.tarball_filename,
                expected_checksums=[ChecksumInfo.from_sri(package.integrity)],
            )


def _sri_to_pnpm_store_key(sri: str) -> tuple[str, str]:
    """Convert a SHA-512 integrity SRI to the (subdir, filename-prefix) of its store index file.

    >>> _sri_to_pnpm_store_key("sha512-v2kDEe57lecTulaDIuNTPy3Ry4gLGJ6Z1O3vE1krgXZNrsQ+LFTGHVxVjcXPs17LhbZR/iIBfbufAb6wWgzA==")
    ('bf', '690311ee7b95e713ba568322e3533f2dd1cb880b189e99d4edef13592b8176')
    """
    raw = base64.b64decode(sri.removeprefix("sha512-"))
    hex_val = raw.hex()
    return hex_val[0:2], hex_val[2:_PNPM_STORE_INDEX_HEX_LEN]


def _sha512_hexdigest(path: Path) -> str:
    """Return the hex SHA-512 digest of a file, read in chunks to bound memory."""
    hasher = hashlib.sha512()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _add_tarball_to_pnpm_store(
    tarball_path: Path,
    store_dir: Path,
    name: str,
    scope: str,
    version: str,
    pkg_integrity: str,
) -> None:
    """Unpack a downloaded npm tarball into pnpm's content-addressable store ("v10").

    The store lets ``pnpm install --offline`` materialise the package from its integrity
    hash without any ``tarball`` URL in the lockfile. The on-disk layout mirrors what
    pnpm itself writes, since pnpm hardlinks the stored files into ``node_modules`` and
    reads its own index, so any deviation makes pnpm treat the package as missing:

    - ``files/<sha512_hex[0:2]>/<sha512_hex[2:]>`` holds each file's raw content;
      executable files (any ``x`` mode bit) get a ``-exec`` suffix and are stored as
      ``0o750`` so the hardlinked copy stays executable, the rest as ``0o640`` -- pnpm
      does not re-apply the index ``mode`` to the hardlink, so the stored file's mode
      is what the installed file gets.
    - ``index/<int_hex[0:2]>/<int_hex[2:64]>-<pkg>@<ver>.json`` holds per-package
      metadata keyed by the package integrity. ``requiresBuild`` must reflect whether
      the package has an install script or a node-gyp config, otherwise pnpm skips the
      build of allow-listed native dependencies.
    """
    files_dir = store_dir / "v10" / "files"
    index_dir = store_dir / "v10" / "index"

    files_index: dict[str, dict[str, object]] = {}
    # pnpm records when it last verified each file; a fixed value keeps the prefetch output
    # reproducible (pnpm re-verifies against the stored integrity on install regardless).
    checked_at = 0
    requires_build = False
    package_manifest: dict[str, object] = {}

    with tarfile.open(tarball_path, "r:gz") as tf:
        for member in tf.getmembers():
            # npm publishes regular files only; directories, and the symlinks/hardlinks a
            # tarball may contain, are skipped -- pnpm's store holds only file content keyed
            # by hash, with no slot to materialise a link.
            if not member.isfile():
                continue

            # npm tarballs wrap everything in a top-level "package/" directory.
            rel_path = member.name
            if rel_path.startswith("package/"):
                rel_path = rel_path[len("package/") :]

            if rel_path.endswith(".gyp"):
                requires_build = True

            file_obj = tf.extractfile(member)
            if file_obj is None:
                continue
            data = file_obj.read()

            if rel_path == "package.json":
                try:
                    parsed_manifest = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    parsed_manifest = None
                # A malformed or non-object package.json must not abort the prefetch;
                # only a JSON object can carry the "scripts" read further down.
                if isinstance(parsed_manifest, dict):
                    package_manifest = parsed_manifest

            file_digest = hashlib.sha512(data).digest()
            file_hex = file_digest.hex()
            file_sri = "sha512-" + base64.b64encode(file_digest).decode()

            is_exec = bool(member.mode & 0o111)
            cas_path = files_dir / file_hex[0:2] / (file_hex[2:] + ("-exec" if is_exec else ""))
            cas_path.parent.mkdir(parents=True, exist_ok=True)
            if not cas_path.exists():
                # Write to a temp file and atomically rename it in, so an interrupted
                # prefetch never leaves a truncated file that a later run would trust
                # (the existence check above skips rewriting).
                tmp_path = cas_path.with_name(cas_path.name + ".tmp")
                tmp_path.write_bytes(data)
                tmp_path.chmod(0o750 if is_exec else 0o640)
                tmp_path.replace(cas_path)

            files_index[rel_path] = {
                "checkedAt": checked_at,
                "integrity": file_sri,
                "mode": member.mode & 0o7777,
                "size": len(data),
            }

    if scope:
        full_name = f"@{scope}/{name}"
        pkg_index_suffix = f"@{scope}+{name}@{version}.json"
    else:
        full_name = name
        pkg_index_suffix = f"{name}@{version}.json"

    # pnpm addresses the store index by the SHA-512 of the tarball. A registry tarball's
    # SHA-512 integrity SRI already is that digest, so reuse it; otherwise (e.g. a legacy
    # sha1 integrity) hash the tarball, since the store is SHA-512-addressed regardless.
    if pkg_integrity.startswith("sha512-"):
        prefix, hex_key = _sri_to_pnpm_store_key(pkg_integrity)
    else:
        tarball_hex = _sha512_hexdigest(tarball_path)
        prefix, hex_key = tarball_hex[0:2], tarball_hex[2:_PNPM_STORE_INDEX_HEX_LEN]
    index_path = index_dir / prefix / f"{hex_key}-{pkg_index_suffix}"
    index_path.parent.mkdir(parents=True, exist_ok=True)

    scripts = package_manifest.get("scripts") or {}
    if isinstance(scripts, dict) and any(
        scripts.get(hook) for hook in ("preinstall", "install", "postinstall")
    ):
        requires_build = True

    index_data = {
        "name": full_name,
        "version": version,
        "requiresBuild": requires_build,
        "files": files_index,
    }
    index_path.write_text(json.dumps(index_data, separators=(",", ":")))


def _prepare_lockfile_for_hermetic_build(
    lockfile: PnpmLock, packages: list[PnpmPackage]
) -> ProjectFile:
    """Point the given packages' resolutions at their downloaded tarballs.

    Registry dependencies resolve offline from the pre-populated pnpm store (see
    ``_add_tarball_to_pnpm_store``) and are not passed here, so their resolutions stay
    registry-style -- pnpm >=10.34.2 rejects a registry-style key paired with a local
    ``tarball: file://`` resolution. Only non-registry dependencies (git/URL), which pnpm
    exempts from that check and cannot serve from the store, get the ``file://`` rewrite.
    """
    lockfile_copy = copy.deepcopy(lockfile)

    for package in packages:
        data = lockfile_copy.packages[package.id]
        resolution = data.setdefault("resolution", {})
        resolution["tarball"] = f"file://${{output_dir}}/deps/pnpm/{package.tarball_filename}"

    return ProjectFile(
        abspath=lockfile_copy.path, template=yaml.safe_dump(lockfile_copy.data, sort_keys=False)
    )
