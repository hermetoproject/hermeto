"""Tests for pip package manager data models."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from hermeto.core.package_managers.pip.models import (
    ArtifactDownload,
    PyPIArtifact,
    URLArtifact,
    VCSArtifact,
)


class TestPyPIArtifact:
    """Test PyPIArtifact dataclass."""

    def test_creation(self) -> None:
        """Test basic PyPIArtifact creation."""
        artifact = PyPIArtifact(
            package="numpy",
            path=Path("/tmp/numpy-1.21.0.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            index_url="https://pypi.org/simple/",
            package_type="sdist",
            version="1.21.0",
        )

        assert artifact.package == "numpy"
        assert artifact.path == Path("/tmp/numpy-1.21.0.tar.gz")
        assert artifact.requirement_file == "requirements.txt"
        assert artifact.missing_req_file_checksum is False
        assert artifact.build_dependency is False
        assert artifact.index_url == "https://pypi.org/simple/"
        assert artifact.package_type == "sdist"
        assert artifact.version == "1.21.0"
        assert artifact.kind == "pypi"

    def test_wheel_artifact(self) -> None:
        """Test PyPIArtifact with wheel package type."""
        artifact = PyPIArtifact(
            package="requests",
            path=Path("/tmp/requests-2.25.1-py2.py3-none-any.whl"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=True,
            build_dependency=True,
            index_url="https://pypi.org/simple/",
            package_type="wheel",
            version="2.25.1",
        )

        assert artifact.package_type == "wheel"
        assert artifact.build_dependency is True
        assert artifact.kind == "pypi"


class TestVCSArtifact:
    """Test VCSArtifact dataclass."""

    def test_creation(self) -> None:
        """Test basic VCSArtifact creation."""
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

        assert artifact.package == "myproject"
        assert artifact.url == "https://github.com/user/myproject.git"
        assert artifact.host == "github.com"
        assert artifact.namespace == "user"
        assert artifact.repo == "myproject"
        assert artifact.ref == "abc123"
        assert artifact.kind == "vcs"

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

    def test_creation(self) -> None:
        """Test basic URLArtifact creation."""
        artifact = URLArtifact(
            package="custom-package",
            path=Path("/tmp/custom-package-abc123.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            original_url="https://example.com/package.tar.gz",
            url_with_hash="https://example.com/package.tar.gz#cachito_hash=sha256:abc123",
        )

        assert artifact.package == "custom-package"
        assert artifact.original_url == "https://example.com/package.tar.gz"
        assert (
            artifact.url_with_hash
            == "https://example.com/package.tar.gz#cachito_hash=sha256:abc123"
        )
        assert artifact.kind == "url"

    def test_version_property(self) -> None:
        """Test that URL version property returns url_with_hash."""
        artifact = URLArtifact(
            package="custom-package",
            path=Path("/tmp/custom-package-abc123.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            original_url="https://example.com/package.tar.gz",
            url_with_hash="https://example.com/package.tar.gz#cachito_hash=sha256:abc123",
        )

        assert artifact.version == "https://example.com/package.tar.gz#cachito_hash=sha256:abc123"


class TestArtifactDownloadUnion:
    """Test the ArtifactDownload union type."""

    def test_union_type_accepts_all_artifacts(self) -> None:
        """Test that ArtifactDownload union accepts all artifact types."""
        pypi_artifact = PyPIArtifact(
            package="numpy",
            path=Path("/tmp/numpy-1.21.0.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            index_url="https://pypi.org/simple/",
            package_type="sdist",
            version="1.21.0",
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
        artifacts: list[ArtifactDownload] = [pypi_artifact, vcs_artifact, url_artifact]

        assert len(artifacts) == 3
        assert isinstance(artifacts[0], PyPIArtifact)
        assert isinstance(artifacts[1], VCSArtifact)
        assert isinstance(artifacts[2], URLArtifact)


class TestArtifactCommonInterface:
    """Test the common interface shared by all artifact types."""

    @pytest.fixture
    def artifacts(self) -> list[ArtifactDownload]:
        """Create sample artifacts of each type."""
        return [
            PyPIArtifact(
                package="numpy",
                path=Path("/tmp/numpy-1.21.0.tar.gz"),
                requirement_file="requirements.txt",
                missing_req_file_checksum=False,
                build_dependency=False,
                index_url="https://pypi.org/simple/",
                package_type="sdist",
                version="1.21.0",
            ),
            VCSArtifact(
                package="myproject",
                path=Path("/tmp/myproject-gitcommit-abc123.tar.gz"),
                requirement_file="requirements.txt",
                missing_req_file_checksum=True,
                build_dependency=True,
                url="https://github.com/user/myproject.git",
                host="github.com",
                namespace="user",
                repo="myproject",
                ref="abc123",
            ),
            URLArtifact(
                package="custom-package",
                path=Path("/tmp/custom-package-abc123.tar.gz"),
                requirement_file="requirements.txt",
                missing_req_file_checksum=False,
                build_dependency=False,
                original_url="https://example.com/package.tar.gz",
                url_with_hash="https://example.com/package.tar.gz#cachito_hash=sha256:abc123",
            ),
        ]

    def test_all_artifacts_have_common_fields(self, artifacts: list[ArtifactDownload]) -> None:
        """Test that all artifacts have the common fields from CommonArtifact."""
        for artifact in artifacts:
            assert hasattr(artifact, "package")
            assert hasattr(artifact, "path")
            assert hasattr(artifact, "requirement_file")
            assert hasattr(artifact, "missing_req_file_checksum")
            assert hasattr(artifact, "build_dependency")
            assert hasattr(artifact, "kind")

    def test_kind_property_returns_correct_values(self, artifacts: list[ArtifactDownload]) -> None:
        """Test that kind property returns the correct values for each type."""
        pypi_artifact, vcs_artifact, url_artifact = artifacts

        assert pypi_artifact.kind == "pypi"
        assert vcs_artifact.kind == "vcs"
        assert url_artifact.kind == "url"

    def test_build_dependency_values(self, artifacts: list[ArtifactDownload]) -> None:
        """Test build_dependency field values."""
        pypi_artifact, vcs_artifact, url_artifact = artifacts

        assert pypi_artifact.build_dependency is False
        assert vcs_artifact.build_dependency is True
        assert url_artifact.build_dependency is False

    def test_to_filter_dict(self, artifacts: list[ArtifactDownload]) -> None:
        """Test to_filter_dict returns correct keys for filter_packages_with_rust_code."""
        pypi_artifact, vcs_artifact, url_artifact = artifacts

        d = pypi_artifact.to_filter_dict()
        assert d == {
            "package": "numpy",
            "path": Path("/tmp/numpy-1.21.0.tar.gz"),
            "kind": "pypi",
        }

        d = vcs_artifact.to_filter_dict()
        assert d["kind"] == "vcs"

        d = url_artifact.to_filter_dict()
        assert d["kind"] == "url"


class TestFrozenDataclasses:
    """Test that dataclasses are immutable."""

    def test_pypi_artifact_is_frozen(self) -> None:
        """Test that PyPIArtifact cannot be mutated."""
        artifact = PyPIArtifact(
            package="numpy",
            path=Path("/tmp/numpy-1.21.0.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            index_url="https://pypi.org/simple/",
            package_type="sdist",
            version="1.21.0",
        )
        with pytest.raises(FrozenInstanceError):
            artifact.build_dependency = True  # type: ignore[misc]

    def test_replace_creates_new_instance(self) -> None:
        """Test that dataclasses.replace works for marking build dependencies."""
        original = PyPIArtifact(
            package="numpy",
            path=Path("/tmp/numpy-1.21.0.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            index_url="https://pypi.org/simple/",
            package_type="sdist",
            version="1.21.0",
        )
        marked = replace(original, build_dependency=True)

        assert original.build_dependency is False
        assert marked.build_dependency is True
        assert marked.package == original.package


class TestVCSArtifactValidation:
    """Test __post_init__ validation on VCSArtifact."""

    def test_empty_url_raises(self) -> None:
        """Test that empty url raises ValueError."""
        with pytest.raises(ValueError, match="non-empty url"):
            VCSArtifact(
                package="myproject",
                path=Path("/tmp/myproject.tar.gz"),
                requirement_file="requirements.txt",
                missing_req_file_checksum=True,
                build_dependency=False,
                url="",
                host="github.com",
                namespace="user",
                repo="myproject",
                ref="abc123",
            )

    def test_empty_ref_raises(self) -> None:
        """Test that empty ref raises ValueError."""
        with pytest.raises(ValueError, match="non-empty ref"):
            VCSArtifact(
                package="myproject",
                path=Path("/tmp/myproject.tar.gz"),
                requirement_file="requirements.txt",
                missing_req_file_checksum=True,
                build_dependency=False,
                url="https://github.com/user/myproject.git",
                host="github.com",
                namespace="user",
                repo="myproject",
                ref="",
            )


class TestURLArtifactPackageType:
    """Test URLArtifact package_type field."""

    def test_default_package_type_is_empty(self) -> None:
        """Test that default package_type is empty string."""
        artifact = URLArtifact(
            package="pkg",
            path=Path("/tmp/pkg.tar.gz"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            original_url="https://example.com/pkg.tar.gz",
            url_with_hash="https://example.com/pkg.tar.gz#cachito_hash=sha256:abc",
        )
        assert artifact.package_type == ""

    def test_wheel_package_type(self) -> None:
        """Test that package_type can be set to 'wheel'."""
        artifact = URLArtifact(
            package="pkg",
            path=Path("/tmp/pkg-1.0-py3-none-any.whl"),
            requirement_file="requirements.txt",
            missing_req_file_checksum=False,
            build_dependency=False,
            original_url="https://example.com/pkg-1.0-py3-none-any.whl",
            url_with_hash="https://example.com/pkg-1.0-py3-none-any.whl#cachito_hash=sha256:abc",
            package_type="wheel",
        )
        assert artifact.package_type == "wheel"
