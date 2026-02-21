# SPDX-License-Identifier: GPL-3.0-only
"""Base classes for binary package filtering."""

from abc import ABC, abstractmethod
from typing import Any

from hermeto.core.models.input import BINARY_FILTER_ALL


class BinaryPackageFilter(ABC):
    """Abstract base class for binary package filtering."""

    def _parse_filter_spec(self, spec: str) -> set[str] | None:
        """Parse filter specification into allowed values set.

        Returns None if spec is ':all:' or contains ':all:' as any item.
        This matches pip's behavior where any occurrence of ':all:' means accept all.
        """
        if spec == BINARY_FILTER_ALL:
            return None

        filters = {stripped_filter for item in spec.split(",") if (stripped_filter := item.strip())}

        if BINARY_FILTER_ALL in filters:
            return None

        return filters

    @abstractmethod
    def __contains__(self, item: Any) -> bool:
        """Check if item passes the filter criteria."""
