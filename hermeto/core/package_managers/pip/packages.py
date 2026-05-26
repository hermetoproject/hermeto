# SPDX-License-Identifier: GPL-3.0-only
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import pypi_simple
from packageurl import PackageURL

from hermeto.core.models.input import CargoPackageInput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import PROXY_COMMENT, PROXY_REF_TYPE, Component, ExternalReference
from hermeto.core.rooted_path import RootedPath


@dataclass
class PipPackage(ABC):
    """Base class for a fetched pip package."""

    name: str
    path: Path
    requirement_file: str
    missing_req_file_checksum: bool
    package_type: str

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

    @abstractmethod
    def _make_purl(self) -> str: ...

    @abstractmethod
    def _sbom_version(self) -> str | None: ...


@dataclass
class PyPIPackage(PipPackage):
    """A package fetched from a PyPI index."""

    version: str
    index_url: str
    proxy_url: str | None = None

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
