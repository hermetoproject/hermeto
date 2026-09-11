# SPDX-License-Identifier: GPL-3.0-only
"""Base classes for binary package filtering."""

from abc import ABC, abstractmethod
from typing import Any

from hermeto.core.models.input import BINARY_FILTER_ALL


def parse_filter_spec(spec: str) -> set[str] | None:
    """Parse filter specification into allowed values set.

    Returns None if spec is ':all:' or contains ':all:' as any item.
    This matches pip's behavior where any occurrence of ':all:' means accept all.

    This is the single source of truth for ``:all:`` semantics; both the
    ``BinaryPackageFilter`` subclasses (wheel/RPM filtering) and the pip marker
    helper parse specs through it so a skip decision can never diverge from a
    wheel-selection decision.
    """
    if spec == BINARY_FILTER_ALL:
        return None

    filters = {stripped_filter for item in spec.split(",") if (stripped_filter := item.strip())}

    if BINARY_FILTER_ALL in filters:
        return None

    return filters


class BinaryPackageFilter(ABC):
    """Abstract base class for binary package filtering."""

    def _parse_filter_spec(self, spec: str) -> set[str] | None:
        """Parse a filter spec into allowed values (see ``parse_filter_spec``)."""
        return parse_filter_spec(spec)

    @abstractmethod
    def __contains__(self, item: Any) -> bool:
        """Check if item passes the filter criteria."""
