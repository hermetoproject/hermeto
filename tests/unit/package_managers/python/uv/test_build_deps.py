# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from unittest import mock

import pypi_simple

from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.package_managers.python.pip.packages import PyPIPackage
from hermeto.core.package_managers.python.uv.build_deps import (
    download_build_dependencies,
    to_component,
)
from hermeto.core.rooted_path import RootedPath

BUILD_REQ_FILE = "requirements-build.txt"


@mock.patch("hermeto.core.package_managers.python.uv.build_deps.download_from_requirement_files")
def test_download_build_dependencies_without_file(mock_download: mock.Mock, tmp_path: Path) -> None:
    result = download_build_dependencies(RootedPath(tmp_path), RootedPath(tmp_path / "output"))

    assert result == []
    mock_download.assert_not_called()


@mock.patch("hermeto.core.package_managers.python.uv.build_deps.download_from_requirement_files")
def test_download_build_dependencies(mock_download: mock.Mock, tmp_path: Path) -> None:
    package_dir = RootedPath(tmp_path)
    output_dir = RootedPath(tmp_path / "output")
    req_file = package_dir.join_within_root("requirements-build.txt")
    req_file.path.write_text("setuptools==80.9.0\n")

    result = download_build_dependencies(package_dir, output_dir)

    assert result == mock_download.return_value
    mock_download.assert_called_once_with(output_dir, [req_file], deps_dir_name="uv")


def build_dep(package_type: str = "sdist", missing_checksum: bool = False) -> PyPIPackage:
    return PyPIPackage(
        name="setuptools",
        path=Path("deps/uv/setuptools-80.9.0.tar.gz"),
        requirement_file=BUILD_REQ_FILE,
        missing_req_file_checksum=missing_checksum,
        package_type=package_type,
        version="80.9.0",
        index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
    )


def test_to_component_attributes_the_dependency_to_uv() -> None:
    """pip downloaded it, but the uv project declared it, so the SBOM must say uv."""
    props = PropertySet.from_properties(to_component(build_dep()).properties)

    assert props.uv_build_dependency
    assert not props.pip_build_dependency
    assert not props.pip_package_binary


def test_to_component_marks_a_wheel_as_a_uv_binary() -> None:
    props = PropertySet.from_properties(to_component(build_dep(package_type="wheel")).properties)

    assert props.uv_package_binary
    assert not props.pip_package_binary


def test_to_component_records_the_file_that_gave_no_checksum() -> None:
    props = PropertySet.from_properties(to_component(build_dep(missing_checksum=True)).properties)

    assert props.missing_hash_in_file == frozenset({BUILD_REQ_FILE})


def test_to_component_keeps_the_identity_pip_resolved() -> None:
    component = to_component(build_dep())

    assert component.name == "setuptools"
    assert component.version == "80.9.0"
    assert component.purl == "pkg:pypi/setuptools@80.9.0"
