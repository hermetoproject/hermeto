"""Unit tests for the Hugging Face package manager."""

from collections.abc import Callable
from pathlib import Path
from unittest import mock

import pytest
from huggingface_hub.utils import HfHubHTTPError
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


# Fixtures


@pytest.fixture
def cache_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create and return cache directories for tests."""
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    datasets_cache = tmp_path / "datasets"
    datasets_cache.mkdir()
    return cache_root, datasets_cache


@pytest.fixture
def snapshot_path_factory(tmp_path: Path) -> Callable[[str, str, str], Path]:
    """Factory fixture to create snapshot paths for different repositories."""

    def _create_snapshot_path(repo_id: str, revision: str, repo_type: str = "model") -> Path:
        """Create a snapshot directory structure."""
        repo_name = repo_id.replace("/", "--")
        prefix = f"{repo_type}s" if repo_type == "dataset" else "models"
        path = tmp_path / "hub" / f"{prefix}--{repo_name}" / "snapshots" / revision
        path.mkdir(parents=True)
        return path

    return _create_snapshot_path


# Test classes for model validation


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

    @pytest.mark.parametrize(
        "repository,expected_namespace,expected_name",
        [
            ("microsoft/deberta-v3-base", "microsoft", "deberta-v3-base"),
            ("gpt2", "", "gpt2"),
        ],
    )
    def test_namespace_parsing(
        self, repository: str, expected_namespace: str, expected_name: str
    ) -> None:
        """Test namespace and name extraction from repository."""
        model = HuggingFaceModel(
            repository=repository,
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
        )
        assert model.namespace == expected_namespace
        assert model.name == expected_name
        assert model.purl_namespace == (expected_namespace if expected_namespace else None)

    def test_valid_model_with_patterns(self) -> None:
        """Test model with include patterns."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            include_patterns=["*.safetensors", "config.json"],
        )
        assert model.include_patterns == ["*.safetensors", "config.json"]

    @pytest.mark.parametrize(
        "revision,error_match",
        [
            ("abc123", "40-character Git commit hash"),
            ("not-a-valid-git-hash-but-40-chars-long!", "40-character Git commit hash"),
        ],
    )
    def test_invalid_revision(self, revision: str, error_match: str) -> None:
        """Test that invalid revisions are rejected."""
        with pytest.raises(ValidationError, match=error_match):
            HuggingFaceModel(repository="gpt2", revision=revision)

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

    @pytest.mark.parametrize(
        "repository,repo_type",
        [
            ("microsoft/deberta-v3-base", "model"),
            ("squad", "dataset"),
        ],
    )
    def test_sbom_component_generation(self, repository: str, repo_type: str) -> None:
        """Test SBOM component generation."""
        model = HuggingFaceModel(
            repository=repository,
            revision="559062ad13d311b87b2c455e67dcd5f1c8f65111",
            type=repo_type,
        )
        component = model.get_sbom_component(f"https://huggingface.co/{repository}")

        assert component.name == repository
        assert component.version == "559062ad13d311b87b2c455e67dcd5f1c8f65111"
        assert "559062ad13d311b87b2c455e67dcd5f1c8f65111" in component.purl
        assert "pkg:huggingface" in component.purl
        assert component.type == "library"


class TestLockfileLoading:
    """Tests for lockfile loading and validation."""

    @pytest.mark.parametrize(
        "lockfile_content,expected_models",
        [
            (LOCKFILE_VALID, 2),
            (LOCKFILE_WITH_DATASET, 1),
        ],
    )
    def test_load_valid_lockfile(
        self, tmp_path: Path, lockfile_content: str, expected_models: int
    ) -> None:
        """Test loading valid lockfiles."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(lockfile_content)

        lockfile = _load_lockfile(lockfile_path)
        assert isinstance(lockfile, HuggingFaceLockfile)
        assert len(lockfile.models) == expected_models

    @pytest.mark.parametrize(
        "lockfile_content,error_match",
        [
            (LOCKFILE_WRONG_VERSION, "format is not valid"),
            (LOCKFILE_INVALID_REVISION, "format is not valid"),
            (LOCKFILE_INVALID_REPO_NAME, "format is not valid"),
            (LOCKFILE_MISSING_REVISION, "format is not valid"),
            (LOCKFILE_INVALID_YAML, "invalid YAML format"),
        ],
    )
    def test_load_invalid_lockfile(
        self, tmp_path: Path, lockfile_content: str, error_match: str
    ) -> None:
        """Test that invalid lockfiles are rejected."""
        lockfile_path = tmp_path / "huggingface.lock.yaml"
        lockfile_path.write_text(lockfile_content)

        with pytest.raises(PackageRejected, match=error_match):
            _load_lockfile(lockfile_path)


class TestUnsafePatternDetection:
    """Tests for unsafe pattern detection and security warnings."""

    def test_no_patterns_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that missing include_patterns triggers warning."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
        )
        with caplog.at_level("WARNING"):
            _check_unsafe_patterns(model)

        assert "Security warning" in caplog.text
        assert "no include_patterns specified" in caplog.text
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

    @pytest.mark.parametrize(
        "unsafe_pattern",
        ["*.bin", "*.pt", "*.pkl", "modeling_*.py"],
    )
    def test_unsafe_patterns_warn(
        self, unsafe_pattern: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that unsafe patterns trigger warnings."""
        model = HuggingFaceModel(
            repository="gpt2",
            revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
            include_patterns=[unsafe_pattern, "config.json"],
        )
        with caplog.at_level("WARNING"):
            _check_unsafe_patterns(model)

        assert "Security warning" in caplog.text
        assert unsafe_pattern in caplog.text
        assert "not during Hermeto's fetch" in caplog.text


# Standalone test functions (following npm/gomod pattern)


@mock.patch.dict("os.environ", {"HF_HUB_OFFLINE": "1"})
def test_fetch_with_offline_mode_raises_error(tmp_path: Path) -> None:
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


@mock.patch("hermeto.core.package_managers.huggingface.main._resolve_huggingface_lockfile")
def test_fetch_with_default_lockfile(mock_resolve: mock.Mock, tmp_path: Path) -> None:
    """Test fetch with default lockfile location."""
    (tmp_path / DEFAULT_LOCKFILE_NAME).write_text(LOCKFILE_VALID)

    mock_request = mock.Mock()
    mock_package = HuggingfacePackageInput.model_construct(
        type="x-huggingface",
        path=Path("."),
        lockfile=None,
    )
    mock_request.huggingface_packages = [mock_package]
    mock_request.source_dir = RootedPath(tmp_path)
    mock_request.output_dir = RootedPath(tmp_path / "output")
    mock_resolve.return_value = []

    fetch_huggingface_source(mock_request)
    mock_resolve.assert_called_once()


def test_lockfile_not_found(tmp_path: Path) -> None:
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

    # Don't create lockfile - should raise error
    with pytest.raises(PackageRejected, match="does not exist"):
        fetch_huggingface_source(mock_request)


@mock.patch("hermeto.core.package_managers.huggingface.main._resolve_huggingface_lockfile")
def test_fetch_with_custom_lockfile(mock_resolve: mock.Mock, tmp_path: Path) -> None:
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

    called_lockfile = mock_resolve.call_args[0][0]
    assert called_lockfile == custom_lockfile


def test_fetch_relative_lockfile_path_rejected(tmp_path: Path) -> None:
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


@pytest.mark.parametrize(
    "model_data,allow_patterns,should_call_dataset_loader",
    [
        pytest.param(
            {
                "repository": "gpt2",
                "revision": "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
                "type": "model",
                "include_patterns": None,
            },
            None,
            False,
            id="model_no_patterns",
        ),
        pytest.param(
            {
                "repository": "microsoft/deberta-v3-base",
                "revision": "559062ad13d311b87b2c455e67dcd5f1c8f65111",
                "type": "model",
                "include_patterns": ["*.safetensors", "config.json"],
            },
            ["*.safetensors", "config.json"],
            False,
            id="model_with_patterns",
        ),
        pytest.param(
            {
                "repository": "squad",
                "revision": "d6ec3ceb99ca480ce37cdd35555d6cb2511d223b",
                "type": "dataset",
                "include_patterns": None,
            },
            None,
            True,
            id="dataset",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.huggingface.main._load_dataset_to_cache")
@mock.patch("hermeto.core.package_managers.huggingface.main.snapshot_download")
def test_fetch_model(
    mock_snapshot: mock.Mock,
    mock_load_dataset: mock.Mock,
    snapshot_path_factory: Callable[[str, str, str], Path],
    cache_dirs: tuple[Path, Path],
    model_data: dict[str, str | list[str] | None],
    allow_patterns: list[str] | None,
    should_call_dataset_loader: bool,
) -> None:
    """Test _fetch_model with various model configurations."""
    from hermeto.core.package_managers.huggingface.main import _fetch_model

    cache_root, datasets_cache = cache_dirs

    # Create snapshot path
    snapshot_path = snapshot_path_factory(
        model_data["repository"], model_data["revision"], model_data["type"]
    )
    mock_snapshot.return_value = str(snapshot_path)

    model_entry = HuggingFaceModel(**model_data)

    component = _fetch_model(model_entry, cache_root, datasets_cache)

    # Verify snapshot_download was called correctly
    mock_snapshot.assert_called_once_with(
        repo_id=model_data["repository"],
        revision=model_data["revision"],
        repo_type=model_data["type"],
        cache_dir=cache_root,
        allow_patterns=allow_patterns,
    )

    # Verify component properties
    assert component.name == model_data["repository"]
    assert component.version == model_data["revision"]
    assert "pkg:huggingface" in component.purl

    # Verify ref file was created
    repo_name = model_data["repository"].replace("/", "--")
    prefix = "datasets" if model_data["type"] == "dataset" else "models"
    ref_file = snapshot_path.parent.parent / "refs" / "main"
    assert ref_file.exists()
    assert ref_file.read_text() == model_data["revision"]

    # Verify dataset loader was called if appropriate
    if should_call_dataset_loader:
        mock_load_dataset.assert_called_once_with(
            model_data["repository"], model_data["revision"], datasets_cache
        )
    else:
        mock_load_dataset.assert_not_called()


@mock.patch("hermeto.core.package_managers.huggingface.main.snapshot_download")
def test_fetch_model_404_error(
    mock_snapshot: mock.Mock,
    cache_dirs: tuple[Path, Path],
) -> None:
    """Test _fetch_model with 404 error."""
    from hermeto.core.package_managers.huggingface.main import _fetch_model

    cache_root, datasets_cache = cache_dirs

    # Create mock 404 response
    mock_response = mock.Mock()
    mock_response.status_code = 404
    http_error = HfHubHTTPError("Not found", response=mock_response)
    mock_snapshot.side_effect = http_error

    model_entry = HuggingFaceModel(
        repository="nonexistent/model",
        revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
        type="model",
    )

    with pytest.raises(PackageRejected, match="Repository 'nonexistent/model' not found"):
        _fetch_model(model_entry, cache_root, datasets_cache)


@mock.patch("hermeto.core.package_managers.huggingface.main.snapshot_download")
def test_fetch_model_generic_error(
    mock_snapshot: mock.Mock,
    cache_dirs: tuple[Path, Path],
) -> None:
    """Test _fetch_model with generic error."""
    from hermeto.core.package_managers.huggingface.main import _fetch_model

    cache_root, datasets_cache = cache_dirs

    mock_snapshot.side_effect = RuntimeError("Network error")

    model_entry = HuggingFaceModel(
        repository="nonexistent/model",
        revision="e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
        type="model",
    )

    with pytest.raises(PackageRejected, match="Failed to fetch repository 'nonexistent/model'"):
        _fetch_model(model_entry, cache_root, datasets_cache)


@pytest.mark.parametrize(
    "dataset_return",
    [
        pytest.param({"train": mock.Mock(), "test": mock.Mock()}, id="with_splits"),
        pytest.param(mock.Mock(__class__=type("Dataset", (), {})), id="no_splits"),
    ],
)
@mock.patch("hermeto.core.package_managers.huggingface.main.load_dataset")
def test_load_dataset_to_cache_success(
    mock_load_dataset: mock.Mock,
    cache_dirs: tuple[Path, Path],
    dataset_return: mock.Mock | dict[str, mock.Mock],
) -> None:
    """Test successful dataset loading with and without splits."""
    from hermeto.core.package_managers.huggingface.main import _load_dataset_to_cache

    _, datasets_cache = cache_dirs
    mock_load_dataset.return_value = dataset_return

    # Should complete without error regardless of dataset type
    _load_dataset_to_cache("squad", "d6ec3ceb99ca480ce37cdd35555d6cb2511d223b", datasets_cache)

    mock_load_dataset.assert_called_once_with(
        "squad",
        revision="d6ec3ceb99ca480ce37cdd35555d6cb2511d223b",
        cache_dir=datasets_cache,
        trust_remote_code=False,
    )


@mock.patch("hermeto.core.package_managers.huggingface.main.load_dataset")
def test_load_dataset_to_cache_error(
    mock_load_dataset: mock.Mock,
    cache_dirs: tuple[Path, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test dataset loading when load_dataset raises an error."""
    from hermeto.core.package_managers.huggingface.main import _load_dataset_to_cache

    _, datasets_cache = cache_dirs
    mock_load_dataset.side_effect = RuntimeError("Failed to load dataset")

    with caplog.at_level("WARNING"):
        _load_dataset_to_cache("squad", "d6ec3ceb99ca480ce37cdd35555d6cb2511d223b", datasets_cache)

    assert "Failed to load dataset 'squad'" in caplog.text
    assert "may not work in offline mode" in caplog.text


@mock.patch("hermeto.core.package_managers.huggingface.main._fetch_model")
def test_resolve_lockfile_integration(mock_fetch: mock.Mock, tmp_path: Path) -> None:
    """Test full lockfile resolution flow."""
    from hermeto.core.package_managers.huggingface.main import _resolve_huggingface_lockfile

    # Create lockfile
    lockfile_path = tmp_path / "huggingface.lock.yaml"
    lockfile_path.write_text(LOCKFILE_VALID)

    # Mock component returns
    mock_component1 = mock.Mock()
    mock_component2 = mock.Mock()
    mock_fetch.side_effect = [mock_component1, mock_component2]

    output_dir = RootedPath(tmp_path / "output")

    components = _resolve_huggingface_lockfile(lockfile_path, output_dir)

    # Verify directories were created
    assert (output_dir.path / "deps" / "huggingface" / "hub").exists()
    assert (output_dir.path / "deps" / "huggingface" / "datasets").exists()

    # Verify _fetch_model was called for each model
    assert mock_fetch.call_count == 2

    # Verify components returned
    assert components == [mock_component1, mock_component2]
