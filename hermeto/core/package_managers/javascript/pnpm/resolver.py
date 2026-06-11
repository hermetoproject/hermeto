# SPDX-License-Identifier: GPL-3.0-only
from collections import deque

from packageurl import PackageURL

from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, PackageRejected
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import PROXY_COMMENT, Component, ExternalReference
from hermeto.core.package_managers.general import get_vcs_qualifiers
from hermeto.core.package_managers.javascript.npm import NPM_REGISTRY_URL
from hermeto.core.package_managers.javascript.package_json import PackageJson
from hermeto.core.package_managers.javascript.pnpm.project import PnpmLock, PnpmPackage
from hermeto.core.rooted_path import RootedPath

JSR_REGISTRY_URL = "https://npm.jsr.io"


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
        property_set = PropertySet(npm_development=package.id not in non_dev_dependencies)

        components.append(
            Component(
                name=package.name,
                version=package.version,
                purl=purl.to_string(),
                properties=property_set.to_properties(),
                external_references=external_references,
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

    # The scope must contain the '@' prefix according to the PURL specification.
    scope = f"@{package.scope.lower()}" if package.scope else None

    return PackageURL(
        type="npm",
        namespace=scope,
        name=package.name.lower(),
        version=package.version,
        qualifiers=qualifiers,
        subpath=subpath,
    )


def _create_root_component(project_dir: RootedPath, vcs_qualifiers: dict[str, str]) -> Component:
    package_json = PackageJson.from_dir(project_dir.path)

    name = package_json.get("name")
    version = package_json.get("version")
    if name is None:
        raise PackageRejected(f"Missing 'name' field in the {package_json.path}", solution=None)

    subpath = str(project_dir.subpath_from_root)
    purl = PackageURL(
        type="npm",
        name=name.lower(),
        version=version,
        qualifiers=vcs_qualifiers,
        subpath=subpath,
    )
    return Component(name=name, version=version, purl=purl.to_string())


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
    return {_strip_peer_suffix(id) for id in seen}


def _strip_peer_suffix(package_id: str) -> str:
    """
    >>> _strip_peer_suffix('pkg@1.0.0')
    'pkg@1.0.0'
    >>> _strip_peer_suffix('pkg@1.0.0(foo@2.0.0)')
    'pkg@1.0.0'
    >>> _strip_peer_suffix('pkg@1.0.0(foo@2.0.0)(bar@2.0.0)')
    'pkg@1.0.0'
    """
    return package_id.partition("(")[0]
