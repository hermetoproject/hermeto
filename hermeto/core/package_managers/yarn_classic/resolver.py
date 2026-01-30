# SPDX-License-Identifier: GPL-3.0-only
import json
import re
import tarfile
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from packageurl import PackageURL
from pyarn.lockfile import Package as PYarnPackage

from hermeto import APP_NAME
from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, PackageRejected, UnexpectedFormat
from hermeto.core.package_managers.general import get_vcs_qualifiers
from hermeto.core.package_managers.npm import NPM_REGISTRY_CNAMES
from hermeto.core.package_managers.yarn_classic.project import PackageJson, Project, YarnLock
from hermeto.core.package_managers.yarn_classic.utils import (
    find_runtime_deps,
    get_git_tarball_mirror_name,
    get_tarball_mirror_name,
)
from hermeto.core.package_managers.yarn_classic.workspaces import (
    Workspace,
    extract_workspace_metadata,
)
from hermeto.core.rooted_path import RootedPath

# https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/resolvers/exotics/git-resolver.js#L15-L17
GIT_HOSTS = frozenset(("github.com", "gitlab.com", "bitbucket.com", "bitbucket.org"))
GIT_PATTERN_MATCHERS = (
    re.compile(r"^git:"),
    re.compile(r"^git\+.+:"),
    re.compile(r"^ssh:"),
    re.compile(r"^https?:.+\.git$"),
    re.compile(r"^https?:.+\.git#.+"),
)

YARN_REGISTRY_URL = "https://registry.yarnpkg.com"


@dataclass
class _BasePackage(ABC):
    """A base Yarn 1.x package."""

    name: str
    version: str | None = None
    integrity: str | None = None
    dev: bool = False

    @property
    @abstractmethod
    def purl(self) -> str:
        """Return the package URL."""


@dataclass
class _UrlMixin:
    url: str


@dataclass
class _PathMixin:
    path: RootedPath

    def _get_vcs_qualifiers(self) -> dict[str, str] | None:
        """Return vcs_url qualifiers dict if repo ID is available, else None."""
        try:
            return get_vcs_qualifiers(self.path.root)
        except NotAGitRepo:
            if get_config().mode == Mode.STRICT:
                raise
            return None


@dataclass
class RegistryPackage(_BasePackage, _UrlMixin):
    """A Yarn 1.x package from the registry."""

    @property
    def purl(self) -> str:
        """Return package URL."""
        qualifiers = {}

        if YARN_REGISTRY_URL in self.url:
            qualifiers = {"repository_url": YARN_REGISTRY_URL}

        if self.integrity:
            qualifiers["checksum"] = str(ChecksumInfo.from_sri(self.integrity))

        return PackageURL(
            type="npm",
            name=self.name,
            version=self.version,
            qualifiers=qualifiers,
        ).to_string()


@dataclass
class GitPackage(_BasePackage, _UrlMixin):
    """A Yarn 1.x package from a git repo."""

    @property
    def purl(self) -> str:
        """Return package URL."""
        parsed_url = urlparse(self.url)
        ref = parsed_url.fragment
        clean_url = parsed_url._replace(fragment="").geturl()
        qualifiers = {"vcs_url": f"git+{clean_url}@{ref}"}
        return PackageURL(
            type="npm",
            name=self.name,
            version=self.version,
            qualifiers=qualifiers,
        ).to_string()


@dataclass
class UrlPackage(_BasePackage, _UrlMixin):
    """A Yarn 1.x package from a http/https URL."""

    @property
    def purl(self) -> str:
        """Return package URL."""
        qualifiers = {"download_url": self.url}
        return PackageURL(
            type="npm",
            name=self.name,
            version=self.version,
            qualifiers=qualifiers,
        ).to_string()


@dataclass
class FilePackage(_BasePackage, _PathMixin):
    """A Yarn 1.x package from a local file path."""

    @property
    def purl(self) -> str:
        """Return package URL."""
        return PackageURL(
            type="npm",
            name=self.name,
            version=self.version,
            qualifiers=self._get_vcs_qualifiers(),
            subpath=str(self.path.subpath_from_root),
        ).to_string()


@dataclass
class WorkspacePackage(_BasePackage, _PathMixin):
    """A Yarn 1.x local workspace package."""

    @property
    def purl(self) -> str:
        """Return package URL."""
        return PackageURL(
            type="npm",
            name=self.name,
            version=self.version,
            qualifiers=self._get_vcs_qualifiers(),
            subpath=str(self.path.subpath_from_root),
        ).to_string()


@dataclass
class LinkPackage(_BasePackage, _PathMixin):
    """A Yarn 1.x local link package."""

    @property
    def purl(self) -> str:
        """Return package URL."""
        return PackageURL(
            type="npm",
            name=self.name,
            version=self.version,
            qualifiers=self._get_vcs_qualifiers(),
            subpath=str(self.path.subpath_from_root),
        ).to_string()


YarnClassicPackage = (
    FilePackage | GitPackage | LinkPackage | RegistryPackage | UrlPackage | WorkspacePackage
)


class _YarnClassicPackageFactory:
    def __init__(
        self, source_dir: RootedPath, mirror_dir: RootedPath, runtime_deps: set[str]
    ) -> None:
        self._source_dir = source_dir
        self._mirror_dir = mirror_dir
        self._runtime_deps = runtime_deps

    def create_package_from_pyarn_package(self, package: PYarnPackage) -> YarnClassicPackage:
        def assert_package_has_relative_path(package: PYarnPackage) -> None:
            if package.path and Path(package.path).is_absolute():
                raise PackageRejected(
                    (
                        f"The package {package.name}@{package.version} has an absolute path "
                        f"({package.path}), which is not permitted."
                    ),
                    solution="Ensure that file/link packages in yarn.lock do not have absolute paths.",
                )

        package_id = f"{package.name}@{package.version}"
        dev = package_id not in self._runtime_deps

        if _is_from_npm_registry(package.url):
            # registry packages should already have the correct name
            return RegistryPackage(
                name=package.name,
                version=package.version,
                integrity=package.checksum,
                url=package.url,
                dev=dev,
            )
        elif package.path is not None:
            # Ensure path is not absolute
            assert_package_has_relative_path(package)
            # Ensure path is within the repository root
            path = self._source_dir.join_within_root(package.path)
            # File packages have a url, whereas link packages do not
            if package.url:
                real_name = _read_name_from_tarball(path)

                return FilePackage(
                    name=real_name,
                    version=package.version,
                    path=path,
                    integrity=package.checksum,
                    dev=dev,
                )

            package_json_path = path.join_within_root("package.json")
            # package.json is not required for link packages
            if package_json_path.path.exists():
                real_name = _read_name_from_package_json(package_json_path)
            else:
                real_name = package.name

            return LinkPackage(
                name=real_name,
                version=package.version,
                path=path,
                dev=dev,
            )
        elif _is_git_url(package.url):
            tarball_name = get_git_tarball_mirror_name(package.url)
            tarball_path = self._mirror_dir.join_within_root(tarball_name)
            real_name = _read_name_from_tarball(tarball_path)

            return GitPackage(
                name=real_name,
                version=package.version,
                url=package.url,
                dev=dev,
            )
        elif _is_tarball_url(package.url):
            tarball_name = get_tarball_mirror_name(package.url)
            tarball_path = self._mirror_dir.join_within_root(tarball_name)
            real_name = _read_name_from_tarball(tarball_path)

            return UrlPackage(
                name=real_name,
                version=package.version,
                url=package.url,
                integrity=package.checksum,
                dev=dev,
            )
        else:
            raise UnexpectedFormat(
                (
                    f"{APP_NAME} could not determine the package type for the following package in "
                    f"yarn.lock: {vars(package)}"
                ),
                solution=(
                    f"Ensure yarn.lock is well-formed and if so, report this error to the {APP_NAME} team"
                ),
            )


def _is_tarball_url(url: str) -> bool:
    """Return True if a package URL is a tarball URL."""
    # Parse the URL to extract components
    parsed_url = urlparse(url)

    # https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/resolvers/exotics/tarball-resolver.js#L34
    if parsed_url.scheme not in {"http", "https"}:
        return False

    # https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/resolvers/exotics/tarball-resolver.js#L40
    # https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/resolvers/exotics/bitbucket-resolver.js#L11
    # https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/resolvers/exotics/gitlab-resolver.js#L10C10-L10C23
    if parsed_url.path.endswith((".tar", ".tar.gz", ".tgz")):
        return True

    # https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/resolvers/exotics/github-resolver.js#L24
    if parsed_url.hostname == "codeload.github.com" and "tar.gz" in parsed_url.path:
        return True

    return False


def _is_git_url(url: str) -> bool:
    """Return True if a package URL is a git URL."""
    # https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/resolvers/exotics/git-resolver.js#L32
    if any(matcher.match(url) for matcher in GIT_PATTERN_MATCHERS):
        return True

    # https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/resolvers/exotics/git-resolver.js#L39
    parsed_url = urlparse(url)
    if parsed_url.hostname in GIT_HOSTS:
        path_segments = [segment for segment in parsed_url.path.split("/") if segment]
        # Return True if the path has exactly two segments (e.g. org/repo, not org/repo/file.tar.gz)
        return len(path_segments) == 2

    return False


def _is_from_npm_registry(url: str) -> bool:
    """Return True if a package URL is from the NPM or Yarn registry."""
    return urlparse(url).hostname in NPM_REGISTRY_CNAMES


def _get_packages_from_lockfile(
    source_dir: RootedPath, mirror_dir: RootedPath, yarn_lock: YarnLock, runtime_deps: set[str]
) -> list[YarnClassicPackage]:
    """Return a list of Packages for all dependencies in yarn.lock."""
    pyarn_packages: list[PYarnPackage] = yarn_lock.yarn_lockfile.packages()
    package_factory = _YarnClassicPackageFactory(source_dir, mirror_dir, runtime_deps)

    return [
        package_factory.create_package_from_pyarn_package(package) for package in pyarn_packages
    ]


def _get_main_package(source_dir: RootedPath, package_json: PackageJson) -> WorkspacePackage:
    """Return a WorkspacePackage for the main package in package.json."""
    if "name" not in package_json._data:
        raise PackageRejected(
            f"The package.json file located at {package_json.path.path} is missing the name field",
            solution="Ensure the package.json file has a valid name.",
        )
    return WorkspacePackage(
        name=package_json.data.get("name"),  # type: ignore
        version=package_json.data.get("version"),
        path=source_dir,
    )


def _get_workspace_packages(
    source_dir: RootedPath, workspaces: list[Workspace]
) -> list[WorkspacePackage]:
    """Return a WorkspacePackage for each Workspace."""
    return [
        WorkspacePackage(
            name=ws.package_json.data.get("name"),  # type: ignore
            version=ws.package_json.data.get("version"),
            path=source_dir.join_within_root(ws.path),
        )
        for ws in workspaces
    ]


def resolve_packages(project: Project, mirror_dir: RootedPath) -> Iterable[YarnClassicPackage]:
    """Return a list of Packages corresponding to all project dependencies."""
    workspaces = extract_workspace_metadata(project.source_dir)
    yarn_lock = YarnLock.from_file(project.source_dir.join_within_root("yarn.lock"))
    runtime_deps = find_runtime_deps(project.package_json, yarn_lock, workspaces)

    result: list[YarnClassicPackage] = []

    result.append(_get_main_package(project.source_dir, project.package_json))
    result.extend(_get_workspace_packages(project.source_dir, workspaces))
    result.extend(
        _get_packages_from_lockfile(project.source_dir, mirror_dir, yarn_lock, runtime_deps)
    )
    return result


def _read_name_from_tarball(tarball_path: RootedPath) -> str:
    """Read the package name from the package.json file in the cached tarball."""
    with tarfile.open(tarball_path) as tar:
        names = tar.getnames()
        package_json_subpath = next((n for n in names if n.endswith("package.json")), None)
        if package_json_subpath is None:
            raise ValueError(f"No package.json found in the tarball {tarball_path}")

        package_json = tar.extractfile(package_json_subpath)
        if package_json is None:
            raise ValueError(f"Failed to extract package.json from {tarball_path}")

        package_json_content: dict[str, Any] = json.loads(package_json.read())

        if (name := package_json_content.get("name")) is None:
            raise ValueError(f"No 'name' field found in package.json in {tarball_path}")

        return name


def _read_name_from_package_json(path: RootedPath) -> str:
    """Read the package name from a package.json file."""
    package_json = PackageJson.from_file(path)

    if (name := package_json.data.get("name")) is None:
        raise ValueError(f"No 'name' field found in package.json in {path}")

    return name
