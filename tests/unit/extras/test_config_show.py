# SPDX-License-Identifier: GPL-3.0-only
from typing import Any

import pytest
import yaml

from hermeto.core.config import Config
from hermeto.core.extras.config_show import (
    ConfigDiff,
    SourceMap,
    _get_env_var_name,
    build_source_map,
    format_diff_output,
    format_yaml_output,
    get_config_diff,
    get_default_config,
    get_effective_config,
)


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


class TestBuildSourceMap:
    """Tests for source attribution map construction."""

    def test_env_wins_over_file(self) -> None:
        """When the same key is set in both env and file, env wins."""
        sources: dict[str, dict[str, Any]] = {
            "env": {"http": {"read_timeout": 600}},
            "file": {"http": {"read_timeout": 900}},
        }
        defaults = {"http": {"read_timeout": 300, "connect_timeout": 30}}

        sm = build_source_map(sources, defaults)

        assert sm["http"]["read_timeout"] == "env"

    def test_file_used_when_no_env(self) -> None:
        """A key only in file gets 'file' label."""
        sources: dict[str, dict[str, Any]] = {
            "env": {},
            "file": {"http": {"read_timeout": 900}},
        }
        defaults = {"http": {"read_timeout": 300, "connect_timeout": 30}}

        sm = build_source_map(sources, defaults)

        assert sm["http"]["read_timeout"] == "file"

    def test_default_when_no_sources(self) -> None:
        """A key absent from all sources gets 'default' label."""
        sources: dict[str, dict[str, Any]] = {"env": {}, "file": {}}
        defaults = {"http": {"read_timeout": 300}}

        sm = build_source_map(sources, defaults)

        assert sm["http"]["read_timeout"] == "default"

    def test_nested_section_mixed_sources(self) -> None:
        """Env and file can each provide different fields within the same section."""
        sources: dict[str, dict[str, Any]] = {
            "env": {"gomod": {"download_max_tries": 10}},
            "file": {"gomod": {"proxy_url": "https://custom.proxy"}},
        }
        defaults = {
            "gomod": {
                "proxy_url": "https://proxy.golang.org,direct",
                "download_max_tries": 5,
            }
        }

        sm = build_source_map(sources, defaults)
        gomod_sm = sm["gomod"]
        assert isinstance(gomod_sm, dict)
        assert gomod_sm["download_max_tries"] == "env"
        assert gomod_sm["proxy_url"] == "file"

    def test_all_labels_present_in_full_config(self) -> None:
        """build_source_map covers every field that defaults defines."""
        sources: dict[str, dict[str, Any]] = {"env": {}, "file": {}}
        defaults = get_default_config()

        sm = build_source_map(sources, defaults)

        # Every top-level section must be present
        assert set(sm.keys()) == set(defaults.keys())

    def test_source_only_scalar_key_gets_label(self) -> None:
        """A scalar key absent from defaults but present in a source is annotated."""
        sources: dict[str, dict[str, Any]] = {
            "env": {"extra_key": "value"},
            "file": {},
        }
        defaults: dict[str, Any] = {}

        sm = build_source_map(sources, defaults)

        assert sm["extra_key"] == "env"

    def test_source_only_scalar_env_wins_over_file(self) -> None:
        """When a source-only key appears in both env and file, env takes priority."""
        sources: dict[str, dict[str, Any]] = {
            "env": {"shared_key": "from-env"},
            "file": {"shared_key": "from-file"},
        }
        defaults: dict[str, Any] = {}

        sm = build_source_map(sources, defaults)

        assert sm["shared_key"] == "env"

    def test_scalar_overrides_dict_section_gets_source_label(self) -> None:
        """A source providing a scalar where the schema expects a dict is labelled,
        not silently dropped — the user needs to see which source is at fault."""
        sources: dict[str, dict[str, Any]] = {
            "env": {"http": "not-a-dict"},
            "file": {},
        }
        defaults = {"http": {"read_timeout": 300}}

        sm = build_source_map(sources, defaults)

        # Section key gets the source label rather than a nested SourceMap.
        assert sm["http"] == "env"


class TestFormatYamlWithSources:
    """Tests for source annotations in format_yaml_output."""

    def test_env_source_annotation(self) -> None:
        """[env] annotation appears for a field provided via env source."""
        effective = {"gomod": {"proxy_url": "https://custom.proxy", "download_max_tries": 5}}
        defaults = {
            "gomod": {"proxy_url": "https://proxy.golang.org,direct", "download_max_tries": 5}
        }
        sm: SourceMap = {"gomod": {"proxy_url": "env", "download_max_tries": "default"}}

        output = format_yaml_output(effective, defaults, source_map=sm)

        assert "[env]" in output
        # The env annotation should appear on the comment line for proxy_url
        proxy_url_comment_line = next(
            line for line in output.splitlines() if "HERMETO_GOMOD__PROXY_URL" in line
        )
        assert "[env]" in proxy_url_comment_line

    def test_file_source_annotation(self) -> None:
        """[file] annotation appears for a field provided via a config file."""
        effective = {"http": {"read_timeout": 600, "connect_timeout": 30, "max_retries": 5}}
        defaults = {"http": {"read_timeout": 300, "connect_timeout": 30, "max_retries": 5}}
        sm: SourceMap = {
            "http": {"read_timeout": "file", "connect_timeout": "default", "max_retries": "default"}
        }

        output = format_yaml_output(effective, defaults, source_map=sm)

        timeout_line = next(
            line for line in output.splitlines() if "HERMETO_HTTP__READ_TIMEOUT" in line
        )
        assert "[file]" in timeout_line

    def test_default_annotation_present(self) -> None:
        """[default] annotation appears for every field that uses the default value."""
        effective = {"http": {"read_timeout": 300, "connect_timeout": 30, "max_retries": 5}}
        defaults = {"http": {"read_timeout": 300, "connect_timeout": 30, "max_retries": 5}}
        sm: SourceMap = {
            "http": {
                "read_timeout": "default",
                "connect_timeout": "default",
                "max_retries": "default",
            }
        }

        output = format_yaml_output(effective, defaults, source_map=sm)

        comment_lines = [line for line in output.splitlines() if "HERMETO_HTTP__" in line]
        assert all("[default]" in line for line in comment_lines)

    def test_no_source_annotation_without_source_map(self) -> None:
        """When source_map is None, no source annotations appear."""
        effective = {"http": {"read_timeout": 300, "connect_timeout": 30, "max_retries": 5}}
        defaults = {"http": {"read_timeout": 300, "connect_timeout": 30, "max_retries": 5}}

        output = format_yaml_output(effective, defaults)

        assert "[env]" not in output
        assert "[file]" not in output
        assert "[default]" not in output


class TestFormatDiffWithSources:
    """Tests for source annotations in format_diff_output."""

    def test_source_annotation_on_changed_line(self) -> None:
        """[env] annotation appears on a changed line when source_map provided."""
        diff: ConfigDiff = {"http": {"read_timeout": (600, 300)}}
        sm: SourceMap = {"http": {"read_timeout": "env"}}

        output = format_diff_output(diff, source_map=sm)

        assert "[env]" in output
        changed_line = next(line for line in output.splitlines() if "read_timeout" in line)
        assert "# default: 300" in changed_line
        assert "[env]" in changed_line

    def test_no_source_annotation_without_source_map(self) -> None:
        """format_diff_output without source_map behaves exactly as before."""
        diff: ConfigDiff = {"http": {"read_timeout": (600, 300)}}

        output = format_diff_output(diff)

        assert "[env]" not in output
        assert "[file]" not in output
        assert "[default]" not in output
