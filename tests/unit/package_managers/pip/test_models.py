# SPDX-License-Identifier: GPL-3.0-only
"""Tests for pip package manager data model."""

from pathlib import Path

from hermeto.core.package_managers.pip.models import (
    URLArtifact,
    VCSArtifact,
)


class TestVCSArtifact:
    """Test VCSArtifact dataclass."""

    def test_version_property(self) -> None:
        """Test that VCS version property returns git+ format."""
        artifact = VCSArtifact(
            package="myproject",
            path=Path("/tmp/myproject-gitcommit-abc123.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=True,
            build_dependency=False,
            url="https://github.com/user/myproject.git",
            host="github.com",
            namespace="user",
            repo="myproject",
            ref="abc123",
        )

        expected_version = "git+https://github.com/user/myproject.git@abc123"

        assert artifact.version == expected_version


class TestURLArtifact:
    """Test URLArtifact dataclass."""

    def test_default_package_type_is_none(self) -> None:
        """Test that default package_type is None."""
        artifact = URLArtifact(
            package="pkg",
            path=Path("/tmp/pkg.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            original_url="https://example.com/pkg.tar.gz",
            url_with_hash="https://example.com/pkg.tar.gz#cachito_hash=sha256:abc",
        )

        assert artifact.package_type is None
