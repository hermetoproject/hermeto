"""Tests for pip package manager data model."""

from pathlib import Path

from hermeto.core.package_managers.pip.models import (
    Artifact,
    PyPIArtifact,
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


class TestArtifactUnion:
    """Test the Artifact union type."""

    def test_union_type_accepts_all_artifacts(self) -> None:
        """Test that Artifact union accepts all artifact types."""
        pypi_artifact = PyPIArtifact(
            package="numpy",
            path=Path("/tmp/numpy-1.21.0.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            index_url="https://pypi.org/simple/",
            package_type="sdist",
            _version="1.21.0",
        )

        vcs_artifact = VCSArtifact(
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

        url_artifact = URLArtifact(
            package="custom-package",
            path=Path("/tmp/custom-package-abc123.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            original_url="https://example.com/package.tar.gz",
            url_with_hash="https://example.com/package.tar.gz#cachito_hash=sha256:abc123",
        )

        # Test that all types are accepted by the union
        artifacts: list[Artifact] = [pypi_artifact, vcs_artifact, url_artifact]

        assert len(artifacts) == 3
