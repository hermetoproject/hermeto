# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path
from typing import Any, Generator

import pytest
import yaml

import hermeto.core.config as config_module

DEFAULT_CONCURRENCY = config_module.RuntimeSettings.model_fields["concurrency_limit"].default


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


def test_normalize_config_structure(caplog: pytest.LogCaptureFixture) -> None:
    """Test that all legacy flat fields are migrated to the new namespaced structure."""
    legacy_config = {
        "goproxy_url": "https://custom.proxy",
        "gomod_download_max_tries": 10,
        "default_environment_variables": {"gomod": {"GOPROXY": "off"}},
        "requests_timeout": 600,
        "subprocess_timeout": 7200,
        "concurrency_limit": 10,
        "allow_yarnberry_processing": False,
        "ignore_pip_dependencies_crates": True,
    }

    config = config_module.Config.model_validate(legacy_config)

    assert config.gomod.proxy_url == "https://custom.proxy"
    assert config.gomod.download_max_tries == 10
    assert config.gomod.environment_variables == {"GOPROXY": "off"}
    assert config.http.read_timeout == 600
    assert config.runtime.subprocess_timeout == 7200
    assert config.runtime.concurrency_limit == 10
    assert config.yarn.enabled is False
    assert config.pip.ignore_dependencies_crates is True
    assert "is deprecated" in caplog.text


def test_migrate_http_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """Test that http.timeout is migrated to http.read_timeout."""
    config = config_module.Config.model_validate({"http": {"timeout": 123}})
    assert config.http.read_timeout == 123
    assert "Config option 'http.timeout' is deprecated" in caplog.text


def test_deprecated_field_removed_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Test that gomod_strict_vendor is removed and logs a deprecation warning."""
    config = config_module.Config.model_validate({"gomod_strict_vendor": True})

    assert config is not None
    assert "gomod_strict_vendor" in caplog.text
    assert "no longer has any effect" in caplog.text


def test_namespaced_fields_take_precedence_over_legacy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that new namespaced fields take precedence over legacy flat fields."""
    config = config_module.Config.model_validate(
        {
            "concurrency_limit": 5,
            "runtime": {"concurrency_limit": 10},
        }
    )

    assert config.runtime.concurrency_limit == 10
    assert "Both 'concurrency_limit' and 'runtime.concurrency_limit' are set" in caplog.text


def test_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that environment variables override defaults (V2 format)."""
    override_concurrency = DEFAULT_CONCURRENCY + 1
    monkeypatch.setenv("HERMETO_RUNTIME__CONCURRENCY_LIMIT", str(override_concurrency))

    config = config_module.get_config()
    assert config.runtime.concurrency_limit == override_concurrency


@pytest.mark.parametrize("config_file_path", config_module.CONFIG_FILE_PATHS)
def test_config_files_override_defaults(tmp_home_cwd: Path, config_file_path: str) -> None:
    """Test that each configured file path can override default values."""
    override_concurrency = DEFAULT_CONCURRENCY + 1
    config_path = Path(config_file_path).expanduser()
    _write_yaml_config(config_path, {"runtime": {"concurrency_limit": override_concurrency}})

    config = config_module.get_config()
    assert config.runtime.concurrency_limit == override_concurrency


def test_cli_config_file_overrides_defaults(tmp_home_cwd: Path) -> None:
    """Test that CLI-provided config file overrides defaults."""
    cli_concurrency = DEFAULT_CONCURRENCY + 1
    cli_config_path = tmp_home_cwd / "cli_config.yaml"
    _write_yaml_config(cli_config_path, {"runtime": {"concurrency_limit": cli_concurrency}})

    config_module.set_config(cli_config_path)

    config = config_module.get_config()
    assert config.runtime.concurrency_limit == cli_concurrency


def test_set_config_invalid_yaml(tmp_home_cwd: Path) -> None:
    """set_config() wraps YAML parse errors in InvalidInput so main() can handle them."""
    from hermeto.core.errors import InvalidInput

    bad_yaml = tmp_home_cwd / "bad.yaml"
    bad_yaml.write_text("key: [unclosed bracket\n")

    with pytest.raises(InvalidInput, match="Invalid YAML in configuration file"):
        config_module.set_config(bad_yaml)


def test_get_source_data_empty(tmp_home_cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env vars and no config files both source dicts are empty."""
    for key in list(os.environ):
        if key.startswith("HERMETO_") and not key.startswith("HERMETO_TEST_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(config_module, "CONFIG_FILE_PATHS", [])

    sources = config_module.get_source_data()

    assert sources["env"] == {}
    assert sources["file"] == {}


def test_get_source_data_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Values set via env vars appear in the 'env' key of source data."""
    monkeypatch.setenv("HERMETO_RUNTIME__CONCURRENCY_LIMIT", "99")
    monkeypatch.setattr(config_module, "CONFIG_FILE_PATHS", [])

    sources = config_module.get_source_data()

    # EnvSettingsSource returns raw strings without pydantic coercion — that is
    # intentional since we bypass full model validation for source attribution.
    assert sources["env"].get("runtime", {}).get("concurrency_limit") == "99"
    assert sources["file"] == {}


def test_get_source_data_file(tmp_home_cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Values set in a config file appear in the 'file' key of source data."""
    for key in list(os.environ):
        if key.startswith("HERMETO_") and not key.startswith("HERMETO_TEST_"):
            monkeypatch.delenv(key)

    config_path = tmp_home_cwd / "hermeto.yaml"
    _write_yaml_config(config_path, {"http": {"read_timeout": 999}})
    monkeypatch.setattr(config_module, "CONFIG_FILE_PATHS", [str(config_path)])

    sources = config_module.get_source_data()

    assert sources["env"] == {}
    assert sources["file"].get("http", {}).get("read_timeout") == 999


def test_get_source_data_cli_file(tmp_home_cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Values in the CLI-specified config file appear in the 'file' source."""
    for key in list(os.environ):
        if key.startswith("HERMETO_") and not key.startswith("HERMETO_TEST_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(config_module, "CONFIG_FILE_PATHS", [])

    cli_path = tmp_home_cwd / "cli.yaml"
    _write_yaml_config(cli_path, {"http": {"connect_timeout": 5}})

    sources = config_module.get_source_data(cli_config_path=cli_path)

    assert sources["file"].get("http", {}).get("connect_timeout") == 5
