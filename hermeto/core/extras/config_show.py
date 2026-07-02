# SPDX-License-Identifier: GPL-3.0-only
"""Configuration introspection utilities.

Provides functions to dump the current effective configuration, generate
corresponding environment variable names, and compute differences against
default values.
"""

import enum
from typing import Any

import yaml

from hermeto.core.config import Config


def _get_env_var_name(section: str, field: str) -> str:
    """Reconstruct the environment variable name for a config field.

    Derives the prefix and nested delimiter from Config.model_config rather than
    hardcoding them, so that changes to the configuration schema are automatically
    reflected.

    Example: ("gomod", "proxy_url") -> "HERMETO_GOMOD__PROXY_URL"
    """
    prefix = Config.model_config.get("env_prefix", "")
    delimiter = Config.model_config.get("env_nested_delimiter", "__")
    return f"{prefix}{section.upper()}{delimiter}{field.upper()}"


def _serialize_value(value: Any) -> Any:
    """Serialize a config value into a YAML/JSON-friendly representation."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, enum.Enum):
        return value.value
    # Convert HttpUrl and other non-primitive types to strings
    if not isinstance(value, (str, int, float, bool)):
        return str(value)
    return value


def _is_section(obj: Any) -> bool:
    """Check if a config field value is a nested settings section (Pydantic model)."""
    return hasattr(type(obj), "model_fields")


def _get_section_dict(section_obj: Any) -> dict[str, Any]:
    """Extract and serialize all fields from a config section object."""
    return {
        field_name: _serialize_value(getattr(section_obj, field_name))
        for field_name in type(section_obj).model_fields
    }


def get_effective_config(config: Config) -> dict[str, dict[str, Any] | Any]:
    """Get the current effective configuration as a nested dict.

    Returns a dict of section_name -> {field_name: value} for nested sections,
    or section_name -> scalar_value for non-section fields.
    All values are serialized to YAML-friendly types.
    """
    result: dict[str, dict[str, Any] | Any] = {}
    for name in Config.model_fields:
        value = getattr(config, name)
        if _is_section(value):
            result[name] = _get_section_dict(value)
        else:
            result[name] = _serialize_value(value)
    return result


def get_default_config() -> dict[str, dict[str, Any] | Any]:
    """Get the default configuration values.

    Uses field.default from Config.model_fields to avoid issues with
    deferred type annotations.
    """
    result: dict[str, dict[str, Any] | Any] = {}
    for name, field in Config.model_fields.items():
        default = field.default
        if _is_section(default):
            result[name] = _get_section_dict(default)
        else:
            result[name] = _serialize_value(default)
    return result


def get_config_diff(
    effective: dict[str, dict[str, Any] | Any],
    defaults: dict[str, dict[str, Any] | Any],
) -> dict[str, dict[str, tuple[Any, Any]] | tuple[Any, Any]]:
    """Compare effective config against defaults.

    Returns only sections and fields where values differ.
    For nested sections, each changed field maps to a tuple of (current, default).
    For scalar fields, the section itself maps to a tuple of (current, default).
    """
    diff: dict[str, dict[str, tuple[Any, Any]] | tuple[Any, Any]] = {}

    for section_name, section_values in effective.items():
        default_values = defaults.get(section_name)

        if not isinstance(section_values, dict):
            if section_values != default_values:
                diff[section_name] = (section_values, default_values)
            continue

        section_defaults = default_values if isinstance(default_values, dict) else {}
        section_diff: dict[str, tuple[Any, Any]] = {}

        for field_name, current_value in section_values.items():
            default_value = section_defaults.get(field_name)
            if current_value != default_value:
                section_diff[field_name] = (current_value, default_value)

        if section_diff:
            diff[section_name] = section_diff

    return diff


def format_yaml_output(
    effective: dict[str, dict[str, Any] | Any],
    defaults: dict[str, dict[str, Any] | Any],
) -> str:
    """Format effective config as YAML with env var comments and diff markers.

    Produces valid, parseable YAML. Env var names are shown as comments above
    each field. Values that differ from defaults are marked with ``# (*)``.

    The output can be piped to a file and parsed by a YAML processor.
    """
    prefix = Config.model_config.get("env_prefix", "")
    lines: list[str] = [
        "# Current effective configuration",
        "# Values marked with (*) differ from defaults",
        "# Environment variables shown in comments",
        "",
    ]

    for section_name, section_values in effective.items():
        default_values = defaults.get(section_name)

        if not isinstance(section_values, dict):
            env_var = f"{prefix}{section_name.upper()}"
            lines.append(f"# {env_var}")
            yaml_value = _format_yaml_value(section_values)
            if section_values != default_values:
                lines.append(f"{section_name}: {yaml_value}  # (*)")
            else:
                lines.append(f"{section_name}: {yaml_value}")
            lines.append("")
            continue

        lines.append(f"{section_name}:")
        section_defaults = default_values if isinstance(default_values, dict) else {}

        for field_name, value in section_values.items():
            env_var = _get_env_var_name(section_name, field_name)
            lines.append(f"  # {env_var}")

            yaml_value = _format_yaml_value(value)
            default_value = section_defaults.get(field_name)

            if value != default_value:
                lines.append(f"  {field_name}: {yaml_value}  # (*)")
            else:
                lines.append(f"  {field_name}: {yaml_value}")

        lines.append("")

    return "\n".join(lines)


def format_diff_output(
    diff: dict[str, dict[str, tuple[Any, Any]] | tuple[Any, Any]],
) -> str:
    """Format only changed values, showing current and default values.

    Produces valid, parseable YAML with default values shown in comments.
    """
    if not diff:
        return "# All values are at their defaults"

    lines: list[str] = [
        "# Only showing values that differ from defaults",
        "",
    ]

    for section_name, section_diff in diff.items():
        if isinstance(section_diff, tuple):
            current_value, default_value = section_diff
            yaml_current = _format_yaml_value(current_value)
            yaml_default = _format_yaml_value(default_value)
            lines.append(f"{section_name}: {yaml_current}  # default: {yaml_default}")
            lines.append("")
            continue

        lines.append(f"{section_name}:")

        for field_name, (current_value, default_value) in section_diff.items():
            yaml_current = _format_yaml_value(current_value)
            yaml_default = _format_yaml_value(default_value)
            lines.append(f"  {field_name}: {yaml_current}  # default: {yaml_default}")

        lines.append("")

    return "\n".join(lines)


def _format_yaml_value(value: Any) -> str:
    """Format a single value for inline YAML representation.

    Uses yaml.dump for correctness (handles quoting, special chars, etc.)
    and strips trailing newlines/document markers.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list)):
        if not value:
            return "{}" if isinstance(value, dict) else "[]"
        return yaml.dump(value, default_flow_style=True).strip()
    if isinstance(value, str):
        # Use yaml.dump to handle quoting correctly
        dumped = yaml.dump(value, default_flow_style=True).strip()
        # yaml.dump adds "...\n" for simple strings, strip document end marker
        if dumped.endswith("\n..."):
            dumped = dumped[:-4].strip()
        return dumped
    return str(value)
