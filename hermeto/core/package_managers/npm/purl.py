# SPDX-License-Identifier: GPL-3.0-only
import functools
from pathlib import Path
from urllib.parse import urlparse

from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.package_managers.npm.urls import (
    _classify_resolved_url,
    _extract_git_info_npm,
    _normalize_resolved_url,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID, get_repo_id


class _Purlifier:
    """Generates purls for npm packages."""

    def __init__(self, pkg_path: RootedPath) -> None:
        """Init a purlifier for the package at pkg_path."""
        self._pkg_path = pkg_path

    @functools.cached_property
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

        resolved_url = _normalize_resolved_url(resolved_url)
        dep_type = _classify_resolved_url(resolved_url)

        if dep_type == "registry":
            pass
        elif dep_type == "git":
            info = _extract_git_info_npm(resolved_url)
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
