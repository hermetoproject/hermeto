# SPDX-License-Identifier: GPL-3.0-only
"""Tests for common JS git clone utilities in js_utils.py."""

from pathlib import Path
from unittest import mock

import pytest

from hermeto.core.errors import PackageRejected
from hermeto.core.package_managers.js_utils import clone_repo_pack_archive, extract_git_url_parts
from hermeto.core.rooted_path import RootedPath


class TestExtractGitUrlParts:
    @pytest.mark.parametrize(
        "url, expected",
        [
            (
                "https://github.com/owner/repo.git",
                {"host": "github.com", "namespace": "owner", "repo": "repo"},
            ),
            (
                "https://github.com/owner/repo",
                {"host": "github.com", "namespace": "owner", "repo": "repo"},
            ),
            (
                "ssh://git@github.com/owner/repo.git",
                {"host": "github.com", "namespace": "owner", "repo": "repo"},
            ),
            (
                "git@github.com:owner/repo.git",
                {"host": "github.com", "namespace": "owner", "repo": "repo"},
            ),
            (
                "git@github.com:owner/repo",
                {"host": "github.com", "namespace": "owner", "repo": "repo"},
            ),
            (
                "https://gitlab.com/org/subgroup/repo.git",
                {"host": "gitlab.com", "namespace": "org/subgroup", "repo": "repo"},
            ),
            (
                "git@gitlab.com:org/subgroup/repo.git",
                {"host": "gitlab.com", "namespace": "org/subgroup", "repo": "repo"},
            ),
            (
                "http://bitbucket.org/ns/repo.git",
                {"host": "bitbucket.org", "namespace": "ns", "repo": "repo"},
            ),
        ],
    )
    def test_valid_urls(self, url: str, expected: dict[str, str]) -> None:
        assert extract_git_url_parts(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "not-a-valid-url",
            "git@github.com/owner/repo.git",
        ],
    )
    def test_invalid_urls(self, url: str) -> None:
        with pytest.raises(PackageRejected, match="Cannot parse git URL"):
            extract_git_url_parts(url)


class TestCloneRepoPackArchive:
    @mock.patch("hermeto.core.package_managers.js_utils.clone_as_tarball")
    def test_creates_tarball_at_expected_path(
        self, mock_clone: mock.Mock, tmp_path: Path
    ) -> None:
        deps_dir = RootedPath(tmp_path)

        result = clone_repo_pack_archive(
            "https://github.com/owner/my-repo.git", "abc123", deps_dir
        )

        expected = tmp_path / "github.com" / "owner" / "my-repo" / "my-repo-external-gitcommit-abc123.tgz"
        assert result.path == expected
        assert expected.parent.is_dir()
        mock_clone.assert_called_once_with(
            "https://github.com/owner/my-repo.git", "abc123", expected
        )

    @mock.patch("hermeto.core.package_managers.js_utils.clone_as_tarball")
    def test_scp_style_url(self, mock_clone: mock.Mock, tmp_path: Path) -> None:
        deps_dir = RootedPath(tmp_path)

        result = clone_repo_pack_archive(
            "git@github.com:org/pkg.git", "def456", deps_dir
        )

        expected = tmp_path / "github.com" / "org" / "pkg" / "pkg-external-gitcommit-def456.tgz"
        assert result.path == expected
        mock_clone.assert_called_once_with(
            "git@github.com:org/pkg.git", "def456", expected
        )
