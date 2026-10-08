# SPDX-License-Identifier: GPL-3.0-only
import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import tomlkit
from packageurl import PackageURL
from pydantic import HttpUrl

from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, UnexpectedFormat
from hermeto.core.models.input import Request
from hermeto.core.models.output import Component
from hermeto.core.models.sbom import PROXY_COMMENT, PROXY_REF_TYPE, ExternalReference
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import get_repo_id

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CargoPackage:
    """Represents a package from Cargo.lock file."""

    name: str
    version: str
    source: str | None = None  # [git|registry]+https://github.com/<org>/<package>#[|<sha>]
    checksum: str | None = None
    proxy: HttpUrl | None = None

    @cached_property
    def purl(self) -> PackageURL:
        """Return corresponding package URL."""
        qualifiers = {}
        # depends on https://github.com/hermetoproject/hermeto/issues/852
        if self.checksum is not None:
            qualifiers["checksum"] = self.checksum

        if self.source is not None:
            if self.source.startswith("git+"):
                parsed_url = urlparse(self.source)
                commit_id = parsed_url.fragment
                base_url = urlunparse(parsed_url._replace(query="", fragment=""))
                qualifiers["vcs_url"] = f"{base_url}@{commit_id}"
            elif self.source.startswith("registry+"):
                # Extract registry URL from source (format: "registry+https://...")
                registry_url = self.source.removeprefix("registry+")
                if "crates.io" not in registry_url:
                    qualifiers["repository_url"] = registry_url
            else:
                raise UnexpectedFormat(f"Unable to construct package URL from '{self.source}'.")

        return PackageURL(type="cargo", name=self.name, version=self.version, qualifiers=qualifiers)

    @property
    def _is_proxied(self) -> bool:
        # Custom registries are not proxied, so only those which actually use
        # proxy_url should be reported, vcs_urls are not proxied.
        # Note, that crates.io gets replaced with proxy_url on .cargo/config.toml level
        if self.proxy is None:
            return False
        if self.source is None:
            # This can happen to some Rust dependencies for Python project,
            # e.g. cryptography-cffi@0.1.0. This is not a local package, those
            # are handled elsewhere.
            return True
        if self.source.startswith("git+"):
            return False
        return "crates.io" in self.source

    def to_component(self) -> Component:
        """Convert CargoPackage into SBOM component."""
        ref_rest = dict(type=PROXY_REF_TYPE, comment=PROXY_COMMENT)
        proxy = [ExternalReference(url=str(self.proxy), **ref_rest)] if self._is_proxied else None
        return Component(
            name=self.name,
            version=self.version,
            purl=self.purl.to_string(),
            external_references=proxy,
        )


@dataclass
class LocalCargoPackage:
    """Represents a local dependency in the project or a workspace."""

    name: str
    version: str | None = None
    vcs_url: str | None = None
    subpath: str | None = None

    @cached_property
    def purl(self) -> PackageURL:
        """Return corresponding package URL."""
        qualifiers = {}
        if self.vcs_url is not None:
            qualifiers["vcs_url"] = self.vcs_url
        else:
            # The subpath does not make sense if there is no VCS URL. This usually happens because
            # of missing .git directory in an unpacked tarball that comes from a pip request.
            self.subpath = None

        return PackageURL(
            type="cargo",
            name=self.name,
            version=self.version,
            qualifiers=qualifiers,
            subpath=self.subpath,
        )

    def to_component(self) -> Component:
        """Convert LocalCargoPackage into SBOM component."""
        return Component(name=self.name, version=self.version, purl=self.purl.to_string())


def _parse_toml_project_file(path: Path) -> dict[str, Any]:
    """Parse any Cargo related TOML file into a dictionary."""
    parsed_toml = tomlkit.parse(path.read_text())
    return parsed_toml.value


def _resolve_main_package(package_dir: RootedPath) -> tuple[str, str | None]:
    """Resolve package name and version from Cargo.toml."""
    parsed_toml = _parse_toml_project_file(package_dir.path / "Cargo.toml")

    package_info = parsed_toml.get("package", {})
    workspace_info = parsed_toml.get("workspace", {})

    # use default values if the project is a virtual workspace without any package information
    name = package_info.get("name", package_dir.path.stem)
    version = package_info.get("version", None)

    # check for a workspace package version
    # https://doc.rust-lang.org/cargo/reference/workspaces.html#the-package-table
    if version is None:
        version = workspace_info.get("package", {}).get("version")

    return name, version


def _find_local_packages(package_dir: RootedPath) -> dict[str, str]:
    """Find local packages in the Cargo.toml file and return their subpaths."""
    parsed_toml = _parse_toml_project_file(package_dir.path / "Cargo.toml")

    result = {}

    runtime_deps = parsed_toml.get("dependencies", {})
    # Patched dependencies are used to override crates.io dependencies with local versions.
    # This is useful for development purposes or quick bug fixes.
    # https://doc.rust-lang.org/cargo/reference/overriding-dependencies.html
    patched_deps = parsed_toml.get("patch", {}).get("crates-io", {})
    all_deps = {**runtime_deps, **patched_deps}

    for name, dep_info in all_deps.items():
        if isinstance(dep_info, dict) and "path" in dep_info:
            result[name] = dep_info["path"]

    return result


def _generate_sbom_components(
    package_dir: RootedPath,
    request: Request,
    invoked_through_pip: bool = False,
) -> list[Component]:
    """Generate SBOM components from Cargo.lock and for the main package."""
    parsed_lockfile = _parse_toml_project_file(package_dir.path / "Cargo.lock")

    all_packages = parsed_lockfile.get("package", [])
    local_packages = _find_local_packages(package_dir)
    main_package_name, main_package_version = _resolve_main_package(package_dir)

    # When cargo is invoked from pip for extracted sdists, the source directory is
    # swapped to point at the output directory. Check if source_dir is inside output_dir
    # to detect this scenario, where we can't expect a git repository (and flip the boolean
    # for readbility).
    source_is_outside_output = not request.source_dir.path.is_relative_to(request.output_dir.path)

    # Missing git repo is tolerated in two independent cases:
    # 1. PERMISSIVE mode: validation is relaxed
    # 2. Nested PM (pip->cargo for sdists): source_dir is inside output_dir,
    #    so missing git is expected even in STRICT mode
    vcs_url = None
    try:
        vcs_url = get_repo_id(package_dir.root).as_vcs_url_qualifier()
    except NotAGitRepo:
        if get_config().mode != Mode.PERMISSIVE and source_is_outside_output:
            raise

    components = []

    for pkg in all_packages:
        pkg_name = pkg.get("name")
        pkg_version = pkg.get("version")

        if pkg_name == main_package_name:
            if invoked_through_pip:
                # The package was collected as a part of processing a python dependency,
                # it has been already reported when collected with pip, so here it must
                # be ignored.
                pass
            else:
                components.append(
                    LocalCargoPackage(
                        name=main_package_name,
                        version=main_package_version,
                        vcs_url=vcs_url,
                        subpath=str(package_dir.path.relative_to(package_dir.root)),
                    ).to_component()
                )

        elif pkg_name in local_packages:
            # Local packages have no other fields in the Cargo.lock file besides the name and version.
            components.append(
                LocalCargoPackage(
                    name=pkg_name,
                    version=pkg_version,
                    vcs_url=vcs_url,
                    subpath=local_packages.get(pkg_name),
                ).to_component()
            )
        else:
            components.append(
                CargoPackage(
                    name=pkg_name,
                    version=pkg_version,
                    source=pkg.get("source"),
                    checksum=pkg.get("checksum"),
                    proxy=get_config().cargo.proxy_url,
                ).to_component()
            )

    return components
