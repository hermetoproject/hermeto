# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from hermeto.core.errors import PackageRejected
from hermeto.core.package_managers.maven.utils import (
    JAVA_TO_PYTHON_CHECKSUM_ALGORITHMS,
    convert_java_checksum_algorithm_to_python,
)


def test_convert_java_checksum_algorithm_to_python_sha256() -> None:
    """convert_java_checksum_algorithm_to_python returns sha256 for SHA-256."""
    assert convert_java_checksum_algorithm_to_python("SHA-256") == "sha256"


def test_convert_java_checksum_algorithm_to_python_all_supported() -> None:
    """convert_java_checksum_algorithm_to_python maps all supported Java algorithms."""
    for java_name, python_name in JAVA_TO_PYTHON_CHECKSUM_ALGORITHMS.items():
        assert convert_java_checksum_algorithm_to_python(java_name) == python_name


def test_convert_java_checksum_algorithm_to_python_unsupported_raises() -> None:
    """convert_java_checksum_algorithm_to_python raises PackageRejected for unsupported algorithm."""
    with pytest.raises(PackageRejected) as exc_info:
        convert_java_checksum_algorithm_to_python("SHA3-256")

    assert "Unsupported checksum algorithm" in str(exc_info.value)
    assert "SHA3-256" in str(exc_info.value)
    assert exc_info.value.solution
    assert "Supported algorithms" in exc_info.value.solution
