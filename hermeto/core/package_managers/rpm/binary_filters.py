# SPDX-License-Identifier: GPL-3.0-only
"""RPM-specific binary package filtering."""

from typing import Any

from hermeto.core.binary_filters import BinaryPackageFilter
from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import BINARY_FILTER_ALL, RpmBinaryFilters
from hermeto.core.package_managers.rpm.redhat import LockfileArch


class UnsatisfiableArchitectureFilter(PackageRejected):
    """RPM architecture filter constraints cannot be satisfied by lockfile architectures."""


class RPMArchitectureFilter(BinaryPackageFilter):
    """Filter RPM architectures based on user constraints."""

    def __init__(self, filters: RpmBinaryFilters | None = None) -> None:
        """Initialize with optional filters, defaulting to accept all."""
        arch_spec = filters.arch if filters else BINARY_FILTER_ALL
        self.arch_constraints: set[str] | None = self._parse_filter_spec(arch_spec)

    def __contains__(self, item: Any) -> bool:
        """Return True if an architecture is allowed by the filter constraints."""
        if self.arch_constraints is None:
            return True

        if isinstance(item, str):
            arch = item
        elif isinstance(item, LockfileArch):
            arch = item.arch
        else:
            return False

        return arch in self.arch_constraints

    def filter(self, arches: list[LockfileArch]) -> list[LockfileArch]:
        """Filter a list of architectures based on constraints."""
        return [arch for arch in arches if arch in self]

    def ensure_satisfiable(self, arches: list[LockfileArch]) -> None:
        """Ensure constraints can be satisfied by the provided architectures."""
        if self.arch_constraints is not None:
            available = {arch.arch for arch in arches}
            unsatisfiable = self.arch_constraints - available
            if unsatisfiable:
                raise UnsatisfiableArchitectureFilter(
                    f"Specified RPM architecture(s) not found in lockfile: {', '.join(sorted(unsatisfiable))}",
                    solution=f"Use one of the available architectures: {', '.join(sorted(available))}",
                )

    def validate_and_filter(self, arches: list[LockfileArch]) -> list[LockfileArch]:
        """Ensure constraints are satisfiable, then filter."""
        self.ensure_satisfiable(arches)
        filtered = self.filter(arches)

        return filtered
