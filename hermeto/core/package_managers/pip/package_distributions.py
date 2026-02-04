"""This module provides functionality to process package distributions from PyPI (sdist and wheel)."""

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any, Literal, cast

import pypi_simple
import requests
from packaging.tags import Tag
from packaging.utils import (
    InvalidWheelFilename,
    canonicalize_name,
    canonicalize_version,
    parse_wheel_filename,
)

from hermeto.core.binary_filters import BinaryPackageFilter
from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import get_config
from hermeto.core.errors import FetchError, PackageRejected
from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.package_managers.pip.requirements import PipRequirement
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


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
    config = get_config()
    timeout = (config.http.connect_timeout, config.http.read_timeout)
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
    binary_filters: PipBinaryFilters | None = None,
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
    name = requirement.package
    version = requirement.version_specs[0][1]
    req_file_checksums = set(map(ChecksumInfo.from_hash, requirement.hashes))

    packages = list(_get_project_packages_from(index_url, name, version))
    sdists = filter(lambda x: x.package_type == "sdist", packages)
    wheels = filter(lambda x: x.package_type == "wheel", packages)

    # process only sdists if no binary filters are provided
    if binary_filters is None:
        allowed_distros = ["sdist"]
        to_process = chain(sdists)
    else:
        wheels_filter = WheelsFilter(binary_filters)
        # process both sdists and wheels if no packages are provided in the binary filters
        if wheels_filter.packages is None:
            allowed_distros = ["sdist", "wheel"]
            to_process = chain(sdists, wheels_filter.filter(wheels))
        # process only wheels for packages in the binary filters
        elif name in wheels_filter.packages:
            allowed_distros = ["wheel"]
            to_process = chain(wheels_filter.filter(wheels))
        # process only sdists for packages NOT in the binary filters
        else:
            allowed_distros = ["sdist"]
            to_process = chain(sdists)

    filtered_sdists: list[DistributionPackageInfo] = []
    filtered_wheels: list[DistributionPackageInfo] = []

    for package in to_process:
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
                filtered_sdists.append(dpi)
            else:
                filtered_wheels.append(dpi)
        else:
            log.info("Filtering out %s due to checksum mismatch", package.filename)

    if allowed_distros == ["sdist"]:
        return _process_no_binary_mode(filtered_sdists, name, version)

    if allowed_distros == ["wheel"]:
        return _process_only_binary_mode(filtered_wheels, name, version)

    return _process_prefer_binary_mode(filtered_sdists, filtered_wheels, name, version)


def _process_no_binary_mode(
    sdists: list[DistributionPackageInfo],
    name: str,
    version: str,
) -> list[DistributionPackageInfo]:
    if not sdists:
        raise PackageRejected(
            f"No distributions found for package {name}=={version}",
            solution="Please check that the package exists and that the name and version are correct.",
        )

    return [_find_the_best_sdist(sdists)]


def _process_prefer_binary_mode(
    sdists: list[DistributionPackageInfo],
    wheels: list[DistributionPackageInfo],
    name: str,
    version: str,
) -> list[DistributionPackageInfo]:
    return wheels if wheels else _process_no_binary_mode(sdists, name, version)


def _process_only_binary_mode(
    wheels: list[DistributionPackageInfo],
    name: str,
    version: str,
) -> list[DistributionPackageInfo]:
    if not wheels:
        raise PackageRejected(
            f"No wheels found for package {name}=={version}",
            solution="Please update the binary filters.",
        )

    return wheels


class WheelsFilter(BinaryPackageFilter):
    """Filter PyPI wheels based on filter constraints."""

    def __init__(self, filters: PipBinaryFilters) -> None:
        """Initialize the filter.

        - multiple values in a field are combined with OR logic
        - multiple fields are combined with AND logic
        - if a field is not provided (None), it is treated as `:all:` and treated as a match
        """
        self.packages = self._parse_filter_spec(filters.packages)
        if self.packages is not None:
            self.packages = {canonicalize_name(package) for package in self.packages}

        self.arch = self._parse_filter_spec(filters.arch)
        self.os = self._parse_filter_spec(filters.os)
        self.py_version = filters.py_version
        self.py_impl = self._parse_filter_spec(filters.py_impl)
        self.abi = self._parse_filter_spec(filters.abi)
        self.platform_regex = filters.platform

    def filter(
        self, wheels: Iterable[pypi_simple.DistributionPackage]
    ) -> list[pypi_simple.DistributionPackage]:
        """Filter a list of wheels based on the filter constraints."""
        return [wheel for wheel in wheels if wheel in self]

    def __contains__(self, item: pypi_simple.DistributionPackage) -> bool:
        """Check if the wheel filename matches the filter constraints."""
        try:
            _, _, _, tags = parse_wheel_filename(item.filename)
        except InvalidWheelFilename:
            log.warning("Skipping invalid wheel filename: %s", item.filename)
            return False

        # usually `tags` set contains only one tag
        # see https://packaging.pypa.io/en/stable/utils.html#packaging.utils.parse_wheel_filename
        return any(self._matches_filter_constraints(tag) for tag in tags)

    def _matches_filter_constraints(self, tag: Tag) -> bool:
        return (
            self._compatible_tag_interpreter(tag.interpreter, tag.abi)
            and self._compatible_tag_abi(tag.abi)
            and self._compatible_tag_platform(tag.platform)
        )

    def _compatible_tag_platform(self, platform: str) -> bool:
        if self.platform_regex is not None:
            return re.search(self.platform_regex, platform) is not None

        compatible_arch = (
            self.arch is None or platform == "any" or any(arch in platform for arch in self.arch)
        )
        compatible_os = (
            self.os is None or platform == "any" or any(os in platform for os in self.os)
        )

        return compatible_arch and compatible_os

    def _compatible_tag_abi(self, abi: str) -> bool:
        return self.abi is None or abi in ("abi3", "none") or any(a in abi for a in self.abi)

    def _compatible_tag_interpreter(self, interpreter: str, abi: str) -> bool:
        compatible_py_impl = (
            self.py_impl is None
            or "py3" in interpreter
            or any(impl in interpreter for impl in self.py_impl)
        )

        wheel_py_version = _parse_py_version(interpreter)
        compatible_py_version = (
            self.py_version is None
            or wheel_py_version == self.py_version
            or (wheel_py_version < self.py_version and abi in ("abi3", "none"))
        )

        return compatible_py_impl and compatible_py_version


def _parse_py_version(interpreter: str) -> int:
    """
    >>> _parse_py_version("cp312")
    312
    >>> _parse_py_version("pp312")
    312
    >>> _parse_py_version("py3")
    3
    >>> _parse_py_version("py2.py3")
    3
    """
    parts = interpreter.split(".")
    versions = []

    for part in parts:
        match = re.fullmatch(r"[a-z]+(\d+)", part)
        if match is not None:
            versions.append(int(match.group(1)))

    if not versions:
        raise RuntimeError(f"Invalid wheel interpreter: {interpreter}")

    return max(versions)
