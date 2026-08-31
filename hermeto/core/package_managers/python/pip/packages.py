# SPDX-License-Identifier: GPL-3.0-only
import logging
import tarfile
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib import parse as urlparse

import pypi_simple
from packageurl import PackageURL

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import CargoPackageInput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import PROXY_COMMENT, PROXY_REF_TYPE, Component, ExternalReference
from hermeto.core.package_managers.general import download_binary_file
from hermeto.core.package_managers.python.pip.requirements import (
    SDIST_FILE_EXTENSIONS,
    get_external_requirement_filepath,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball

log = logging.getLogger(__name__)


@dataclass
class PipPackage(ABC):
    """Base class for a fetched pip package."""

    name: str
    requirement_file: str
    missing_req_file_checksum: bool
    package_type: str
    path: Path = field(init=False)

    def to_component(self, build_dependency: bool) -> Component:
        """Build an SBOM Component from this package."""
        missing_hash = (
            frozenset({self.requirement_file}) if self.missing_req_file_checksum else frozenset()
        )
        return Component(
            name=self.name,
            version=self._sbom_version(),
            purl=self._make_purl(),
            properties=PropertySet(
                missing_hash_in_file=missing_hash,
                pip_package_binary=(self.package_type == "wheel"),
                pip_build_dependency=build_dependency,
            ).to_properties(),
            external_references=self._get_external_refs(),
        )

    def _get_external_refs(self) -> list[ExternalReference] | None:
        return None

    def download(self, deps_dir: RootedPath) -> None:
        """Download this package. Sets self.path. Subclasses override for specific download."""
        ...

    def verify(self) -> bool:
        """Verify this package after download. Returns False if verification fails."""
        return True

    @abstractmethod
    def _make_purl(self) -> str: ...

    @abstractmethod
    def _sbom_version(self) -> str | None: ...


@dataclass
class PyPIPackage(PipPackage):
    """A package fetched from a PyPI index."""

    version: str
    index_url: str
    url: str = ""
    checksums_to_match: set[ChecksumInfo] = field(default_factory=set)
    auth_header: str | None = None
    proxy_url: str | None = None

    @property
    def remote_location(self) -> str:
        """Return the URL to download from."""
        return self.url

    def download_location(self, deps_dir: RootedPath) -> RootedPath:
        """Return the path where this package should be downloaded."""
        filename = Path(urlparse.urlparse(self.url).path).name
        return deps_dir.join_within_root(filename)

    def verify(self) -> bool:
        """Verify checksums and metadata for this package."""
        if self.checksums_to_match:
            if not _checksum_must_match_or_path_unlink(self.path, self.checksums_to_match):
                return False
        if self.package_type == "sdist":
            _check_metadata_in_sdist(self.path)
        return True

    def _get_external_refs(self) -> list[ExternalReference] | None:
        if self.proxy_url is None:
            return None
        return [ExternalReference(url=self.proxy_url, type=PROXY_REF_TYPE, comment=PROXY_COMMENT)]

    def _make_purl(self) -> str:
        qualifiers = None
        if self.index_url.rstrip("/") != pypi_simple.PYPI_SIMPLE_ENDPOINT.rstrip("/"):
            qualifiers = {"repository_url": self.index_url}
        return PackageURL(
            type="pypi",
            name=self.name,
            version=self.version,
            qualifiers=qualifiers,
        ).to_string()

    def _sbom_version(self) -> str:
        return self.version


@dataclass
class VCSPackage(PipPackage):
    """A package fetched from a VCS repository (git)."""

    url: str
    ref: str

    def download(self, deps_dir: RootedPath) -> None:
        """Fetch this package from VCS (git)."""
        filename = get_external_requirement_filepath(
            "vcs", f"git+{self.url}@{self.ref}", self.name, ""
        )
        download_to = deps_dir.join_within_root(filename)
        download_to.path.parent.mkdir(exist_ok=True, parents=True)
        clone_as_tarball(self.url, self.ref, to_path=download_to.path)
        self.path = download_to.path
        log.debug(
            "Successfully processed '%s' in path '%s'",
            self.name,
            self.path.relative_to(deps_dir.root),
        )

    def _make_purl(self) -> str:
        return PackageURL(
            type="pypi",
            name=self.name,
            qualifiers={"vcs_url": f"git+{self.url}@{self.ref}"},
        ).to_string()

    def _sbom_version(self) -> str | None:
        return None


@dataclass
class URLPackage(PipPackage):
    """A package fetched from a direct URL."""

    original_url: str
    checksum: str
    insecure: bool = False
    # Retain all lockfile hashes for download verification.
    checksums_to_match: set[ChecksumInfo] = field(default_factory=set)

    def download(self, deps_dir: RootedPath) -> None:
        """Download this package from a URL."""
        _, _, digest = self.checksum.partition(":")
        filepath = get_external_requirement_filepath("url", self.original_url, self.name, digest)
        download_to = deps_dir.join_within_root(filepath)
        download_to.path.parent.mkdir(exist_ok=True, parents=True)
        download_binary_file(self.original_url, download_to.path, insecure=self.insecure)
        self.path = download_to.path
        log.debug(
            "Successfully processed '%s' in path '%s'",
            self.name,
            self.path.relative_to(deps_dir.root),
        )

    def verify(self) -> bool:
        """Verify against every recorded hash, or the single ``checksum``."""
        checksums = self.checksums_to_match or (
            {ChecksumInfo.from_hash(self.checksum)} if self.checksum else set()
        )
        if checksums:
            return _checksum_must_match_or_path_unlink(self.path, checksums)
        return True

    def _make_purl(self) -> str:
        return PackageURL(
            type="pypi",
            name=self.name,
            qualifiers={"download_url": self.original_url, "checksum": self.checksum},
        ).to_string()

    def _sbom_version(self) -> str | None:
        return None


@dataclass
class PipPackageInfo:
    """Resolved pip package with all its dependencies."""

    name: str
    version: str | None
    requires: list[PipPackage]
    build_requires: list[PipPackage]
    requirements: list[RootedPath]
    packages_containing_rust_code: list[CargoPackageInput]


def _checksum_must_match_or_path_unlink(path: Path, checksum_info: Iterable[ChecksumInfo]) -> bool:
    try:
        must_match_any_checksum(path, checksum_info)
        return True
    except PackageRejected:
        path.unlink(missing_ok=True)
        log.warning("Download '%s' was removed from the output directory", path.name)
        return False


def _iter_zip_file(file_path: Path) -> Iterator[str]:
    with zipfile.ZipFile(file_path, "r") as zf:
        yield from zf.namelist()


def _iter_tar_file(file_path: Path) -> Iterator[str]:
    with tarfile.open(file_path, "r") as tar:
        for member in tar:
            yield member.name


def _is_pkg_info_dir(path: str) -> bool:
    """Simply check whether a path represents the PKG_INFO directory.

    Generally, it is in the format for example: pkg-1.0/PKG_INFO
    """
    return Path(path).name == "PKG-INFO"


def _check_metadata_in_sdist(sdist_path: Path) -> None:
    """Check if a downloaded sdist package has metadata.

    :param sdist_path: the path of a sdist package file.
    :type sdist_path: pathlib.Path
    :raise PackageRejected: if the sdist is invalid.
    """
    if sdist_path.name.endswith(".zip"):
        files_iter = _iter_zip_file(sdist_path)
    elif sdist_path.name.endswith(".tar.Z"):
        log.warning("Skip checking metadata from compressed sdist %s", sdist_path.name)
        return
    elif any(map(sdist_path.name.endswith, SDIST_FILE_EXTENSIONS)):
        files_iter = _iter_tar_file(sdist_path)
    else:
        # Invalid usage of the method (we don't download files without a known extension)
        raise ValueError(
            f"Cannot check metadata from {sdist_path}, "
            f"which does not have a known supported extension.",
        )

    try:
        if not any(map(_is_pkg_info_dir, files_iter)):
            raise PackageRejected(
                f"{sdist_path.name} does not include metadata (there is no PKG-INFO file). "
                "It is not a valid sdist and cannot be downloaded from PyPI.",
                solution=(
                    "Consider editing your requirements file to download the package from git "
                    "or a direct download URL instead."
                ),
            )
    except tarfile.ReadError as e:
        raise PackageRejected(f"Cannot open {sdist_path} as a Tar file. Error: {e}")
    except zipfile.BadZipFile as e:
        raise PackageRejected(f"Cannot open {sdist_path} as a Zip file. Error: {e}")
