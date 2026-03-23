# SPDX-License-Identifier: GPL-3.0-only
import fnmatch
import json
from functools import cached_property
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse

from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import get_config
from hermeto.core.errors import InvalidLockfileFormat, UnexpectedFormat, UnsupportedFeature
from hermeto.core.models.output import ProjectFile
from hermeto.core.models.sbom import PROXY_COMMENT, PROXY_REF_TYPE, ExternalReference
from hermeto.core.package_managers.npm.utils import (
    classify_resolved_url,
    extract_git_info_npm,
    normalize_resolved_url,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID, get_repo_id


class NpmComponentInfo(TypedDict):
    """Contains the data needed to generate an npm SBOM component."""

    name: str
    purl: str
    version: str | None
    dev: bool
    bundled: bool
    external_refs: list[ExternalReference] | None
    missing_hash_in_file: Path | None


class ResolvedNpmPackage(TypedDict):
    """Contains all of the data for a resolved npm package."""

    package: NpmComponentInfo
    dependencies: list[NpmComponentInfo]
    projectfiles: list[ProjectFile]


class Package:
    """A npm package."""

    def __init__(self, name: str, path: str, package_dict: dict[str, Any]) -> None:
        """Initialize a Package.

        :param name: the package name, which should correspond to the name in it's package.json
        :param path: the relative path to the package from the root project dir.
        :param package_dict: the raw dict for a package-lock.json `package`
        """
        self.name = name
        self.path = path
        self._package_dict = package_dict

    @property
    def package_dict(self) -> dict[str, Any]:
        """Get the package dictionary with additional info."""
        return self._package_dict

    @property
    def integrity(self) -> str | None:
        """Get the package integrity."""
        return self._package_dict.get("integrity")

    @integrity.setter
    def integrity(self, integrity: str) -> None:
        """Set the package integrity."""
        self._package_dict["integrity"] = integrity

    @property
    def version(self) -> str | None:
        """Get the package version.

        This will be a semver from the package.json file.
        https://docs.npmjs.com/cli/v7/configuring-npm/package-lock-json#packages
        """
        return self._package_dict.get("version")

    @property
    def resolved_url(self) -> str | None:
        """Get the location where the package was resolved from.

        For package-lock.json `packages`, this will be the "resolved" key
        unless it is a file dep, in which case it will be the path to the file.

        For bundled dependencies, this will be None. Such dependencies are included
        in the tarball of a different dependency (the dependency that bundles them).
        """
        if "resolved" not in self._package_dict:
            # indirect bundled dependency, does not have a resolved url
            if self._package_dict.get("inBundle"):
                return None
            # file dependency (or a workspace)
            else:
                return f"file:{self.path}"

        return self._package_dict["resolved"]

    @resolved_url.setter
    def resolved_url(self, resolved_url: str) -> None:
        """Set the location where the package should be resolved from."""
        self._package_dict["resolved"] = resolved_url

    @property
    def bundled(self) -> bool:
        """Return True if this package is bundled."""
        return (
            self._package_dict.get("inBundle", False)
            # In v2+ lockfiles, direct dependencies do have "inBundle": true if they are to be
            # bundled. They will get bundled if the package is uploaded to the npm registry, but
            # aren't bundled yet. These have a resolved url and shouldn't be considered bundled.
            and "resolved" not in self._package_dict
        )

    @property
    def dev(self) -> bool:
        """Return True if this package is a dev dependency."""
        return self._package_dict.get("dev", False)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Package):
            return (
                self.name == other.name
                and self.path == other.path
                and self._package_dict == other._package_dict
            )
        return False


class PackageLock:
    """A npm package-lock.json file."""

    def __init__(self, lockfile_path: RootedPath, lockfile_data: dict[str, Any]) -> None:
        """Initialize a PackageLock."""
        self._workspaces: list[str] = []
        self._lockfile_path = lockfile_path
        self._lockfile_data = lockfile_data
        self._main_package, self._packages = self._get_packages()

    @property
    def packages(self) -> list[Package]:
        """Get list of packages loaded from package-lock.json."""
        return self._packages

    @property
    def main_package(self) -> Package:
        """Return main package info stored as Package instance."""
        return self._main_package

    @property
    def lockfile_data(self) -> dict[str, Any]:
        """Get content of package-lock.json stored in Dictionary."""
        return self._lockfile_data

    def _check_if_package_is_workspace(self, resolved_url: str) -> bool:
        """Test if package is workspace based on main package workspaces."""
        if (
            "packages" not in self._lockfile_data
            or "" not in self._lockfile_data["packages"]
            or "workspaces" not in self._lockfile_data["packages"][""]
        ):
            return False

        main_package_workspaces = self._lockfile_data["packages"][""]["workspaces"]

        for main_package_workspace in main_package_workspaces:
            if fnmatch.fnmatch(resolved_url, Path(main_package_workspace).as_posix()):
                return True
        return False

    @cached_property
    def _purlifier(self) -> "_Purlifier":
        pkg_path = self._lockfile_path.join_within_root("..")
        return _Purlifier(pkg_path)

    @classmethod
    def from_file(cls, lockfile_path: RootedPath) -> "PackageLock":
        """Create a PackageLock from a package-lock.json file."""
        lockfile_data = _load_json_file(lockfile_path.path)

        lockfile_version = lockfile_data.get("lockfileVersion")
        if lockfile_version not in (2, 3):
            raise UnsupportedFeature(
                f"lockfileVersion {lockfile_version} from {lockfile_path} is not supported. lockfileVersion 1 was deprecated.",
                solution="Please use a supported lockfileVersion, which are versions 2 and 3",
            )

        return cls(lockfile_path, lockfile_data)

    def get_project_file(self) -> ProjectFile:
        """Return a ProjectFile for the npm package-lock.json data."""
        return ProjectFile(
            abspath=self._lockfile_path.path.resolve(),
            template=json.dumps(self._lockfile_data, indent=2) + "\n",
        )

    def _get_packages(self) -> tuple[Package, list[Package]]:
        """Return a flat list of Packages from a package-lock.json file."""

        def get_package_name_from_path(package_path: str) -> str:
            """Get the package name from the path in package-lock.json file."""
            path = Path(package_path)
            parent_name = Path(package_path).parents[0].name
            is_scoped = parent_name.startswith("@")
            return (Path(parent_name) / path.name).as_posix() if is_scoped else path.name

        main_package = Package("", "", {})
        packages = []
        for package_path, package_data in self._lockfile_data.get("packages", {}).items():
            # ignore links and the main package, since they're already accounted
            # for elsewhere in the lockfile
            if package_data.get("link"):
                if self._check_if_package_is_workspace(package_data.get("resolved")):
                    self._workspaces.append(package_data.get("resolved"))
                continue

            if package_path == "":
                # Store main package as Package instance
                main_package = Package(package_data.get("name"), package_path, package_data)
                continue

            # if there is no name key, derive it from the package path
            if not (package_name := package_data.get("name")):
                package_name = get_package_name_from_path(package_path)

            packages.append(Package(package_name, package_path, package_data))

        return main_package, packages

    def get_main_package(self) -> NpmComponentInfo:
        """Return a dict with sbom component data for the main package."""
        name = self._lockfile_data.get("name")
        if not name:
            raise UnexpectedFormat("The main package in package-lock.json is missing a 'name'.")
        version = self._lockfile_data.get("version")
        purl = self._purlifier.get_purl(name, version, "file:.", integrity=None)
        return {
            "name": name,
            "version": version,
            "purl": purl.to_string(),
            "dev": False,
            "bundled": False,
            "missing_hash_in_file": None,
            "external_refs": None,
        }

    def get_sbom_components(self) -> list[NpmComponentInfo]:
        """Return a list of dicts with sbom component data."""
        packages = self._packages
        proxy_common = dict(type=PROXY_REF_TYPE, comment=PROXY_COMMENT)
        proxy_url = get_config().npm.proxy_url

        def to_component(package: Package) -> NpmComponentInfo:
            purl = self._purlifier.get_purl(
                package.name, package.version, package.resolved_url, package.integrity
            ).to_string()

            missing_hash_in_file = None
            external_refs = None
            if package.resolved_url:  # dependency is not bundled
                resolved_url = normalize_resolved_url(package.resolved_url)
                dep_type = classify_resolved_url(resolved_url)

                if not package.integrity:
                    if dep_type in ("registry", "https"):
                        missing_hash_in_file = Path(self._lockfile_path.subpath_from_root)
                if dep_type == "registry" and proxy_url is not None:
                    external_refs = [ExternalReference(url=str(proxy_url), **proxy_common)]

            component: NpmComponentInfo = {
                "name": package.name,
                "version": package.version,
                "purl": purl,
                "dev": package.dev,
                "bundled": package.bundled,
                "missing_hash_in_file": missing_hash_in_file,
                "external_refs": external_refs,
            }

            return component

        return list(map(to_component, packages))

    def get_dependencies_to_download(self) -> dict[str, dict[str, str | None]]:
        """Return a Dict of URL dependencies to download."""
        packages = self._packages
        return {
            resolved_url: {
                "version": package.version,
                "name": package.name,
                "integrity": package.integrity,
            }
            for package in packages
            if (resolved_url := package.resolved_url) and not resolved_url.startswith("file:")
        }

    @property
    def workspaces(self) -> list:
        """Return list of workspaces."""
        return self._workspaces


class _Purlifier:
    """Generates purls for npm packages."""

    def __init__(self, pkg_path: RootedPath) -> None:
        """Init a purlifier for the package at pkg_path."""
        self._pkg_path = pkg_path

    @cached_property
    def _repo_id(self) -> RepoID:
        return get_repo_id(self._pkg_path.root)

    def get_purl(
        self,
        name: str,
        version: str | None,
        resolved_url: str | None,
        integrity: str | None,
    ) -> PackageURL:
        """Get the purl for an npm package.

        https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#npm
        """
        if not resolved_url:
            # bundled dependency, same purl as a registry dependency
            # (differentiation between bundled and registry should be done elsewhere)
            return PackageURL(type="npm", name=name.lower(), version=version)

        qualifiers: dict[str, str] | None = None
        subpath: str | None = None

        resolved_url = normalize_resolved_url(resolved_url)
        dep_type = classify_resolved_url(resolved_url)

        if dep_type == "registry":
            pass
        elif dep_type == "git":
            info = extract_git_info_npm(resolved_url)
            repo_id = RepoID(origin_url=info["url"], commit_id=info["ref"])
            qualifiers = {"vcs_url": repo_id.as_vcs_url_qualifier()}
        elif dep_type == "file":
            qualifiers = {"vcs_url": self._repo_id.as_vcs_url_qualifier()}
            path = urlparse(resolved_url).path
            subpath_from_root = self._pkg_path.join_within_root(path).subpath_from_root
            if subpath_from_root != Path():
                subpath = subpath_from_root.as_posix()
        else:  # dep_type == "https"
            qualifiers = {"download_url": resolved_url}
            if integrity:
                qualifiers["checksum"] = str(ChecksumInfo.from_sri(integrity))

        return PackageURL(
            type="npm",
            name=name.lower(),
            version=version,
            qualifiers=qualifiers,
            subpath=subpath,
        )


def _load_json_file(file_path: Path) -> dict[str, Any]:
    """Load and parse a JSON file, raising an appropriate error on decode failure."""
    try:
        with file_path.open("r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        if "lock" in file_path.name:
            raise InvalidLockfileFormat(
                lockfile_path=file_path,
                err_details=str(e),
            ) from e
        raise UnexpectedFormat(f"The {file_path.name} file must contain valid JSON: {e}") from e

    return data
