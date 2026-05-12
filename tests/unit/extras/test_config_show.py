# SPDX-License-Identifier: GPL-3.0-only
import os

import pytest
import yaml

from hermeto.core.config import Config
from hermeto.core.extras.config_show import (
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

    def test_returns_all_sections(self) -> None:
        config = Config()
        effective = get_effective_config(config)

        expected_sections = {"pip", "yarn", "npm", "gomod", "http", "runtime"}
        assert set(effective.keys()) == expected_sections

    def test_gomod_defaults(self) -> None:
        config = Config()
        effective = get_effective_config(config)

        gomod = effective["gomod"]
        assert gomod["proxy_url"] == "https://proxy.golang.org,direct"
        assert gomod["download_max_tries"] == 5
        assert gomod["environment_variables"] == {}

    def test_http_defaults(self) -> None:
        config = Config()
        effective = get_effective_config(config)

        http = effective["http"]
        assert http["connect_timeout"] == 30
        assert http["read_timeout"] == 300

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_runtime_defaults(self) -> None:
        config = Config()
        effective = get_effective_config(config)

        runtime = effective["runtime"]
        assert runtime["subprocess_timeout"] == 3600
        assert runtime["concurrency_limit"] == 5

    def test_section_order_matches_model_definition(self) -> None:
        """Output order must match Config model field order for readability."""
        config = Config()
        effective = get_effective_config(config)

        expected_order = list(Config.model_fields.keys())
        actual_order = list(effective.keys())
        assert actual_order == expected_order

    def test_field_order_within_sections_matches_model(self) -> None:
        """Field order within each section must match the settings class definition."""
        config = Config()
        effective = get_effective_config(config)

        for section_name in Config.model_fields:
            section_obj = getattr(config, section_name)
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
        expected_sections = {"pip", "yarn", "npm", "gomod", "http", "runtime"}
        assert set(defaults.keys()) == expected_sections

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
            "gomod": {"proxy_url": "https://custom.proxy.com", "download_max_tries": 5},
            "http": {"connect_timeout": 30, "read_timeout": 600},
        }
        defaults = {
            "gomod": {"proxy_url": "https://proxy.golang.org,direct", "download_max_tries": 5},
            "http": {"connect_timeout": 30, "read_timeout": 300},
        }

        diff = get_config_diff(effective, defaults)

        assert "gomod" in diff
        assert "proxy_url" in diff["gomod"]
        assert diff["gomod"]["proxy_url"] == ("https://custom.proxy.com", "https://proxy.golang.org,direct")

        assert "http" in diff
        assert "read_timeout" in diff["http"]
        assert diff["http"]["read_timeout"] == (600, 300)

    def test_unchanged_values_not_in_diff(self) -> None:
        effective = {"gomod": {"proxy_url": "same", "download_max_tries": 5}}
        defaults = {"gomod": {"proxy_url": "same", "download_max_tries": 5}}

        diff = get_config_diff(effective, defaults)
        assert diff == {}


class TestFormatYamlOutput:
    """Tests for YAML output formatting."""

    def test_output_is_valid_yaml(self) -> None:
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()

        output = format_yaml_output(effective, defaults)

        # Strip comment-only lines and parse remaining YAML
        yaml_lines = [
            line for line in output.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        yaml_content = "\n".join(yaml_lines)
        parsed = yaml.safe_load(yaml_content)
        assert parsed is not None
        assert "gomod" in parsed

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
            "gomod": {"proxy_url": "https://custom.com", "download_max_tries": 5},
        }
        defaults = {
            "gomod": {"proxy_url": "https://proxy.golang.org,direct", "download_max_tries": 5},
        }

        output = format_yaml_output(effective, defaults)
        # The changed value should have a star marker
        assert "# (*)" in output
        # The unchanged value should NOT have a star marker
        lines = output.splitlines()
        for line in lines:
            if "download_max_tries" in line and not line.strip().startswith("#"):
                assert "# (*)" not in line


class TestFormatDiffOutput:
    """Tests for diff output formatting."""

    def test_empty_diff(self) -> None:
        output = format_diff_output({})
        assert "All values are at their defaults" in output

    def test_shows_current_and_default(self) -> None:
        diff = {
            "gomod": {"proxy_url": ("https://custom.com", "https://proxy.golang.org,direct")},
        }
        output = format_diff_output(diff)
        assert "https://custom.com" in output
        assert "default:" in output
        assert "https://proxy.golang.org,direct" in output

    def test_diff_output_is_valid_yaml(self) -> None:
        diff = {
            "http": {"read_timeout": (600, 300)},
        }
        output = format_diff_output(diff)

        yaml_lines = [
            line for line in output.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        yaml_content = "\n".join(yaml_lines)
        parsed = yaml.safe_load(yaml_content)
        assert parsed is not None
        assert "http" in parsed

