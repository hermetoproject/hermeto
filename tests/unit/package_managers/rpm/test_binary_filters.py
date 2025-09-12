from typing import Optional
from unittest import mock

import pytest

from hermeto.core.models.input import RpmBinaryFilters
from hermeto.core.package_managers.rpm.binary_filters import (
    RPMArchitectureFilter,
    UnsatisfiableArchitectureFilter,
)
from hermeto.core.package_managers.rpm.redhat import LockfileArch


@pytest.mark.parametrize(
    "filters,expected_arches",
    [
        pytest.param(None, ["x86_64", "aarch64", "s390x", "ppc64le"], id="none_accepts_all"),
        pytest.param(RpmBinaryFilters(arch="x86_64"), ["x86_64"], id="single_arch"),
        pytest.param(
            RpmBinaryFilters(arch="x86_64,aarch64"), ["x86_64", "aarch64"], id="multiple_arches"
        ),
        pytest.param(
            RpmBinaryFilters(arch=":all:"),
            ["x86_64", "aarch64", "s390x", "ppc64le"],
            id="all_keyword",
        ),
    ],
)
def test_validate_and_filter_success(
    filters: Optional[RpmBinaryFilters], expected_arches: list[str]
) -> None:
    """Test validate_and_filter with satisfiable constraints."""
    arch_filter = RPMArchitectureFilter(filters)

    lockfile_arches = ["x86_64", "aarch64", "s390x", "ppc64le"]
    all_arches = [mock.Mock(spec=LockfileArch, arch=arch) for arch in lockfile_arches]

    filtered = arch_filter.validate_and_filter(all_arches)  # type: ignore[arg-type]
    filtered_arch_strings = [arch.arch for arch in filtered]

    assert filtered_arch_strings == expected_arches


@pytest.mark.parametrize(
    "filters",
    [
        pytest.param(RpmBinaryFilters(arch="armv7l"), id="no_match"),
        pytest.param(RpmBinaryFilters(arch="x86_64,armv7l"), id="partial_match"),
    ],
)
def test_validate_and_filter_unsatisfiable_constraints(filters: Optional[RpmBinaryFilters]) -> None:
    """Test validate_and_filter raises UnsatisfiableArchitectureFilter for unsatisfiable constraints."""
    arch_filter = RPMArchitectureFilter(filters)

    lockfile_arches = ["x86_64", "aarch64", "s390x", "ppc64le"]
    all_arches = [mock.Mock(spec=LockfileArch, arch=arch) for arch in lockfile_arches]

    with pytest.raises(UnsatisfiableArchitectureFilter):
        _ = arch_filter.validate_and_filter(all_arches)  # type: ignore[arg-type]
