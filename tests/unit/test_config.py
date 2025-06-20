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


def test_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    override_concurrency = DEFAULT_CONCURRENCY + 1
    monkeypatch.setenv("HERMETO_CONCURRENCY_LIMIT", str(override_concurrency))

    config = config_module.get_config()
    assert config.concurrency_limit == override_concurrency


def test_home_yaml_overrides_default(tmp_home_cwd: Path) -> None:
    override_concurrency = DEFAULT_CONCURRENCY + 1
    home_config_path = tmp_home_cwd / ".config/hermeto/config.yaml"
    _write_yaml_config(home_config_path, {"concurrency_limit": override_concurrency})

    config = config_module.get_config()
    assert config.concurrency_limit == override_concurrency


def test_cwd_yaml_overrides_home_yaml(tmp_home_cwd: Path) -> None:
    home_concurrency = DEFAULT_CONCURRENCY + 1
    home_config_path = tmp_home_cwd / ".config/hermeto/config.yaml"
    _write_yaml_config(home_config_path, {"concurrency_limit": home_concurrency})

    cwd_concurrency = home_concurrency + 1
    cwd_config_path = tmp_home_cwd / "config.yaml"
    _write_yaml_config(cwd_config_path, {"concurrency_limit": cwd_concurrency})

    config = config_module.get_config()
    assert config.concurrency_limit == cwd_concurrency


def test_cli_yaml_overrides_cwd_yaml(tmp_home_cwd: Path) -> None:
    cwd_concurrency = DEFAULT_CONCURRENCY + 1
    cwd_config_path = tmp_home_cwd / "config.yaml"
    _write_yaml_config(cwd_config_path, {"concurrency_limit": cwd_concurrency})

    cli_concurrency = cwd_concurrency + 1
    cli_config_path = tmp_home_cwd / "cli_config.yaml"
    _write_yaml_config(cli_config_path, {"concurrency_limit": cli_concurrency})

    config_module.set_config(cli_config_path)

    config = config_module.get_config()
    assert config.concurrency_limit == cli_concurrency
