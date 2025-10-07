"""This module provides functionality to process package distributions from PyPI (sdist and wheel)."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, cast

import pypi_simple
import requests
from packaging.utils import canonicalize_version

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import get_config
from hermeto.core.errors import FetchError, PackageRejected
from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.package_managers.pip.requirements import PipRequirement
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

PIP_NO_SDIST_DOC = "https://github.com/hermetoproject/hermeto/blob/main/docs/pip.md#dependency-does-not-distribute-sources"


@dataclass
class DistributionPackageInfo:
    """A class representing relevant information about a distribution package."""

    name: str
    version: str
    package_type: Literal["sdist", "wheel"]
    path: Path
    url: str
    index_url: str
    is_yanked: bool

    # PyPi only returns a single checksum for a given download artifact
    pypi_checksums: set[ChecksumInfo] = field(default_factory=set)
    # "User" checksums *must* come from a 'requirements*.txt' file or equivalent
    req_file_checksums: set[ChecksumInfo] = field(default_factory=set)

    checksums_to_match: set[ChecksumInfo] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self.checksums_to_match = self._determine_checksums_to_match()

    def _determine_checksums_to_match(self) -> set[ChecksumInfo]:
        """Determine the set of checksums to match for a given distribution package."""
        checksums: set[ChecksumInfo] = set()

        if self.pypi_checksums and self.req_file_checksums:
            checksums = self.pypi_checksums.intersection(self.req_file_checksums)
            msg = "using intersection of requirements-file and PyPI-reported checksums"
        elif self.pypi_checksums:
            checksums = self.pypi_checksums
            msg = "using PyPI-reported checksums"
        elif self.req_file_checksums:
            checksums = self.req_file_checksums
            msg = "using requirements-file checksums"
        else:
            msg = "no checksums reported by PyPI or specified in requirements file"

        log.debug("%s: %s", self.path.name, msg)
        return checksums

    def should_download(self) -> bool:
        """Determine if this artifact should be downloaded.

        If there are checksums in the requirements file, but they do not match
        with those reported by PyPI, we do not want to download the artifact.

        Otherwise, we do.
        """
        return (
            len(self.checksums_to_match) > 0
            or len(self.pypi_checksums) == 0
            or len(self.req_file_checksums) == 0
        )

    @property
    def has_checksums_to_match(self) -> bool:
        """Determine if we have checksums to match against.

        This decides whether or not we
        call `hermeto.core.checksum.must_match_any_checksum()`
        """
        return len(self.checksums_to_match) > 0

    @property
    def download_info(self) -> dict[str, Any]:
        """Only necessary attributes to process download information."""
        return {
            "package": self.name,
            "version": self.version,
            "path": self.path,
        }


def _sdist_preference(sdist_pkg: DistributionPackageInfo) -> tuple[int, int]:
    """
    Compute preference for a sdist package, can be used to sort in ascending order.

    Prefer files that are not yanked over ones that are.
    Within the same category (yanked vs. not), prefer .tar.gz > .zip > anything else.
    """
    # Higher number = higher preference
    yanked_pref = 0 if sdist_pkg.is_yanked else 1

    filename = sdist_pkg.name
    if filename.endswith(".tar.gz"):
        filetype_pref = 2
    elif filename.endswith(".zip"):
        filetype_pref = 1
    else:
        filetype_pref = 0

    return yanked_pref, filetype_pref


def _find_the_best_sdist(sdists: list[DistributionPackageInfo]) -> DistributionPackageInfo:
    """Find the best sdist package based on our preference."""
    best = max(sdists, key=_sdist_preference)
    if best.is_yanked:
        log.warning("Package %s==%s is yanked, use a different version", best.name, best.version)

    return best


def _get_project_packages_from(
    index_url: str,
    name: str,
    version: str,
) -> Iterable[pypi_simple.DistributionPackage]:
    """Get all the project packages from the given index URL."""
    timeout = get_config().requests_timeout
    with pypi_simple.PyPISimple(index_url) as client:
        try:
            project_page = client.get_project_page(name, timeout)
        except (requests.RequestException, pypi_simple.NoSuchProjectError) as e:
            raise FetchError(f"PyPI query failed: {e}") from e

    return filter(
        lambda p: p.version is not None
        and canonicalize_version(p.version) == canonicalize_version(version),
        project_page.packages,
    )


def process_package_distributions(
    requirement: PipRequirement,
    pip_deps_dir: RootedPath,
    binary_filters: Optional[PipBinaryFilters] = None,
    index_url: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
) -> list[DistributionPackageInfo]:
    """
    Return a list of DPI objects for the provided pip package.

    Scrape the package's PyPI page and generate a list of all available
    artifacts. Filter by version and allowed artifact type. Filter to find the
    best matching sdist artifact. Process wheel artifacts.

    A note on nomenclature (to address a common misconception)

    To strictly follow Python packaging terminology, we should avoid "source",
    since it is an overloaded term - so rather than talking about "source" vs.
    "wheel" we should use "sdist" vs "wheel" instead.

    _sdist_ - "source distribution": a tarball (usually) of a project's entire repo
    _wheel_ - built distribution: a stripped-down version of sdist, containing
    **only** Python modules and necessary application data which are needed to
    install and run the application (note that it doesn't need to and commonly
    won't include Python bytecode modules *.pyc)

    :param requirement: which pip package to process
    :param str pip_deps_dir:
    :param binary_filters: process wheels?
    :return: a list of DPI
    :rtype: list[DistributionPackageInfo]
    """
    allowed_distros = ["sdist", "wheel"] if binary_filters is not None else ["sdist"]
    processed_dpis: list[DistributionPackageInfo] = []
    name = requirement.package
    version = requirement.version_specs[0][1]
    sdists: list[DistributionPackageInfo] = []
    req_file_checksums = set(map(ChecksumInfo.from_hash, requirement.hashes))
    wheels: list[DistributionPackageInfo] = []

    packages = _get_project_packages_from(index_url, name, version)

    for package in packages:
        if package.package_type is None or package.package_type not in allowed_distros:
            continue

        pypi_checksums: set[ChecksumInfo] = {
            ChecksumInfo(algorithm, digest) for algorithm, digest in package.digests.items()
        }

        dpi = DistributionPackageInfo(
            name,
            version,
            cast(Literal["sdist", "wheel"], package.package_type),
            pip_deps_dir.join_within_root(package.filename).path,
            package.url,
            index_url,
            package.is_yanked,
            pypi_checksums,
            req_file_checksums,
        )

        if dpi.should_download():
            if dpi.package_type == "sdist":
                sdists.append(dpi)
            else:
                wheels.append(dpi)
        else:
            log.info("Filtering out %s due to checksum mismatch", package.filename)

    if sdists:
        best_sdist = _find_the_best_sdist(sdists)
        processed_dpis.append(best_sdist)
    else:
        log.warning("No sdist found for package %s==%s", name, version)

        if len(wheels) == 0:
            if binary_filters is not None:
                solution = (
                    "Please check that the package exists on PyPI or that the name"
                    " and version are correct.\n"
                )
                docs = None
            else:
                solution = (
                    "It seems that this version does not exist or isn't published as an"
                    " sdist.\n"
                    "Try to specify the dependency directly via a URL instead, for example,"
                    " the tarball for a GitHub release.\n"
                    "Alternatively, allow the use of wheels."
                )
                docs = PIP_NO_SDIST_DOC
            raise PackageRejected(
                f"No distributions found for package {name}=={version}",
                solution=solution,
                docs=docs,
            )

    processed_dpis.extend(wheels)

    return processed_dpis
