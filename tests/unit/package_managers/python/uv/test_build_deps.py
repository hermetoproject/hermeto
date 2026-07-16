# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from unittest import mock

from hermeto.core.package_managers.python.uv.build_deps import download_build_dependencies
from hermeto.core.rooted_path import RootedPath


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
