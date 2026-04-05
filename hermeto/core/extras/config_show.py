# SPDX-License-Identifier: GPL-3.0-only
"""Configuration introspection utilities.

Provides functions to dump the current effective configuration, generate
corresponding environment variable names, and compute differences against
default values.
"""

from typing import Any

import yaml

from hermeto.core.config import Config

# Type aliases for configuration diff structures.
ConfigValue = str | int | float | bool | None | dict[str, Any] | list[Any]
FieldDiff = tuple[ConfigValue, ConfigValue]  # (current_value, default_value)
# Recursive: a section maps field names to FieldDiffs, or to further nested sections.
ConfigDiff = dict[str, "FieldDiff | ConfigDiff"]


def _get_env_var_name(*parts: str) -> str:
    """Reconstruct the environment variable name from config key parts.

    Derives the prefix and nested delimiter from Config.model_config rather than
    hardcoding them, so that changes to the configuration schema are automatically
    reflected.

    >>> _get_env_var_name("gomod", "proxy_url")
    'HERMETO_GOMOD__PROXY_URL'
    >>> _get_env_var_name("runtime", "concurrency_limit")
    'HERMETO_RUNTIME__CONCURRENCY_LIMIT'
    """
    prefix = Config.model_config.get("env_prefix") or ""
    delimiter = Config.model_config.get("env_nested_delimiter") or "__"
    return f"{prefix}{delimiter.join(part.upper() for part in parts)}"


def get_effective_config(config: Config, *, raw: bool = False) -> dict[str, Any]:
    """Get the current effective configuration as a nested dict.

    Uses Pydantic's model_dump(mode='json') for serialization, which handles
    SecretStr redaction, Enum conversion, HttpUrl stringification, etc.
    SecretStr fields are redacted unless raw=True.
    """
    context = {"reveal": True} if raw else {}
    return config.model_dump(mode="json", context=context)


def get_default_config() -> dict[str, Any]:
    """Get the default configuration values.

    Uses field.default from Config.model_fields rather than Config() because
    Config extends BaseSettings, so Config() would read from env vars and
    config files instead of returning pure schema defaults.
    """
    result: dict[str, Any] = {}
    for name, field in Config.model_fields.items():
        default = field.default
        if hasattr(type(default), "model_fields"):
            result[name] = default.model_dump(mode="json")
        else:
            result[name] = default.value if hasattr(default, "value") else default
    return result


def get_config_diff(
    effective: dict[str, Any],
    defaults: dict[str, Any],
) -> ConfigDiff:
    """Compare effective config against defaults.

    Recursively walks nested dicts and returns only values that differ.
    For nested dicts, produces nested diff dicts.
    For leaf values, produces a FieldDiff tuple of (current, default).
    """
    diff: ConfigDiff = {}

    for key, current_value in effective.items():
        default_value = defaults.get(key)

        if isinstance(current_value, dict) and isinstance(default_value, dict):
            sub_diff = get_config_diff(current_value, default_value)
            if sub_diff:
                diff[key] = sub_diff
        elif current_value != default_value:
            diff[key] = (current_value, default_value)

    return diff


def format_yaml_output(
    effective: dict[str, Any],
    defaults: dict[str, Any],
) -> str:
    """Format effective config as YAML with env var comments and diff markers.

    Produces valid, parseable YAML. Env var names are shown as comments above
    each field. Values that differ from defaults are marked with ``# (*)``.

    The output can be piped to a file and parsed by a YAML processor.
    """
    lines: list[str] = [
        "# Current effective configuration",
        "# Values marked with (*) differ from defaults",
        "# Environment variables shown in comments",
        "",
    ]

    _walk_yaml_lines(effective, defaults, lines, depth=0, env_parts=[])

    return "\n".join(lines)


def _walk_yaml_lines(
    data: dict[str, Any],
    defaults: dict[str, Any],
    lines: list[str],
    depth: int,
    env_parts: list[str],
) -> None:
    """Recursively build YAML output lines with env var comments and diff markers."""
    indent = "  " * depth

    for key, value in data.items():
        default_value = defaults.get(key)
        current_env_parts = env_parts + [key]

        if isinstance(value, dict) and value:
            lines.append(f"{indent}{key}:")
            sub_defaults = default_value if isinstance(default_value, dict) else {}
            _walk_yaml_lines(value, sub_defaults, lines, depth + 1, current_env_parts)
            if depth == 0:
                lines.append("")
        else:
            env_var = _get_env_var_name(*current_env_parts)
            lines.append(f"{indent}# {env_var}")
            yaml_value = _format_yaml_value(value)
            if value != default_value:
                lines.append(f"{indent}{key}: {yaml_value}  # (*)")
            else:
                lines.append(f"{indent}{key}: {yaml_value}")
            if depth == 0:
                lines.append("")


def format_diff_output(
    diff: ConfigDiff,
) -> str:
    """Format only changed values, showing current and default values.

    Produces valid, parseable YAML with default values shown in comments.

    >>> format_diff_output({"http": {"read_timeout": (600, 300)}})
    '# Only showing values that differ from defaults\\n\\nhttp:\\n  read_timeout: 600  # default: 300\\n'
    >>> format_diff_output({})
    '# All values are at their defaults'
    """
    if not diff:
        return "# All values are at their defaults"

    lines: list[str] = [
        "# Only showing values that differ from defaults",
        "",
    ]

    _walk_diff_lines(diff, lines, depth=0)

    return "\n".join(lines)


def _walk_diff_lines(
    diff: ConfigDiff,
    lines: list[str],
    depth: int,
) -> None:
    """Recursively build diff output lines."""
    indent = "  " * depth

    for key, value in diff.items():
        if isinstance(value, tuple):
            current_value, default_value = value
            yaml_current = _format_yaml_value(current_value)
            yaml_default = _format_yaml_value(default_value)
            lines.append(f"{indent}{key}: {yaml_current}  # default: {yaml_default}")
            if depth == 0:
                lines.append("")
        else:
            lines.append(f"{indent}{key}:")
            _walk_diff_lines(value, lines, depth + 1)
            if depth == 0:
                lines.append("")


def _format_yaml_value(value: ConfigValue) -> str:
    """Format a single value for inline YAML representation.

    Uses yaml.dump for correctness (handles quoting, special chars, etc.)
    and strips trailing newlines/document markers.
    """
    match value:
        case None:
            return "null"
        case bool():
            return str(value).lower()
        case dict() | list():
            if not value:
                return "{}" if isinstance(value, dict) else "[]"
            return yaml.dump(value, default_flow_style=True, width=float("inf")).strip()
        case str():
            # Use yaml.dump to handle quoting correctly
            dumped = yaml.dump(value, default_flow_style=True, width=float("inf"))
            # yaml.dump adds "...\n" for simple strings, strip document end marker
            dumped = dumped.removesuffix("...\n").strip()
            return dumped
        case _:
            return str(value)
