# SPDX-License-Identifier: GPL-3.0-only
from typing import Any
from unittest import mock

from hermeto.core.package_managers.python.pip import lockfile as pip
from hermeto.core.package_managers.python.pip.packages import (
    PipPackage,
    PyPIPackage,
    URLPackage,
    VCSPackage,
)
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF


class TestDownloadDependencies:
    """Tests for the single shared download pipeline used by all lockfile formats."""

    @mock.patch("hermeto.core.package_managers.python.pip.lockfile.async_download_files")
    @mock.patch("hermeto.core.package_managers.python.pip.packages.download_binary_file")
    @mock.patch("hermeto.core.package_managers.python.pip.packages.clone_as_tarball")
    @mock.patch(
        "hermeto.core.package_managers.python.pip.packages._checksum_must_match_or_path_unlink",
        return_value=True,
    )
    @mock.patch("hermeto.core.package_managers.python.pip.packages._check_metadata_in_sdist")
    def test_dispatches_by_type(
        self,
        mock_metadata: Any,
        mock_checksum: Any,
        mock_clone: Any,
        mock_download: Any,
        mock_async: Any,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """vcs is cloned, url is downloaded sync, index is batched — all become components."""
        deps = [
            VCSPackage(
                name="vcs-pkg",
                requirement_file="pylock.toml",
                missing_req_file_checksum=True,
                package_type="",
                url="https://github.com/spam/eggs",
                ref=GIT_REF,
            ),
            URLPackage(
                name="url-pkg",
                requirement_file="pylock.toml",
                missing_req_file_checksum=False,
                package_type="",
                original_url="https://example.org/url_pkg.tar.gz",
                checksum="sha256:abcdef",
            ),
            PyPIPackage(
                name="index-pkg",
                requirement_file="pylock.toml",
                missing_req_file_checksum=False,
                package_type="sdist",
                version="1.0",
                index_url="https://pypi.org/simple/",
                url="https://files.example.org/index_pkg-1.0.tar.gz",
            ),
        ]

        result = pip._download_dependencies(deps, rooted_tmp_path)

        by_type = sorted(type(d).__name__ for d in result)
        assert by_type == ["PyPIPackage", "URLPackage", "VCSPackage"]
        mock_clone.assert_called_once()
        mock_download.assert_called_once()
        mock_async.assert_called_once()

    @mock.patch("hermeto.core.package_managers.python.pip.lockfile.async_download_files")
    @mock.patch("hermeto.core.package_managers.python.pip.packages.download_binary_file")
    @mock.patch(
        "hermeto.core.package_managers.python.pip.packages._checksum_must_match_or_path_unlink",
        return_value=False,
    )
    def test_url_dropped_on_checksum_mismatch(
        self,
        mock_checksum: Any,
        mock_download: Any,
        mock_async: Any,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """A URL package whose checksum does not match is dropped from the result."""
        deps: list[PipPackage] = [
            URLPackage(
                name="url-pkg",
                requirement_file="reqs.txt",
                missing_req_file_checksum=False,
                package_type="",
                original_url="https://example.org/url_pkg.tar.gz",
                checksum="sha256:abcdef",
            ),
        ]

        assert pip._download_dependencies(deps, rooted_tmp_path) == []

    @mock.patch("hermeto.core.package_managers.python.pip.lockfile.async_download_files")
    def test_index_batch_skips_existing_and_sets_auth_header(
        self,
        mock_async: Any,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Only missing files are batched, and proxy auth becomes a per-URL header."""
        existing = PyPIPackage(
            name="cached",
            requirement_file="reqs.txt",
            missing_req_file_checksum=False,
            package_type="sdist",
            version="1.0",
            index_url="https://pypi.org/simple/",
            url="https://files.example.org/cached-1.0.tar.gz",
        )
        # Pre-create the destination so it is skipped by the batch.
        existing.download_location(rooted_tmp_path).path.touch()
        fresh = PyPIPackage(
            name="fresh",
            requirement_file="reqs.txt",
            missing_req_file_checksum=False,
            package_type="sdist",
            version="1.0",
            index_url="https://pypi.org/simple/",
            url="https://files.example.org/fresh-1.0.tar.gz",
            auth_header="Basic dXNlcjpwYXNz",
        )

        with (
            mock.patch(
                "hermeto.core.package_managers.python.pip.packages._checksum_must_match_or_path_unlink",
                return_value=True,
            ),
            mock.patch(
                "hermeto.core.package_managers.python.pip.packages._check_metadata_in_sdist"
            ),
        ):
            pip._download_dependencies([existing, fresh], rooted_tmp_path)

        files_arg, _ = mock_async.call_args[0][0], mock_async.call_args
        assert list(files_arg) == [fresh.url]  # cached one skipped
        headers = mock_async.call_args.kwargs["headers"]
        assert headers == {fresh.url: {"Authorization": "Basic dXNlcjpwYXNz"}}
