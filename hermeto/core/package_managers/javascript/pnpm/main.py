# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import copy
import logging
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
from hermeto.core.package_managers.javascript.pnpm.resolver import (
    JSR_REGISTRY_URL,
    generate_sbom_components,
)

log = logging.getLogger(__name__)


def fetch_pnpm_source(request: Request) -> RequestOutput:
    """Process all pnpm source directories in the given request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []
    annotations: list[Annotation] = []

    deps_dir = request.output_dir.path.joinpath("deps", "pnpm")
    deps_dir.mkdir(parents=True, exist_ok=True)

    for package in request.pnpm_packages:
        project_dir = request.source_dir.join_within_root(package.path)
        log.info("Fetching pnpm dependencies at %s", project_dir)

        lockfile = PnpmLock.from_dir(project_dir.path)
        packages, updated_lockfile = _resolve_pnpm_project(deps_dir, lockfile)
        project_files.append(updated_lockfile)
        project_files.append(_prepare_npmrc_for_hermetic_build(project_dir.path))
        components.extend(generate_sbom_components(project_dir, packages, lockfile))

    if backend_annotation := create_backend_annotation(components, "x-pnpm"):
        annotations.append(backend_annotation)

    return RequestOutput.from_obj_list(
        components=components,
        # https://pnpm.io/blog/releases/11.3#trustlockfile
        environment_variables=[
            EnvironmentVariable(name="PNPM_CONFIG_TRUST_LOCKFILE", value="true"),
        ],
        project_files=project_files,
        annotations=annotations,
    )


def _resolve_pnpm_project(
    deps_dir: Path, lockfile: PnpmLock
) -> tuple[list[PnpmPackage], ProjectFile]:
    """Resolve a pnpm project."""
    packages = parse_packages(lockfile)
    log.info("Parsed %d packages from %s", len(packages), lockfile.path)
    non_local = [p for p in packages if not p.url.startswith("file:")]
    _download_resolved_packages(non_local, deps_dir)
    return packages, _prepare_lockfile_for_hermetic_build(lockfile, non_local)


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
        tarball_path = _mirror_tarball_path(deps_dir, package)
        tarball_path.parent.mkdir(parents=True, exist_ok=True)

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
                file_path=_mirror_tarball_path(deps_dir, package),
                expected_checksums=[ChecksumInfo.from_sri(package.integrity)],
            )
        else:
            log.warning("Missing checksum for %s", package.id)


def _prepare_lockfile_for_hermetic_build(
    lockfile: PnpmLock, packages: list[PnpmPackage]
) -> ProjectFile:
    lockfile_copy = copy.deepcopy(lockfile)

    for package in packages:
        data = lockfile_copy.packages[package.id]
        resolution = data.setdefault("resolution", {})
        if package.url.startswith(NPM_REGISTRY_URL) or package.url.startswith(JSR_REGISTRY_URL):
            resolution["tarball"] = package.tarball_filename
        else:
            resolution["tarball"] = f"file://${{output_dir}}/deps/pnpm/{package.tarball_filename}"

    return ProjectFile(
        abspath=lockfile_copy.path, template=yaml.safe_dump(lockfile_copy.data, sort_keys=False)
    )


def _prepare_npmrc_for_hermetic_build(project_dir: Path) -> ProjectFile:
    """
    Create a .npmrc file with registry configuration instead of using environment variables because
    of limitations with `npm_config_@jsr:registry` environment variable ("@" is not a valid POSIX
    export name when sourcing hermeto.env).
    """
    npmrc_path = project_dir.joinpath(".npmrc")
    content = (npmrc_path.read_text() + "\n") if npmrc_path.is_file() else ""

    content += (
        "registry=file://${output_dir}/deps/pnpm/npm/\n"
        "@jsr:registry=file://${output_dir}/deps/pnpm/jsr/\n"
    )
    return ProjectFile(abspath=npmrc_path, template=content)


def _mirror_tarball_path(deps_dir: Path, package: PnpmPackage) -> Path:
    if package.url.startswith(NPM_REGISTRY_URL):
        deps_dir = deps_dir / "npm"
    elif package.url.startswith(JSR_REGISTRY_URL):
        deps_dir = deps_dir / "jsr"

    return deps_dir / package.tarball_filename
