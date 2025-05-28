from pathlib import Path
from typing import Any, Generator

import pytest
import yaml

import hermeto.core.config as config_module

DEFAULT_CONCURRENCY = config_module.Config.model_fields["concurrency_limit"].default


@pytest.fixture(autouse=True)
def reset_config_singleton() -> Generator[None, None, None]:
    """Reset the global config before and after a test."""
    config_module.config = None
    yield
    config_module.config = None


@pytest.fixture
def tmp_home_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a tmp_path which is HOME and the CWD."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_yaml_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))


@pytest.mark.parametrize("config_file_path", config_module.CONFIG_FILE_PATHS)
def test_config_files_override_defaults(tmp_home_cwd: Path, config_file_path: str) -> None:
    """Test that each configured file path can override default values."""
    override_concurrency = DEFAULT_CONCURRENCY + 1
    config_path = Path(config_file_path).expanduser()
    _write_yaml_config(config_path, {"concurrency_limit": override_concurrency})

    config = config_module.get_config()
    assert config.concurrency_limit == override_concurrency


def test_cli_config_file_overrides_defaults(tmp_home_cwd: Path) -> None:
    """Test that CLI-provided config file overrides defaults."""
    cli_concurrency = DEFAULT_CONCURRENCY + 1
    cli_config_path = tmp_home_cwd / "cli_config.yaml"
    _write_yaml_config(cli_config_path, {"concurrency_limit": cli_concurrency})

    config_module.set_config(cli_config_path)

    config = config_module.get_config()
    assert config.concurrency_limit == cli_concurrency
