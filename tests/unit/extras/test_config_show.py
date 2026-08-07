# SPDX-License-Identifier: GPL-3.0-only
import os

import pytest
import yaml

from hermeto.core.config import Config
from hermeto.core.extras.config_show import (
    ConfigDiff,
    _get_env_var_name,
    format_diff_output,
    format_yaml_output,
    get_config_diff,
    get_default_config,
    get_effective_config,
)


@pytest.fixture()
def _clean_hermeto_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure Config() returns pure defaults by clearing env vars and config files."""
    for key in list(os.environ):
        if key.startswith("HERMETO_") and not key.startswith("HERMETO_TEST_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr("hermeto.core.config.CONFIG_FILE_PATHS", [])


class TestGetEnvVarName:
    """Tests for environment variable name reconstruction."""

    @pytest.mark.parametrize(
        "section, field, expected",
        [
            ("gomod", "proxy_url", "HERMETO_GOMOD__PROXY_URL"),
            ("gomod", "download_max_tries", "HERMETO_GOMOD__DOWNLOAD_MAX_TRIES"),
            ("http", "connect_timeout", "HERMETO_HTTP__CONNECT_TIMEOUT"),
            ("http", "read_timeout", "HERMETO_HTTP__READ_TIMEOUT"),
            ("runtime", "subprocess_timeout", "HERMETO_RUNTIME__SUBPROCESS_TIMEOUT"),
            ("runtime", "concurrency_limit", "HERMETO_RUNTIME__CONCURRENCY_LIMIT"),
            ("pip", "ignore_dependencies_crates", "HERMETO_PIP__IGNORE_DEPENDENCIES_CRATES"),
            ("yarn", "enabled", "HERMETO_YARN__ENABLED"),
            ("npm", "proxy_url", "HERMETO_NPM__PROXY_URL"),
            ("npm", "proxy_login", "HERMETO_NPM__PROXY_LOGIN"),
            ("npm", "proxy_password", "HERMETO_NPM__PROXY_PASSWORD"),
        ],
    )
    def test_env_var_name_generation(self, section: str, field: str, expected: str) -> None:
        assert _get_env_var_name(section, field) == expected


class TestGetEffectiveConfig:
    """Tests for dumping current effective configuration."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_returns_all_sections(self) -> None:
        config = Config()
        effective = get_effective_config(config)

        assert set(effective.keys()) == set(Config.model_fields.keys())

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_section_order_matches_model_definition(self) -> None:
        """Output order must match Config model field order for readability."""
        config = Config()
        effective = get_effective_config(config)

        expected_order = list(Config.model_fields.keys())
        actual_order = list(effective.keys())
        assert actual_order == expected_order

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_field_order_within_sections_matches_model(self) -> None:
        """Field order within each section must match the settings class definition."""
        config = Config()
        effective = get_effective_config(config)

        for section_name in Config.model_fields:
            section_obj = getattr(config, section_name)
            if not hasattr(type(section_obj), "model_fields"):
                continue
            expected_fields = list(type(section_obj).model_fields.keys())
            actual_fields = list(effective[section_name].keys())
            assert actual_fields == expected_fields, (
                f"Field order mismatch in {section_name}: "
                f"expected {expected_fields}, got {actual_fields}"
            )


class TestGetDefaultConfig:
    """Tests for default configuration retrieval."""

    def test_returns_all_sections(self) -> None:
        defaults = get_default_config()
        assert set(defaults.keys()) == set(Config.model_fields.keys())

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_matches_effective_when_no_overrides(self) -> None:
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()
        assert effective == defaults


class TestGetConfigDiff:
    """Tests for configuration diff computation."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_no_diff_with_defaults(self) -> None:
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()
        diff = get_config_diff(effective, defaults)
        assert diff == {}

    def test_detects_changed_values(self) -> None:
        effective = {
            "gomod": {"proxy_url": "https://custom-proxy.example.com", "download_max_tries": 5},
            "http": {"connect_timeout": 30, "read_timeout": 600},
        }
        defaults = {
            "gomod": {"proxy_url": "https://proxy.golang.org,direct", "download_max_tries": 5},
            "http": {"connect_timeout": 30, "read_timeout": 300},
        }

        diff = get_config_diff(effective, defaults)

        assert "gomod" in diff
        gomod_diff = diff["gomod"]
        assert isinstance(gomod_diff, dict)
        assert "proxy_url" in gomod_diff
        assert gomod_diff["proxy_url"] == (
            "https://custom-proxy.example.com",
            "https://proxy.golang.org,direct",
        )

        assert "http" in diff
        http_diff = diff["http"]
        assert isinstance(http_diff, dict)
        assert "read_timeout" in http_diff
        assert http_diff["read_timeout"] == (600, 300)

    def test_unchanged_values_not_in_diff(self) -> None:
        effective = {"gomod": {"proxy_url": "same", "download_max_tries": 5}}
        defaults = {"gomod": {"proxy_url": "same", "download_max_tries": 5}}

        diff = get_config_diff(effective, defaults)
        assert diff == {}


class TestFormatYamlOutput:
    """Tests for YAML output formatting."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_yaml_roundtrip_matches_effective_config(self) -> None:
        """Dumped YAML can be parsed back and matches the effective config."""
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()

        output = format_yaml_output(effective, defaults)

        # yaml.safe_load natively ignores YAML comments
        parsed = yaml.safe_load(output)
        assert parsed == effective

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_contains_env_var_comments(self) -> None:
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()

        output = format_yaml_output(effective, defaults)
        assert "# HERMETO_GOMOD__PROXY_URL" in output
        assert "# HERMETO_HTTP__CONNECT_TIMEOUT" in output
        assert "# HERMETO_RUNTIME__CONCURRENCY_LIMIT" in output

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_no_star_markers_when_all_defaults(self) -> None:
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()

        output = format_yaml_output(effective, defaults)
        assert "# (*)" not in output

    def test_star_markers_on_changed_values(self) -> None:
        effective = {
            "gomod": {"proxy_url": "https://custom-proxy.example.com", "download_max_tries": 5},
        }
        defaults = {
            "gomod": {"proxy_url": "https://proxy.golang.org,direct", "download_max_tries": 5},
        }

        output = format_yaml_output(effective, defaults)
        value_lines = [
            line
            for line in output.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        star_lines = [line for line in value_lines if "# (*)" in line]
        assert len(star_lines) == 1
        assert "proxy_url" in star_lines[0]


class TestFormatDiffOutput:
    """Tests for diff output formatting."""

    def test_empty_diff(self) -> None:
        output = format_diff_output({})
        assert "All values are at their defaults" in output

    def test_non_empty_diff_is_valid_yaml(self) -> None:
        """Non-empty diff output is parseable YAML showing current values."""
        diff: ConfigDiff = {
            "gomod": {
                "proxy_url": ("https://custom.example.com", "https://proxy.golang.org,direct")
            },
            "http": {"read_timeout": (600, 300)},
        }
        output = format_diff_output(diff)
        # yaml.safe_load natively ignores YAML comments
        parsed = yaml.safe_load(output)
        assert parsed["gomod"]["proxy_url"] == "https://custom.example.com"
        assert parsed["http"]["read_timeout"] == 600


class TestSecretStrRedaction:
    """Tests for SecretStr-based redaction in config output."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_proxy_password_redacted_by_default(self) -> None:
        config = Config(
            gomod={
                "proxy_url": "https://proxy.example.com",
                "proxy_login": "user",
                "proxy_password": "s3cret",
            },  # noqa: S106
        )
        effective = get_effective_config(config)
        assert effective["gomod"]["proxy_password"] == "**********"  # noqa: S105

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_proxy_password_revealed_with_raw(self) -> None:
        config = Config(
            gomod={
                "proxy_url": "https://proxy.example.com",
                "proxy_login": "user",
                "proxy_password": "s3cret",
            },  # noqa: S106
        )
        effective = get_effective_config(config, raw=True)
        assert effective["gomod"]["proxy_password"] == "s3cret"  # noqa: S105

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_raw_flag_does_not_affect_non_sensitive_fields(self) -> None:
        """The raw flag should only reveal SecretStr fields, not change other values."""
        config = Config(
            gomod={
                "proxy_url": "https://proxy.example.com",
                "proxy_login": "user",
                "proxy_password": "s3cret",
            },  # noqa: S106
        )
        default_output = get_effective_config(config)
        raw_output = get_effective_config(config, raw=True)
        assert default_output["gomod"]["proxy_login"] == raw_output["gomod"]["proxy_login"]
        assert default_output["gomod"]["proxy_url"] == raw_output["gomod"]["proxy_url"]
