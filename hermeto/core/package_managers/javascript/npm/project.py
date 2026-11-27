# SPDX-License-Identifier: GPL-3.0-only
import fnmatch
import json
from functools import cached_property
from pathlib import Path
from typing import Any, NamedTuple, TypedDict
from urllib.parse import urlparse

from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import (
    InvalidLockfileFormat,
    NotAGitRepo,
    PackageRejected,
    UnexpectedFormat,
    UnsupportedFeature,
)
from hermeto.core.models.output import ProjectFile
from hermeto.core.models.sbom import PROXY_COMMENT, PROXY_REF_TYPE, ExternalReference
from hermeto.core.package_managers.javascript.js_utils import parse_git_clone_url
from hermeto.core.package_managers.javascript.npm.utils import (
    classify_resolved_url,
    normalize_resolved_url,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID, get_repo_id

# npm does not install devDependencies of transitive deps — they are only
# relevant for the packages being developed (root + selected workspaces).
_TRANSITIVE_DEP_TYPES = ("dependencies", "optionalDependencies", "peerDependencies")
_ALL_DEP_TYPES = (*_TRANSITIVE_DEP_TYPES, "devDependencies")


def _get_dep_names(dep_types: tuple[str, ...], package_data: dict[str, Any]) -> list[str]:
    """Return dependency names declared under the given dependency types."""
    return [
        dep_name for dep_type in dep_types for dep_name in package_data.get(dep_type, {}).keys()
    ]


class _PendingDep(NamedTuple):
    """A dependency waiting to be resolved during the reachability walk.

    context_path determines the npm resolution search order for dep_name.
    """

    context_path: str
    dep_name: str


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

    def __init__(
        self,
        lockfile_path: RootedPath,
        lockfile_data: dict[str, Any],
        workspace_filter: list[str] | None = None,
    ) -> None:
        """Initialize a PackageLock."""
        self._workspaces: list[str] = []
        self._lockfile_path = lockfile_path
        self._lockfile_data = lockfile_data
        self._main_package, self._packages = self._get_packages()

        if workspace_filter is not None:
            unknown = set(workspace_filter) - set(self._workspaces)
            if unknown:
                raise PackageRejected(
                    f"Unknown npm workspaces: {', '.join(sorted(unknown))}",
                    solution=(
                        f"Use workspace paths from package-lock.json: {', '.join(self._workspaces)}"
                    ),
                )
            reachable = self._get_reachable_packages(workspace_filter)
            self._packages = [p for p in self._packages if p.path in reachable]
            self._workspaces = [ws for ws in self._workspaces if ws in workspace_filter]

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
    def from_file(
        cls,
        lockfile_path: RootedPath,
        workspace_filter: list[str] | None = None,
    ) -> "PackageLock":
        """Create a PackageLock from a package-lock.json file.

        :param lockfile_path: path to the package-lock.json file
        :param workspace_filter: optional list of workspace names to include
        """
        lockfile_data = _load_json_file(lockfile_path.path)

        lockfile_version = lockfile_data.get("lockfileVersion")
        if lockfile_version not in (2, 3):
            raise UnsupportedFeature(
                f"lockfileVersion {lockfile_version} from {lockfile_path} is not supported. lockfileVersion 1 was deprecated.",
                solution="Please use a supported lockfileVersion, which are versions 2 and 3",
            )

        return cls(lockfile_path, lockfile_data, workspace_filter)

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

    def _get_reachable_packages(self, workspace_filter: list[str]) -> set[str]:
        """Get package paths reachable from selected workspaces and root deps.

        Walks the dependency graph starting from root dependencies and each selected
        workspace's declared dependencies. Follows npm's node_modules resolution
        (closest ancestor first) and resolves workspace link entries to their targets.

        :param workspace_filter: workspace directory names to include
        :return: set of lockfile package paths that are reachable
        """
        packages_data = self._lockfile_data.get("packages", {})
        lockfile_path = self._lockfile_path.path
        pkg_dir = lockfile_path.parent if lockfile_path.is_file() else lockfile_path

        pending: list[_PendingDep] = []

        root_data = packages_data.get("", {})
        pending.extend(
            _PendingDep("", dep_name) for dep_name in _get_dep_names(_ALL_DEP_TYPES, root_data)
        )

        reachable: set[str] = set()

        for ws_name in workspace_filter:
            reachable.add(ws_name)
            pending += self._list_workspace_deps(pkg_dir, ws_name)

        visited: set[_PendingDep] = set()

        while pending:
            dep = pending.pop()
            if dep in visited:
                continue
            visited.add(dep)

            resolved_path = self._resolve_dep_path(dep.context_path, dep.dep_name, packages_data)
            if resolved_path is None or resolved_path in reachable:
                continue

            reachable.add(resolved_path)

            pkg_data = packages_data.get(resolved_path, {})
            if pkg_data.get("link") and (target := pkg_data.get("resolved")):
                if target not in reachable:
                    reachable.add(target)
                    pending += self._list_workspace_deps(pkg_dir, target)
                    continue

            pending.extend(
                _PendingDep(resolved_path, child_name)
                for child_name in _get_dep_names(_TRANSITIVE_DEP_TYPES, pkg_data)
            )

        return reachable

    @staticmethod
    def _list_workspace_deps(pkg_dir: Path, ws_name: str) -> list[_PendingDep]:
        """Read a workspace's package.json and list its deps for the reachability walk.

        :param pkg_dir: root directory containing workspace subdirectories
        :param ws_name: workspace directory name
        :return: the workspace's declared dependencies, resolved from the workspace
        """
        ws_pkg_json_path = pkg_dir / ws_name / "package.json"
        if not ws_pkg_json_path.exists():
            return []
        ws_pkg_json = _load_json_file(ws_pkg_json_path)
        return [
            _PendingDep(ws_name, dep_name)
            for dep_name in _get_dep_names(_ALL_DEP_TYPES, ws_pkg_json)
        ]

    @staticmethod
    def _resolve_dep_path(
        context_path: str, dep_name: str, packages_data: dict[str, Any]
    ) -> str | None:
        """Resolve a dependency name from a context path using npm resolution.

        Walks up the node_modules hierarchy from context_path until the dependency
        is found, mirroring Node.js module resolution.

        :param context_path: lockfile package path to resolve from, e.g. ``""``,
            ``"client"``, ``"node_modules/express"``, or
            ``"node_modules/express/node_modules/qs"``
        :param dep_name: package name to resolve
        :param packages_data: the ``packages`` dict from the lockfile
        :return: the resolved lockfile path, or None if not found
        """
        # current tracks the search context, walking up through ancestor
        # node_modules directories: "node_modules/a/node_modules/b" -> "node_modules/a"
        # -> "" (root). Workspace directories like "client" fall through to root.
        current = context_path
        while True:
            candidate = (
                f"{current}/node_modules/{dep_name}" if current else f"node_modules/{dep_name}"
            )
            if candidate in packages_data:
                return candidate
            if "/node_modules/" in current:
                current = current.rsplit("/node_modules/", 1)[0]
            elif current:
                current = ""
            else:
                break
        return None

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
    def _repo_id(self) -> RepoID | None:
        try:
            return get_repo_id(self._pkg_path.root)
        except NotAGitRepo:
            if get_config().mode == Mode.PERMISSIVE:
                return None
            raise

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
            clone_url = parse_git_clone_url(resolved_url)
            if not clone_url.ref:
                raise UnexpectedFormat(
                    f"Cannot parse git URL: {clone_url}",
                    solution="Ensure the git dependency has a valid URL and commit ref.",
                )
            repo_id = RepoID(origin_url=clone_url.url, commit_id=clone_url.ref)
            qualifiers = {"vcs_url": repo_id.as_vcs_url_qualifier()}
        elif dep_type == "file":
            if self._repo_id is not None:
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
