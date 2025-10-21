"""Unit tests for the DVC package manager."""

from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import DVCPackageInput, Mode
from hermeto.core.package_managers.dvc.main import DEFAULT_LOCKFILE_NAME, fetch_dvc_source
from hermeto.core.package_managers.dvc.models import DVCDep, DVCLockfile, DVCOut, DVCStage
from hermeto.core.package_managers.dvc.parser import (
    _create_generic_component,
    _create_huggingface_component,
    _is_huggingface_url,
    _parse_huggingface_url,
    generate_sbom_components,
    load_dvc_lockfile,
    validate_checksums_present,
)
from hermeto.core.rooted_path import RootedPath

# Test lockfile content
LOCKFILE_VALID = """
schema: '2.0'
stages:
  fetch_model:
    cmd: dvc import-url ...
    deps:
    - path: https://huggingface.co/gpt2/resolve/e7da7f221ccf5f2856f4331d34c2d0e82aa2a986/model.safetensors
      md5: abc123
      size: 12345
    outs:
    - path: models/gpt2/model.safetensors
      md5: def456
      size: 67890
"""

LOCKFILE_MULTIPLE_SOURCES = """
schema: '2.0'
stages:
  fetch_hf_model:
    deps:
    - path: https://huggingface.co/microsoft/deberta-v3-base/resolve/559062ad13d311b87b2c455e67dcd5f1c8f65111/pytorch_model.bin
      md5: abc123
  fetch_s3_data:
    deps:
    - path: s3://mybucket/data.csv
      md5: def456
  fetch_http:
    deps:
    - path: https://example.com/file.tar.gz
      md5: ghi789
"""

LOCKFILE_WITH_LOCAL = """
schema: '2.0'
stages:
  process:
    deps:
    - path: local_file.txt
      md5: abc123
    - path: https://example.com/data.csv
      md5: def456
"""

LOCKFILE_MISSING_CHECKSUM = """
schema: '2.0'
stages:
  fetch:
    deps:
    - path: https://example.com/file.tar.gz
"""

LOCKFILE_WRONG_SCHEMA = """
schema: '1.0'
stages:
  fetch:
    deps:
    - path: https://example.com/file.tar.gz
      md5: abc123
"""

LOCKFILE_INVALID_YAML = """
schema: '2.0'
stages:
  fetch
    deps:
"""


class TestDVCModels:
    """Tests for DVC Pydantic models."""

    def test_dvc_dep_external_url(self) -> None:
        """Test external URL detection."""
        dep_http = DVCDep(path="https://example.com/file.txt", md5="abc123")
        assert dep_http.is_external_url

        dep_s3 = DVCDep(path="s3://bucket/file.txt", md5="def456")
        assert dep_s3.is_external_url

        dep_local = DVCDep(path="local_file.txt", md5="ghi789")
        assert not dep_local.is_external_url

    def test_dvc_dep_checksum_properties(self) -> None:
        """Test checksum property accessors."""
        dep = DVCDep(path="https://example.com/file.txt", md5="abc123")
        assert dep.checksum_algorithm == "md5"
        assert dep.checksum_value == "abc123"

    def test_dvc_lockfile_schema_validation(self) -> None:
        """Test DVC schema version validation."""
        valid = DVCLockfile(schema_="2.0", stages={})
        assert valid.schema_ == "2.0"

        with pytest.raises(ValidationError, match="Unsupported DVC schema version"):
            DVCLockfile(schema_="1.0", stages={})

    def test_dvc_lockfile_get_external_deps(self) -> None:
        """Test extraction of external dependencies."""
        lockfile = DVCLockfile(
            schema_="2.0",
            stages={
                "stage1": DVCStage(
                    deps=[
                        DVCDep(path="https://example.com/file.txt", md5="abc123"),
                        DVCDep(path="local.txt", md5="def456"),
                    ]
                ),
                "stage2": DVCStage(
                    deps=[DVCDep(path="s3://bucket/data.csv", md5="ghi789")]
                ),
            },
        )

        external_deps = lockfile.get_all_external_deps()
        assert len(external_deps) == 2
        assert external_deps[0][0] == "stage1"
        assert external_deps[0][1].path == "https://example.com/file.txt"
        assert external_deps[1][0] == "stage2"
        assert external_deps[1][1].path == "s3://bucket/data.csv"


class TestDVCParser:
    """Tests for DVC lockfile parsing."""

    def test_load_valid_lockfile(self, tmp_path: Path) -> None:
        """Test loading a valid lockfile."""
        lockfile_path = tmp_path / "dvc.lock"
        lockfile_path.write_text(LOCKFILE_VALID)

        lockfile = load_dvc_lockfile(lockfile_path)
        assert lockfile.schema_ == "2.0"
        assert "fetch_model" in lockfile.stages
        assert len(lockfile.stages["fetch_model"].deps) == 1

    def test_load_lockfile_not_found(self, tmp_path: Path) -> None:
        """Test error when lockfile doesn't exist."""
        lockfile_path = tmp_path / "nonexistent.lock"

        with pytest.raises(PackageRejected, match="does not exist"):
            load_dvc_lockfile(lockfile_path)

    def test_load_lockfile_invalid_yaml(self, tmp_path: Path) -> None:
        """Test error on invalid YAML."""
        lockfile_path = tmp_path / "dvc.lock"
        lockfile_path.write_text(LOCKFILE_INVALID_YAML)

        with pytest.raises(PackageRejected, match="invalid YAML format"):
            load_dvc_lockfile(lockfile_path)

    def test_load_lockfile_wrong_schema(self, tmp_path: Path) -> None:
        """Test error on unsupported schema version."""
        lockfile_path = tmp_path / "dvc.lock"
        lockfile_path.write_text(LOCKFILE_WRONG_SCHEMA)

        with pytest.raises(PackageRejected, match="format is not valid"):
            load_dvc_lockfile(lockfile_path)

    def test_validate_checksums_strict_mode(self, tmp_path: Path) -> None:
        """Test checksum validation in strict mode."""
        lockfile_path = tmp_path / "dvc.lock"
        lockfile_path.write_text(LOCKFILE_MISSING_CHECKSUM)
        lockfile = load_dvc_lockfile(lockfile_path)

        with pytest.raises(PackageRejected, match="missing checksums"):
            validate_checksums_present(lockfile, strict=True)

    def test_validate_checksums_permissive_mode(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test checksum validation in permissive mode."""
        lockfile_path = tmp_path / "dvc.lock"
        lockfile_path.write_text(LOCKFILE_MISSING_CHECKSUM)
        lockfile = load_dvc_lockfile(lockfile_path)

        validate_checksums_present(lockfile, strict=False)
        assert "Missing checksums" in caplog.text


class TestHuggingFaceURLParsing:
    """Tests for HuggingFace URL parsing."""

    def test_is_huggingface_url(self) -> None:
        """Test HuggingFace URL detection."""
        assert _is_huggingface_url("https://huggingface.co/gpt2/resolve/abc/model.bin")
        assert not _is_huggingface_url("https://example.com/file.txt")
        assert not _is_huggingface_url("s3://bucket/file.txt")

    def test_parse_huggingface_url_with_namespace(self) -> None:
        """Test parsing HF URL with namespace."""
        url = "https://huggingface.co/microsoft/deberta-v3-base/resolve/559062ad13d311b87b2c455e67dcd5f1c8f65111/model.bin"
        repo_id, revision, file_path = _parse_huggingface_url(url)

        assert repo_id == "microsoft/deberta-v3-base"
        assert revision == "559062ad13d311b87b2c455e67dcd5f1c8f65111"
        assert file_path == "model.bin"

    def test_parse_huggingface_url_without_namespace(self) -> None:
        """Test parsing HF URL without namespace."""
        url = "https://huggingface.co/gpt2/resolve/e7da7f221ccf5f2856f4331d34c2d0e82aa2a986/model.safetensors"
        repo_id, revision, file_path = _parse_huggingface_url(url)

        assert repo_id == "gpt2"
        assert revision == "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
        assert file_path == "model.safetensors"

    def test_parse_invalid_huggingface_url(self) -> None:
        """Test parsing invalid HF URL."""
        url = "https://huggingface.co/invalid/format"
        repo_id, revision, file_path = _parse_huggingface_url(url)

        assert repo_id is None
        assert revision is None
        assert file_path is None


class TestSBOMGeneration:
    """Tests for SBOM component generation."""

    def test_create_huggingface_component(self) -> None:
        """Test creating HF component."""
        dep = DVCDep(
            path="https://huggingface.co/gpt2/resolve/e7da7f221ccf5f2856f4331d34c2d0e82aa2a986/model.bin",
            md5="abc123",
        )
        component = _create_huggingface_component("gpt2", [("stage1", dep)])

        assert component.name == "gpt2"
        assert component.version == "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
        assert "pkg:huggingface/gpt2" in component.purl
        assert component.type == "library"

    def test_create_huggingface_component_with_namespace(self) -> None:
        """Test creating HF component with namespace."""
        dep = DVCDep(
            path="https://huggingface.co/microsoft/deberta/resolve/abc123/model.bin",
            md5="def456",
        )
        component = _create_huggingface_component("microsoft/deberta", [("stage1", dep)])

        assert component.name == "microsoft/deberta"
        assert "pkg:huggingface/microsoft/deberta" in component.purl

    def test_create_generic_component(self) -> None:
        """Test creating generic component."""
        dep = DVCDep(path="https://example.com/data/file.tar.gz", md5="abc123")
        component = _create_generic_component("fetch_stage", dep)

        assert component.name == "file.tar.gz"
        assert component.version == "abc123"[:8]
        assert "pkg:generic/file.tar.gz" in component.purl
        assert "checksum=md5:abc123" in component.purl
        assert component.type == "library"

    def test_generate_sbom_components_mixed_sources(self, tmp_path: Path) -> None:
        """Test SBOM generation with mixed source types."""
        lockfile_path = tmp_path / "dvc.lock"
        lockfile_path.write_text(LOCKFILE_MULTIPLE_SOURCES)
        lockfile = load_dvc_lockfile(lockfile_path)

        components = generate_sbom_components(lockfile)

        # Should have 3 components: 1 HF, 1 S3, 1 HTTP
        assert len(components) == 3

        # Find HF component
        hf_component = next(c for c in components if "huggingface" in c.purl)
        assert hf_component.name == "microsoft/deberta-v3-base"

        # Other two should be generic
        generic_components = [c for c in components if "generic" in c.purl]
        assert len(generic_components) == 2

    def test_generate_sbom_skips_local_deps(self, tmp_path: Path) -> None:
        """Test that local dependencies are skipped."""
        lockfile_path = tmp_path / "dvc.lock"
        lockfile_path.write_text(LOCKFILE_WITH_LOCAL)
        lockfile = load_dvc_lockfile(lockfile_path)

        components = generate_sbom_components(lockfile)

        # Should only have 1 component (the HTTP one, not the local file)
        assert len(components) == 1
        assert "example.com" in components[0].purl


class TestFetchDVCSource:
    """Tests for main fetch logic."""

    @mock.patch("hermeto.core.package_managers.dvc.main._run_dvc_fetch")
    @mock.patch("hermeto.core.package_managers.dvc.main.load_dvc_lockfile")
    def test_fetch_dvc_source(
        self,
        mock_load: mock.Mock,
        mock_run_dvc: mock.Mock,
        tmp_path: Path,
    ) -> None:
        """Test basic fetch flow."""
        # Setup
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        lockfile_path = source_dir / "dvc.lock"
        lockfile_path.write_text(LOCKFILE_VALID)

        mock_lockfile = DVCLockfile(schema_="2.0", stages={})
        mock_load.return_value = mock_lockfile

        mock_request = mock.Mock()
        mock_request.dvc_packages = [DVCPackageInput(type="x-dvc")]
        mock_request.source_dir = RootedPath(source_dir)
        mock_request.output_dir = RootedPath(output_dir)
        mock_request.mode = Mode.STRICT

        # Execute
        result = fetch_dvc_source(mock_request)

        # Verify
        mock_load.assert_called_once()
        mock_run_dvc.assert_called_once()
        assert len(result.build_config.environment_variables) == 1
        assert result.build_config.environment_variables[0].name == "DVC_CACHE_DIR"

    @mock.patch("hermeto.core.package_managers.dvc.main.load_dvc_lockfile")
    def test_fetch_dvc_source_missing_lockfile(
        self,
        mock_load: mock.Mock,
        tmp_path: Path,
    ) -> None:
        """Test error when lockfile is missing."""
        mock_load.side_effect = PackageRejected("dvc.lock not found", solution="Create lockfile")

        source_dir = tmp_path / "source"
        source_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        mock_request = mock.Mock()
        mock_request.dvc_packages = [DVCPackageInput(type="x-dvc")]
        mock_request.source_dir = RootedPath(source_dir)
        mock_request.output_dir = RootedPath(output_dir)
        mock_request.mode = Mode.STRICT

        with pytest.raises(PackageRejected, match="dvc.lock not found"):
            fetch_dvc_source(mock_request)
