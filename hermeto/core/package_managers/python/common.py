# SPDX-License-Identifier: GPL-3.0-only
"""Common utilities for Python package managers."""

import logging
import re
from collections.abc import Iterable
from enum import Enum, auto
from typing import NamedTuple

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
from hermeto.core.config import get_config
from hermeto.core.errors import FetchError
from hermeto.core.models.input import PythonBinaryFilters

log = logging.getLogger(__name__)


class ProxyOverride(NamedTuple):
    """A simple container to hold resolved artifact proxy parameters."""

    proxy_url: str | None
    proxy_login: str | None
    proxy_password: str | None

    @property
    def credentials_defined(self) -> bool:
        """Checks if credentials are defined."""
        return self.proxy_login is not None and self.proxy_password is not None

    @property
    def credentials(self) -> tuple[str | None, str | None]:
        """Convenience method to access credentials as a tuple."""
        return self.proxy_login, self.proxy_password


def get_project_packages_from(
    index_url: str,
    name: str,
    version: str,
    auth: requests.auth.HTTPBasicAuth | None = None,
) -> Iterable[pypi_simple.DistributionPackage]:
    """Get all the project packages from the given index URL."""
    config = get_config()
    timeout = (config.http.connect_timeout, config.http.read_timeout)
    with pypi_simple.PyPISimple(index_url, auth=auth) as client:
        try:
            project_page = client.get_project_page(name, timeout)
        except (requests.RequestException, pypi_simple.NoSuchProjectError) as e:
            if (
                (config.pip.proxy_url is not None or config.uv.proxy_url is not None)
                and isinstance(e, requests.HTTPError)
                and e.response is not None
                and e.response.status_code == 401
            ):
                raise FetchError(
                    f"Proxy {index_url} requires authentication. "
                    "Verify that proxy URL, login and password are set correctly."
                ) from e
            raise FetchError(f"PyPI query failed: {e}") from e

    return filter(
        lambda p: (
            p.version is not None
            and canonicalize_version(p.version) == canonicalize_version(version)
        ),
        project_page.packages,
    )


def make_proxy_override(pm_name: str) -> ProxyOverride | None:
    """Extract package manager related configuration from config if present."""
    config = get_config()
    pm = getattr(config, pm_name)
    proxy_url = str(pm.proxy_url) if pm.proxy_url is not None else None
    proxy_login = proxy_password = None
    if proxy_url is None:
        return None
    if pm.proxy_login and pm.proxy_password:
        proxy_password = pm.proxy_password.get_secret_value()
        proxy_login = pm.proxy_login
    return ProxyOverride(proxy_url, proxy_login, proxy_password)


class WheelsFilter(BinaryPackageFilter):
    """Filter Python wheels based on filter constraints."""

    class PackageFetchPolicy(Enum):
        """Distribution fetching policy for a registry dependency."""

        NO_BINARY = auto()
        ONLY_BINARY = auto()
        PREFER_BINARY = auto()

    def __init__(self, filters: PythonBinaryFilters) -> None:
        """Initialize the filter.

        - multiple values in a field are combined with OR logic
        - multiple fields are combined with AND logic
        - if a field is not provided (None), it is treated as `:all:` and treated as a match
        """
        package_specs = [package.strip() for package in filters.packages.split(",")]
        self.excluded_packages = {
            canonicalize_name(package[1:]) for package in package_specs if package.startswith("!")
        }

        # Negative-only selections retain the implicit :all: default.
        included_packages = set(package for package in package_specs if not package.startswith("!"))

        self.packages = (
            self._parse_filter_spec(",".join(included_packages)) if included_packages else None
        )
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
        return [wheel for wheel in wheels if self.matches_filename(wheel.filename)]

    def __contains__(self, wheel: pypi_simple.DistributionPackage) -> bool:
        """Check whether a wheel object matches the filter constraints."""
        return self.matches_filename(wheel.filename)

    def matches_filename(self, filename: str) -> bool:
        """Check whether a wheel filename matches the filter constraints."""
        try:
            _, _, _, tags = parse_wheel_filename(filename)
        except InvalidWheelFilename:
            log.warning("Skipping invalid wheel filename: %s", filename)
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
        is_compatible_py_impl = (
            self.py_impl is None
            or "py3" in interpreter
            or any(impl in interpreter for impl in self.py_impl)
        )

        if self.py_version is None:
            return is_compatible_py_impl

        wheel_py_versions = _parse_py_versions(interpreter)

        # we compare strings because e.g. '312' is all we get from parsing wheel interpreter tag
        is_same_major_ver = lambda other, this: str(other)[0] == str(this)[0]
        is_versions_match = lambda other, this: other == this
        is_compatible_abi = lambda abi: abi in ("abi3", "none")
        is_version_superseded = lambda other, this: (other < this) and is_compatible_abi(abi)
        is_compatible_candidate = lambda other, this: (
            is_same_major_ver(other, this)
            and (is_versions_match(other, this) or is_version_superseded(other, this))
        )

        this = self.py_version
        some_candidate_is_compatible = any(
            is_compatible_candidate(v, this) for v in wheel_py_versions
        )

        return is_compatible_py_impl and some_candidate_is_compatible

    def package_policy(self, package: str) -> PackageFetchPolicy:
        """Wheel fetching policy."""
        if package in self.excluded_packages:
            return self.PackageFetchPolicy.NO_BINARY
        if self.packages is None:
            return self.PackageFetchPolicy.PREFER_BINARY
        if package in self.packages:
            return self.PackageFetchPolicy.ONLY_BINARY
        return self.PackageFetchPolicy.NO_BINARY


def _parse_py_versions(interpreter: str) -> list[int]:
    """Parse all Python version numbers from a wheel interpreter tag.

    Supports multi-version compressed tags per PEP 425 (e.g. py38.py39.py310).
    Some real-world packages on PyPI use these compressed tags, for example
    semgrep (https://pypi.org/simple/semgrep/).

    >>> _parse_py_versions("cp312")
    [312]
    >>> _parse_py_versions("pp312")
    [312]
    >>> _parse_py_versions("py3")
    [3]
    >>> _parse_py_versions("py2.py3")
    [2, 3]
    >>> _parse_py_versions("py38.py39.py310")
    [38, 39, 310]
    """
    parts = interpreter.split(".")
    versions = []

    for part in parts:
        match = re.fullmatch(r"[a-z]+(\d+)", part)
        if match is not None:
            versions.append(int(match.group(1)))

    if not versions:
        raise RuntimeError(f"Invalid wheel interpreter: {interpreter}")

    return versions
