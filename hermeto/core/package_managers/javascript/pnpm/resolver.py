# SPDX-License-Identifier: GPL-3.0-only
from packageurl import PackageURL

from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, PackageRejected
from hermeto.core.models.sbom import PROXY_COMMENT, Component, ExternalReference
from hermeto.core.package_managers.general import get_vcs_qualifiers
from hermeto.core.package_managers.javascript.package_json import PackageJson
from hermeto.core.package_managers.javascript.pnpm.project import PnpmPackage
from hermeto.core.package_managers.npm import NPM_REGISTRY_URL
from hermeto.core.rooted_path import RootedPath

JSR_REGISTRY_URL = "https://npm.jsr.io"


def generate_sbom_components(
    project_dir: RootedPath, packages: list[PnpmPackage]
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
        *_create_dependency_components(packages, vcs_qualifiers),
    ]


def _create_dependency_components(
    packages: list[PnpmPackage], vcs_qualifiers: dict[str, str]
) -> list[Component]:
    config = get_config()
    proxy_url = config.pnpm.proxy_url

    components = []
    for package in packages:
        if package.url.startswith(NPM_REGISTRY_URL) and proxy_url is not None:
            external_references = [ExternalReference(url=str(proxy_url), comment=PROXY_COMMENT)]
        else:
            external_references = None

        purl = _generate_purl_for(package, vcs_qualifiers)
        components.append(
            Component(
                name=package.name,
                version=package.version,
                purl=purl.to_string(),
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
