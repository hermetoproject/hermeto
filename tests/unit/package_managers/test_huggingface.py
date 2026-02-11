"""Unit tests for the Hugging Face package manager."""

from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import HuggingfacePackageInput
from hermeto.core.package_managers.huggingface.main import (
    DEFAULT_LOCKFILE_NAME,
    _check_unsafe_patterns,
    _load_lockfile,
    fetch_huggingface_source,
)
from hermeto.core.package_managers.huggingface.models import HuggingFaceLockfile, HuggingFaceModel
from hermeto.core.rooted_path import RootedPath

# Test lockfile content
LOCKFILE_VALID = """
metadata:
  version: '1.0'
models:
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    type: "model"
  - repository: "microsoft/deberta-v3-base"
    revision: "559062ad13d311b87b2c455e67dcd5f1c8f65111"
    type: "model"
    include_patterns:
      - "*.safetensors"
      - "config.json"
"""

LOCKFILE_WITH_DATASET = """
metadata:
  version: '1.0'
models:
  - repository: "squad"
    revision: "d6ec3ceb99ca480ce37cdd35555d6cb2511d223b"
    type: "dataset"
"""

LOCKFILE_WRONG_VERSION = """
metadata:
  version: '2.0'
models:
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
"""

LOCKFILE_INVALID_REVISION = """
metadata:
  version: '1.0'
models:
  - repository: "gpt2"
    revision: "invalid-revision"
    type: "model"
"""

LOCKFILE_INVALID_REPO_NAME = """
metadata:
  version: '1.0'
models:
  - repository: "namespace/too/many/parts"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
"""

LOCKFILE_MISSING_REVISION = """
metadata:
  version: '1.0'
models:
  - repository: "gpt2"
    type: "model"
"""

LOCKFILE_INVALID_YAML = """
metadata:
  version: '1.0'
models:
  - repository: "gpt2
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
"""


class TestHuggingFaceModel:
    """Tests for HuggingFaceModel Pydantic model."""

    def test_valid_model_minimal(self) -> None:
        """Test minimal valid model entry."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
        )
        assert model.repository == "gpt2"
        assert model.type == "model"
        assert model.include_patterns is None

    def test_valid_model_with_namespace(self) -> None:
        """Test model with namespace."""
        model = HuggingFaceModel(
            repository="microsoft/deberta-v3-base",
            revision="559062ad13d311b87b2c455e67dcd5f1c8f65111",
        )
        assert model.namespace == "microsoft"
        assert model.name == "deberta-v3-base"
        assert model.purl_namespace == "microsoft"

    def test_valid_model_without_namespace(self) -> None:
        """Test model without namespace."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
        )
        assert model.namespace == ""
        assert model.name == "gpt2"
        assert model.purl_namespace is None

    def test_valid_model_with_patterns(self) -> None:
        """Test model with include patterns."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            include_patterns=["*.safetensors", "config.json"],
        )
        assert model.include_patterns == ["*.safetensors", "config.json"]

    def test_invalid_revision_short(self) -> None:
        """Test that short revision is rejected."""
        with pytest.raises(ValidationError, match="40-character Git commit hash"):
            HuggingFaceModel(
                repository="gpt2",
                revision="abc123",
            )

    def test_invalid_revision_format(self) -> None:
        """Test that invalid revision format is rejected."""
        with pytest.raises(ValidationError, match="40-character Git commit hash"):
            HuggingFaceModel(
                repository="gpt2",
                revision="not-a-valid-git-hash-but-40-chars-long!",
            )

    def test_invalid_repository_too_many_parts(self) -> None:
        """Test that repository with too many slashes is rejected."""
        with pytest.raises(ValidationError, match="namespace/name"):
            HuggingFaceModel(
                repository="too/many/parts",
                revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            )

    def test_invalid_repository_empty(self) -> None:
        """Test that empty repository is rejected."""
        with pytest.raises(ValidationError):
            HuggingFaceModel(
                repository="",
                revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            )

    def test_dataset_type(self) -> None:
        """Test dataset type."""
        model = HuggingFaceModel(
            repository="squad",
            revision="d6ec3ceb99ca480ce37cdd35555d6cb2511d223b",
            type="dataset",
        )
        assert model.type == "dataset"

    def test_sbom_component_generation(self) -> None:
        """Test SBOM component generation."""
        model = HuggingFaceModel(
            repository="microsoft/deberta-v3-base",
            revision="559062ad13d311b87b2c455e67dcd5f1c8f65111",
        )
        component = model.get_sbom_component("https://huggingface.co/microsoft/deberta-v3-base")

        assert component.name == "microsoft/deberta-v3-base"
        assert component.version == "559062ad13d311b87b2c455e67dcd5f1c8f65111"
        # PURL version should be lowercase
        assert "559062ad13d311b87b2c455e67dcd5f1c8f65111" in component.purl
        assert "pkg:huggingface/microsoft/deberta-v3-base" in component.purl
        assert component.type == "library"

    def test_sbom_component_dataset(self) -> None:
        """Test SBOM component for dataset."""
        model = HuggingFaceModel(
            repository="squad",
            revision="d6ec3ceb99ca480ce37cdd35555d6cb2511d223b",
            type="dataset",
        )
        component = model.get_sbom_component("https://huggingface.co/squad")
        assert component.type == "library"


class TestLockfileLoading:
    """Tests for lockfile loading and validation."""

    def test_load_valid_lockfile(self, tmp_path: Path) -> None:
        """Test loading a valid lockfile."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(LOCKFILE_VALID)

        lockfile = _load_lockfile(lockfile_path)
        assert isinstance(lockfile, HuggingFaceLockfile)
        assert len(lockfile.models) == 2
        assert lockfile.models[0].repository == "gpt2"

    def test_load_lockfile_with_dataset(self, tmp_path: Path) -> None:
        """Test loading lockfile with dataset."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(LOCKFILE_WITH_DATASET)

        lockfile = _load_lockfile(lockfile_path)
        assert lockfile.models[0].type == "dataset"

    def test_load_lockfile_wrong_version(self, tmp_path: Path) -> None:
        """Test that wrong version is rejected."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(LOCKFILE_WRONG_VERSION)

        with pytest.raises(PackageRejected, match="format is not valid"):
            _load_lockfile(lockfile_path)

    def test_load_lockfile_invalid_revision(self, tmp_path: Path) -> None:
        """Test that invalid revision is rejected."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(LOCKFILE_INVALID_REVISION)

        with pytest.raises(PackageRejected, match="format is not valid"):
            _load_lockfile(lockfile_path)

    def test_load_lockfile_invalid_repo(self, tmp_path: Path) -> None:
        """Test that invalid repository name is rejected."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(LOCKFILE_INVALID_REPO_NAME)

        with pytest.raises(PackageRejected, match="format is not valid"):
            _load_lockfile(lockfile_path)

    def test_load_lockfile_missing_revision(self, tmp_path: Path) -> None:
        """Test that missing revision is rejected."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(LOCKFILE_MISSING_REVISION)

        with pytest.raises(PackageRejected, match="format is not valid"):
            _load_lockfile(lockfile_path)

    def test_load_lockfile_invalid_yaml(self, tmp_path: Path) -> None:
        """Test that invalid YAML is rejected."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(LOCKFILE_INVALID_YAML)

        with pytest.raises(PackageRejected, match="invalid YAML format"):
            _load_lockfile(lockfile_path)


class TestUnsafePatternDetection:
    """Tests for unsafe pattern detection and security warnings."""

    def test_no_patterns_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that missing include_patterns triggers warning."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            # No include_patterns specified
        )
        with caplog.at_level("WARNING"):
            _check_unsafe_patterns(model)

        assert "Security warning" in caplog.text
        assert "no include_patterns specified" in caplog.text
        assert "*.bin, *.pt, *.pkl" in caplog.text
        assert "not during Hermeto's fetch" in caplog.text

    def test_safe_patterns_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that safe patterns don't trigger warning."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            include_patterns=["*.safetensors", "config.json", "tokenizer.json"],
        )
        with caplog.at_level("WARNING"):
            _check_unsafe_patterns(model)

        assert "Security warning" not in caplog.text

    def test_unsafe_pattern_bin_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that *.bin pattern triggers warning."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            include_patterns=["*.bin", "config.json"],
        )
        with caplog.at_level("WARNING"):
            _check_unsafe_patterns(model)

        assert "Security warning" in caplog.text
        assert "*.bin" in caplog.text
        assert "pickle serialization" in caplog.text
        assert "not during Hermeto's fetch" in caplog.text

    def test_unsafe_pattern_pt_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that *.pt pattern triggers warning."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            include_patterns=["*.pt"],
        )
        with caplog.at_level("WARNING"):
            _check_unsafe_patterns(model)

        assert "Security warning" in caplog.text
        assert "*.pt" in caplog.text

    def test_unsafe_pattern_modeling_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that modeling_*.py pattern triggers warning."""
        model = HuggingFaceModel(
            repository="custom/model",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            include_patterns=["modeling_*.py", "config.json"],
        )
        with caplog.at_level("WARNING"):
            _check_unsafe_patterns(model)

        assert "Security warning" in caplog.text
        assert "modeling_*.py" in caplog.text

    def test_multiple_unsafe_patterns_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that multiple unsafe patterns all get reported."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            include_patterns=["*.bin", "*.pt", "config.json"],
        )
        with caplog.at_level("WARNING"):
            _check_unsafe_patterns(model)

        assert "Security warning" in caplog.text
        # Should mention at least one unsafe pattern
        assert "*.bin" in caplog.text or "*.pt" in caplog.text


class TestFetchHuggingfaceSource:
    """Tests for the main fetch_huggingface_source function."""

    @mock.patch("hermeto.core.package_managers.huggingface.main._resolve_huggingface_lockfile")
    def test_fetch_with_default_lockfile(self, mock_resolve: mock.Mock, tmp_path: Path) -> None:
        """Test fetch with default lockfile location."""
        mock_request = mock.Mock()
        mock_package = HuggingfacePackageInput.model_construct(
            type="x-huggingface",
            path=Path("."),
            lockfile=None,
        )
        mock_request.huggingface_packages = [mock_package]
        mock_request.source_dir = RootedPath(tmp_path)
        mock_request.output_dir = RootedPath(tmp_path / "output")

        # Create default lockfile
        (tmp_path / DEFAULT_LOCKFILE_NAME).write_text(LOCKFILE_VALID)

        mock_resolve.return_value = []

        fetch_huggingface_source(mock_request)

        mock_resolve.assert_called_once()
        called_lockfile = mock_resolve.call_args[0][0]
        assert called_lockfile.name == DEFAULT_LOCKFILE_NAME

    @mock.patch("hermeto.core.package_managers.huggingface.main._resolve_huggingface_lockfile")
    def test_fetch_with_custom_lockfile(self, mock_resolve: mock.Mock, tmp_path: Path) -> None:
        """Test fetch with custom lockfile path."""
        custom_lockfile = tmp_path / "custom.yaml"
        custom_lockfile.write_text(LOCKFILE_VALID)

        mock_request = mock.Mock()
        mock_package = HuggingfacePackageInput.model_construct(
            type="x-huggingface",
            path=Path("."),
            lockfile=custom_lockfile,
        )
        mock_request.huggingface_packages = [mock_package]
        mock_request.source_dir = RootedPath(tmp_path)
        mock_request.output_dir = RootedPath(tmp_path / "output")

        mock_resolve.return_value = []

        fetch_huggingface_source(mock_request)

        mock_resolve.assert_called_once()
        called_lockfile = mock_resolve.call_args[0][0]
        assert called_lockfile == custom_lockfile

    def test_fetch_relative_lockfile_path(self, tmp_path: Path) -> None:
        """Test that relative lockfile path is rejected."""
        mock_request = mock.Mock()
        mock_package = HuggingfacePackageInput.model_construct(
            type="x-huggingface",
            path=Path("."),
            lockfile=Path("relative.yaml"),
        )
        mock_request.huggingface_packages = [mock_package]
        mock_request.source_dir = RootedPath(tmp_path)

        with pytest.raises(PackageRejected, match="not absolute"):
            fetch_huggingface_source(mock_request)

    def test_fetch_lockfile_not_found(self, tmp_path: Path) -> None:
        """Test that missing lockfile is detected."""
        mock_request = mock.Mock()
        mock_package = HuggingfacePackageInput.model_construct(
            type="x-huggingface",
            path=Path("."),
            lockfile=None,
        )
        mock_request.huggingface_packages = [mock_package]
        mock_request.source_dir = RootedPath(tmp_path)
        mock_request.output_dir = RootedPath(tmp_path / "output")

        # Don't create lockfile

        with pytest.raises(PackageRejected, match="does not exist"):
            fetch_huggingface_source(mock_request)

    @mock.patch.dict("os.environ", {"HF_HUB_OFFLINE": "1"})
    def test_fetch_with_offline_mode_raises_error(self, tmp_path: Path) -> None:
        """Test that HF_HUB_OFFLINE=1 is rejected."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(LOCKFILE_VALID)

        mock_request = mock.Mock()
        mock_package = HuggingfacePackageInput.model_construct(
            type="x-huggingface",
            path=Path("."),
            lockfile=None,
        )
        mock_request.huggingface_packages = [mock_package]
        mock_request.source_dir = RootedPath(tmp_path)
        mock_request.output_dir = RootedPath(tmp_path / "output")

        with pytest.raises(PackageRejected, match="cannot fetch Hugging Face dependencies in offline mode"):
            fetch_huggingface_source(mock_request)
