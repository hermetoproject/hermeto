# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Any, Generator

import pytest
import yaml
from pydantic import ValidationError

import hermeto.core.config as config_module
from hermeto.core.errors import InvalidInput

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


@pytest.mark.parametrize(
    "proxy_url",
    [
        pytest.param("https://proxy.golang.org,direct", id="default_list"),
        pytest.param("https://goproxy.example.com", id="single_url"),
        pytest.param("https://goproxy.example.com,direct", id="url_and_direct"),
        pytest.param(
            "https://goproxy.example.com,https://proxy.golang.org,direct",
            id="url_url_direct",
        ),
        pytest.param("off", id="off"),
        pytest.param("direct", id="direct"),
    ],
)
def test_gomod_proxy_url_accepts_goproxy_lists(proxy_url: str) -> None:
    settings = config_module.GomodSettings(proxy_url=proxy_url)
    assert settings.proxy_url == proxy_url


def test_gomod_proxy_url_default() -> None:
    assert config_module.GomodSettings().proxy_url == "https://proxy.golang.org,direct"
    assert config_module.Config().gomod.proxy_url == "https://proxy.golang.org,direct"


@pytest.mark.parametrize(
    "proxy_url",
    [
        pytest.param("https://user:pass@goproxy.example.com", id="single_url_credentials"),
        pytest.param(
            "https://goproxy.example.com,https://user:pass@evil.example.com,direct",
            id="list_with_credentials",
        ),
        pytest.param("https://user@goproxy.example.com", id="user_only"),
        pytest.param("https://:pass@goproxy.example.com", id="password_only"),
    ],
)
def test_gomod_proxy_url_rejects_embedded_credentials(proxy_url: str) -> None:
    with pytest.raises(ValidationError, match="embedded credentials") as exc_info:
        config_module.GomodSettings(proxy_url=proxy_url)
    messages = " ".join(error["msg"] for error in exc_info.value.errors())
    assert "user:pass" not in messages
    assert "pass@" not in messages


@pytest.mark.parametrize(
    "proxy_url",
    [
        pytest.param("", id="empty"),
        pytest.param(",", id="empty_entries"),
        pytest.param("https://goproxy.example.com,", id="trailing_comma"),
        pytest.param("https://goproxy.example.com,,direct", id="blank_middle_entry"),
        pytest.param("not-a-url", id="invalid_token"),
        pytest.param("Direct", id="direct_wrong_case"),
        pytest.param("OFF", id="off_wrong_case"),
        pytest.param("ftp://goproxy.example.com", id="non_http_scheme"),
    ],
)
def test_gomod_proxy_url_rejects_empty_or_invalid_entries(proxy_url: str) -> None:
    with pytest.raises(ValidationError):
        config_module.GomodSettings(proxy_url=proxy_url)


def test_gomod_proxy_login_and_password_still_pair() -> None:
    settings = config_module.GomodSettings(
        proxy_url="https://goproxy.example.com",
        proxy_login="user",
        proxy_password="secret",  # noqa: S106
    )
    assert settings.proxy_login == "user"
    assert settings.proxy_password is not None
    assert settings.proxy_password.get_secret_value() == "secret"

    with pytest.raises(InvalidInput, match="Proxy password must be set"):
        config_module.GomodSettings(proxy_login="user")

    with pytest.raises(InvalidInput, match="Proxy login must be set"):
        config_module.GomodSettings(proxy_password="secret")  # noqa: S106
