# SPDX-License-Identifier: GPL-3.0-only
"""Tests for common JS git clone utilities in js_utils.py."""

import pytest

from hermeto.core.errors import PackageRejected
from hermeto.core.package_managers.js_utils import extract_git_url_parts


class TestExtractGitUrlParts:
    """Tests for extract_git_url_parts."""

    def test_https_url(self) -> None:
        result = extract_git_url_parts("https://github.com/owner/repo.git")
        assert result == {"host": "github.com", "namespace": "owner", "repo": "repo"}

    def test_https_url_without_git_suffix(self) -> None:
        result = extract_git_url_parts("https://github.com/owner/repo")
        assert result == {"host": "github.com", "namespace": "owner", "repo": "repo"}

    def test_ssh_protocol_url(self) -> None:
        result = extract_git_url_parts("ssh://git@github.com/owner/repo.git")
        assert result == {"host": "github.com", "namespace": "owner", "repo": "repo"}

    def test_scp_style_url(self) -> None:
        result = extract_git_url_parts("git@github.com:owner/repo.git")
        assert result == {"host": "github.com", "namespace": "owner", "repo": "repo"}

    def test_scp_style_url_without_git_suffix(self) -> None:
        result = extract_git_url_parts("git@github.com:owner/repo")
        assert result == {"host": "github.com", "namespace": "owner", "repo": "repo"}

    def test_deeply_nested_path_protocol_url(self) -> None:
        """Multi-level paths (e.g. GitLab subgroups) should split on the last '/'."""
        result = extract_git_url_parts("https://gitlab.com/org/subgroup/repo.git")
        assert result == {"host": "gitlab.com", "namespace": "org/subgroup", "repo": "repo"}

    def test_deeply_nested_path_scp_url(self) -> None:
        result = extract_git_url_parts("git@gitlab.com:org/subgroup/repo.git")
        assert result == {"host": "gitlab.com", "namespace": "org/subgroup", "repo": "repo"}

    def test_http_url(self) -> None:
        result = extract_git_url_parts("http://bitbucket.org/ns/repo.git")
        assert result == {"host": "bitbucket.org", "namespace": "ns", "repo": "repo"}

    def test_scp_style_invalid_url_raises(self) -> None:
        with pytest.raises(PackageRejected, match="Cannot parse git URL"):
            extract_git_url_parts("not-a-valid-url")

    def test_scp_style_no_colon_raises(self) -> None:
        with pytest.raises(PackageRejected, match="Cannot parse git URL"):
            extract_git_url_parts("git@github.com/owner/repo.git")
