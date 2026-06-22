# SPDX-License-Identifier: GPL-3.0-only
import logging
from collections import deque

import yaml
from packageurl import PackageURL

from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import (
    InvalidLockfileFormat,
    LockfileNotFound,
    NotAGitRepo,
    PackageRejected,
)
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import (
    PROXY_COMMENT,
    Component,
    ExternalReference,
    Patch,
    PatchDiff,
    Pedigree,
)
from hermeto.core.package_managers.general import get_vcs_qualifiers
from hermeto.core.package_managers.javascript.npm import NPM_REGISTRY_URL
from hermeto.core.package_managers.javascript.package_json import PackageJson
from hermeto.core.package_managers.javascript.pnpm.project import PnpmLock, PnpmPackage
from hermeto.core.package_managers.javascript.yarn_classic.workspaces import (
    ensure_no_path_leads_out,
    get_workspace_paths,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import first_for

JSR_REGISTRY_URL = "https://npm.jsr.io"

log = logging.getLogger(__name__)


def generate_sbom_components(
    project_dir: RootedPath, packages: list[PnpmPackage], lockfile: PnpmLock
) -> list[Component]:
    """Generate SBOM components for the project."""
    config = get_config()
    try:
        vcs_qualifiers = get_vcs_qualifiers(project_dir.root)
    except NotAGitRepo:
        if config.mode == Mode.PERMISSIVE:
            vcs_qualifiers = dict()
        else:
            raise

    return [
        _create_root_component(project_dir, vcs_qualifiers),
        *_create_workspace_components(project_dir, vcs_qualifiers),
        *_create_dependency_components(packages, vcs_qualifiers, lockfile),
    ]


def _create_dependency_components(
    packages: list[PnpmPackage], vcs_qualifiers: dict[str, str], lockfile: PnpmLock
) -> list[Component]:
    config = get_config()
    proxy_url = config.pnpm.proxy_url

    non_dev_dependencies = _find_non_dev_dependencies(lockfile)

    components = []
    for package in packages:
        if package.url.startswith(NPM_REGISTRY_URL) and proxy_url is not None:
            external_references = [ExternalReference(url=str(proxy_url), comment=PROXY_COMMENT)]
        else:
            external_references = None

        purl = _generate_purl_for(package, vcs_qualifiers)
        pedigree = _generate_pedigree_for(package, vcs_qualifiers, lockfile)
        property_set = PropertySet(npm_development=package.id not in non_dev_dependencies)

        components.append(
            Component(
                name=package.name,
                version=package.version,
                purl=purl.to_string(),
                properties=property_set.to_properties(),
                external_references=external_references,
                pedigree=pedigree,
            )
        )

    return components


def _generate_purl_for(package: PnpmPackage, vcs_qualifiers: dict[str, str]) -> PackageURL:
    """Generate a PURL for the given package."""
    qualifiers: dict[str, str] = {}
    subpath = None

    if package.url.startswith("file:"):
        subpath = package.url.removeprefix("file:")
        qualifiers.update(vcs_qualifiers)

    elif package.url.startswith(JSR_REGISTRY_URL):
        qualifiers["repository_url"] = JSR_REGISTRY_URL

    elif not package.url.startswith(NPM_REGISTRY_URL):
        qualifiers["download_url"] = package.url

    return PackageURL(
        type="npm",
        name=package.full_name,
        version=package.version,
        qualifiers=qualifiers,
        subpath=subpath,
    )


def _generate_pedigree_for(
    package: PnpmPackage, vcs_qualifiers: dict[str, str], lockfile: PnpmLock
) -> Pedigree | None:
    """Generate a Pedigree for the given package."""
    vcs_url = vcs_qualifiers.get("vcs_url")
    if not vcs_url:
        return None

    patches = lockfile.patched_dependencies

    def get_patch_url(key: str) -> str:
        try:
            subpath = patches[key]["path"]
        except (KeyError, TypeError):
            raise InvalidLockfileFormat(lockfile.path, f"Missing path for patched dependency {key}")

        return vcs_url + "#" + subpath

    # Dependencies can be patched by package ID or package name (including scope).
    search_keys = (package.id, package.full_name)
    key = first_for(lambda search_key: search_key in patches, search_keys, None)

    return Pedigree(patches=[Patch(diff=PatchDiff(url=get_patch_url(key)))]) if key else None


def _create_root_component(project_dir: RootedPath, vcs_qualifiers: dict[str, str]) -> Component:
    package_json = PackageJson.from_dir(project_dir.path)

    name = package_json.get("name")
    version = package_json.get("version")
    if name is None:
        raise PackageRejected(f"Missing 'name' field in the {package_json.path}")

    subpath = str(project_dir.subpath_from_root)
    purl = PackageURL(
        type="npm",
        name=name,
        version=version,
        qualifiers=vcs_qualifiers,
        subpath=subpath,
    )
    return Component(name=name, version=version, purl=purl.to_string())


def _create_workspace_components(
    project_dir: RootedPath, vcs_qualifiers: dict[str, str]
) -> list[Component]:
    components = []

    workspaces_globs = _read_workspace_globs(project_dir)
    workspace_paths = get_workspace_paths(workspaces_globs, project_dir)
    ensure_no_path_leads_out(workspace_paths, project_dir)

    log.debug("Found %d workspaces", len(workspace_paths))

    for workspace_path in workspace_paths:
        try:
            package_json = PackageJson.from_dir(workspace_path)
        except LockfileNotFound:
            log.warning(
                "The workspace %s does not contain a package.json file and will be ignored",
                workspace_path,
            )
            continue

        name = package_json.get("name")
        version = package_json.get("version")
        if name is None:
            raise PackageRejected(f"Missing 'name' field in the {package_json.path}")

        subpath = str(project_dir.join_within_root(workspace_path).subpath_from_root)
        purl = PackageURL(
            type="npm",
            name=name,
            version=version,
            qualifiers=vcs_qualifiers,
            subpath=subpath,
        )
        components.append(Component(name=name, version=version, purl=purl.to_string()))

    return components


def _read_workspace_globs(project_dir: RootedPath) -> list[str]:
    pnpm_workspace_path = project_dir.path / "pnpm-workspace.yaml"
    if not pnpm_workspace_path.exists():
        return []

    try:
        with pnpm_workspace_path.open() as f:
            pnpm_workspace = yaml.safe_load(f)
    except yaml.YAMLError:
        raise PackageRejected(f"The {pnpm_workspace_path} file must contain valid YAML.")

    return pnpm_workspace.get("packages", [])


def _find_non_dev_dependencies(lockfile: PnpmLock) -> set[str]:
    """
    Find all transitive non-development dependencies using BFS algorithm.

    If a dependency is found as a runtime dependency and also as a development dependency,
    it is classified as runtime dependency.
    """
    seen = set(lockfile.root_dependencies)
    queue = deque(seen)

    while queue:
        current_id = queue.popleft()
        current_snapshot = lockfile.snapshots.get(current_id, {})

        transitive_dependencies = current_snapshot.get("dependencies", {})
        transitive_optional = current_snapshot.get("optionalDependencies", {})

        for name, version in {**transitive_dependencies, **transitive_optional}.items():
            new_id = f"{name}@{version}"
            if new_id not in seen:
                seen.add(new_id)
                queue.append(new_id)

    # Strip the peer dependencies suffix from the package IDs to get the actual package IDs from
    # the «packages» section of the lockfile. See:
    # https://github.com/argoproj/argo-cd/blob/bf1591de63e39b7c3be5f5ba54abe8763de1a48c/ui/pnpm-lock.yaml#L2164
    # https://github.com/argoproj/argo-cd/blob/bf1591de63e39b7c3be5f5ba54abe8763de1a48c/ui/pnpm-lock.yaml#L7869
    return {_strip_dependency_path_suffix(id) for id in seen}


def _strip_dependency_path_suffix(package_id: str) -> str:
    """
    https://github.com/pnpm/pnpm/blob/46fd26afc9926b4391636a851ae32493f9b2c9ff/deps/path/src/index.ts#L52

    >>> _strip_dependency_path_suffix('pkg@1.0.0')
    'pkg@1.0.0'
    >>> _strip_dependency_path_suffix('pkg@1.0.0(foo@2.0.0)')
    'pkg@1.0.0'
    >>> _strip_dependency_path_suffix('pkg@1.0.0(foo@2.0.0)(bar@2.0.0)')
    'pkg@1.0.0'
    >>> _strip_dependency_path_suffix('pkg@1.0.0(patch_hash=abc123)')
    'pkg@1.0.0'
    """
    return package_id.partition("(")[0]
