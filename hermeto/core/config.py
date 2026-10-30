# SPDX-License-Identifier: GPL-3.0-only
import logging
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    FieldSerializationInfo,
    HttpUrl,
    SecretStr,
    ValidationError,
    field_serializer,
    model_validator,
)
from pydantic_core import ErrorDetails
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
    YamlConfigSettingsSource,
)

from hermeto import APP_NAME
from hermeto.core.constants import Mode
from hermeto.core.errors import InvalidInput

# Ascending priority
CONFIG_FILE_PATHS = [
    f"~/.config/{APP_NAME.lower()}/config.yaml",
    f"{APP_NAME.lower()}.yaml",
    f".{APP_NAME.lower()}.yaml",
]
log = logging.getLogger(__name__)
config = None

_FLAT_FIELD_MIGRATIONS = [
    ("allow_yarnberry_processing", ("yarn", "enabled")),
    ("ignore_pip_dependencies_crates", ("pip", "ignore_dependencies_crates")),
    ("goproxy_url", ("gomod", "proxy_url")),
    ("gomod_download_max_tries", ("gomod", "download_max_tries")),
    ("requests_timeout", ("http", "read_timeout")),
    ("subprocess_timeout", ("runtime", "subprocess_timeout")),
    ("concurrency_limit", ("runtime", "concurrency_limit")),
]


def _remove_gomod_strict_vendor(data: dict[str, Any]) -> None:
    """Remove the deprecated gomod_strict_vendor field with a warning."""
    if "gomod_strict_vendor" in data:
        data.pop("gomod_strict_vendor")
        log.warning(
            "The 'gomod_strict_vendor' config option is deprecated and no longer has any effect. "
            f"{APP_NAME.capitalize()} will always check the vendored contents and fail if they "
            "are not up-to-date. Please remove this option from your configuration."
        )


def _migrate_deprecated_field(
    data: dict[str, Any],
    old_key: str,
    value: Any,
    namespace: str,
    new_key: str,
) -> None:
    """Migrate a deprecated config field to its replacement.

    If the new field isn't already set, copies the value and warns about deprecation.
    If already set, keeps the existing value and warns about the conflict.
    """
    data.setdefault(namespace, {})
    new_path = f"{namespace}.{new_key}"

    if new_key not in data[namespace]:
        data[namespace][new_key] = value
        log.warning(f"Config option '{old_key}' is deprecated. Please use '{new_path}' instead.")
    else:
        log.warning(
            f"Both '{old_key}' and '{new_path}' are set. "
            f"Using '{new_path}'. Please remove '{old_key}'."
        )


def _proxy_url_must_not_contain_credentials(url: HttpUrl | None) -> HttpUrl | None:
    if url is None:
        return None
    if url.username or url.password:
        raise ValueError(
            "User-provided proxy URL appears to contain embedded credentials. "
            "Please ensure that proxy URL does not contain credentials "
            "supplied as protocol://<login>:<password>@address. Please use "
            "appropriate environment variables or configuration file entries for that. "
            "Please refer to documentation for further information on how to set up "
            "proxy access.",
        )
    return url


ProxyUrl = Annotated[
    HttpUrl | None,
    AfterValidator(_proxy_url_must_not_contain_credentials),
]


class ProxyMixin(BaseModel):
    """Proxy URL and authorization mixin.

    This class models artifactory proxies rather than traditional Squid-like proxies.
    """

    proxy_url: ProxyUrl = None
    proxy_login: str | None = None
    proxy_password: SecretStr | None = None

    def _backend_name(self) -> str:
        """Return the lowercase backend name for use in error messages."""
        return type(self).__name__.removesuffix("Settings").lower()

    @model_validator(mode="after")
    def _validate_login_and_password_both_set(self) -> Self:
        if self.proxy_login is not None and self.proxy_password is None:
            raise InvalidInput(
                f"{self._backend_name()}: Proxy password must be set when proxy login is set"
            )
        if self.proxy_login is None and self.proxy_password is not None:
            raise InvalidInput(
                f"{self._backend_name()}: Proxy login must be set when proxy password is set"
            )
        return self

    @model_validator(mode="after")
    def _validate_proxy_url_is_set_when_proxy_credentials_are_set(self) -> Self:
        if self.proxy_login is not None and self.proxy_url is None:
            raise InvalidInput(
                f"{self._backend_name()}: Proxy URL must be set when proxy credentials are set"
            )
        return self

    @field_serializer("proxy_password")
    def _serialize_proxy_password(
        self, value: SecretStr | None, info: FieldSerializationInfo
    ) -> str | None:
        if value is None:
            return None
        if info.context and info.context.get("reveal"):
            return value.get_secret_value()
        return str(value)


class PipSettings(ProxyMixin, extra="forbid"):
    """Settings for Pip."""

    # This setting exists for legacy use-cases only and must not be relied upon
    ignore_dependencies_crates: bool = False


class YarnSettings(ProxyMixin, extra="forbid"):
    """Settings for Yarn v2+."""

    # This setting exists for legacy use-cases only and must not be relied upon
    enabled: bool = True


class GomodSettings(ProxyMixin, extra="forbid"):
    """Settings for Go modules."""

    # TODO: refactor typesystem to trivially include comma-separated lists of URLs.
    # Ignore type error until that happens.
    proxy_url: str = "https://proxy.golang.org,direct"  # type: ignore
    download_max_tries: int = 5
    environment_variables: dict[str, str] = {}


class HttpSettings(BaseModel, extra="forbid"):
    """HTTP-related settings."""

    connect_timeout: int = 30
    read_timeout: int = 300
    max_retries: int = 5


class RuntimeSettings(BaseModel, extra="forbid"):
    """General runtime execution settings."""

    # This is how an environment variable name should look like:
    #   HERMETO_RUNTIME__CONCURRENCY_LIMIT
    # Note single underscore after application name, then name of the section
    # as it appears in Config class definition, then double underscore and
    # field name after that.
    subprocess_timeout: int = 3600
    concurrency_limit: int = 5


class NpmSettings(ProxyMixin, extra="forbid"):
    """Npm settings."""


class PnpmSettings(ProxyMixin, extra="forbid"):
    """Pnpm settings."""


class BundlerSettings(ProxyMixin, extra="forbid"):
    """Bundler settings."""


class CargoSettings(ProxyMixin, extra="forbid"):
    """Cargo settings."""

    @model_validator(mode="after")
    def _ensure_proxy_url_ends_with_slash(self) -> Self:
        # cargo fails when sparse registry URL does not end in a slash.
        if self.proxy_url is not None and str(self.proxy_url)[-1] != "/":
            self.proxy_url = HttpUrl(str(self.proxy_url) + "/")
        return self


class Config(BaseSettings):
    """Singleton that provides default configuration for the application process."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        # Double underscore is pydantic-settings' convention for nested config structures.
        # Single underscores can't be used since they appear in field names (e.g., concurrency_limit).
        env_nested_delimiter="__",
        env_prefix=f"{APP_NAME.upper()}_",
        extra="forbid",
    )

    cargo: CargoSettings = CargoSettings()
    pip: PipSettings = PipSettings()
    yarn: YarnSettings = YarnSettings()
    npm: NpmSettings = NpmSettings()
    pnpm: PnpmSettings = PnpmSettings()
    bundler: BundlerSettings = BundlerSettings()
    gomod: GomodSettings = GomodSettings()
    http: HttpSettings = HttpSettings()
    runtime: RuntimeSettings = RuntimeSettings()
    mode: Mode = Mode.STRICT

    @model_validator(mode="before")
    @classmethod
    def _normalize_config_structure(cls, data: Any) -> Any:
        """Normalize config data to the new namespaced structure.

        - Remove deprecated fields with warnings
        - Migrate legacy flat fields to new namespaced structure

        FIXME: Drop these normalizations and conversions on the next major release
        """
        if not isinstance(data, dict):
            return data

        _remove_gomod_strict_vendor(data)

        for old_key, (namespace, new_key) in _FLAT_FIELD_MIGRATIONS:
            if old_key in data:
                _migrate_deprecated_field(data, old_key, data.pop(old_key), namespace, new_key)

        # Migrate http.timeout -> http.read_timeout
        http_data = data.get("http")
        if isinstance(http_data, dict) and "timeout" in http_data:
            _migrate_deprecated_field(
                data,
                "http.timeout",
                http_data.pop("timeout"),
                "http",
                "read_timeout",
            )

        # default_environment_variables.gomod -> gomod.environment_variables
        # (default_environment_variables only ever supported the gomod backend)
        default_gomod_env_vars = data.pop("default_environment_variables", {}).get("gomod")
        if default_gomod_env_vars is not None:
            _migrate_deprecated_field(
                data,
                "default_environment_variables",
                default_gomod_env_vars,
                "gomod",
                "environment_variables",
            )

        return data

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Control allowed settings sources and priority.

        Priority (highest to lowest): init_settings (for programmatic/test overrides),
        env vars, CLI config file, default config files.

        https://docs.pydantic.dev/2.11/concepts/pydantic_settings/#customise-settings-sources
        """
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(
                settings_cls
            ),  # The CLI config path from yaml_file in model_config
            YamlConfigSettingsSource(settings_cls, CONFIG_FILE_PATHS),
        )


def create_cli_config_class(config_path: Path) -> type[Config]:
    """Return a subclass of Config that uses the CLI YAML file input.

    This is necessary because the path of the YAML config file from the CLI is not known
    ahead of time: https://github.com/pydantic/pydantic-settings/issues/259
    """

    class CLIConfig(Config):
        """A subclass of Config that uses the CLI YAML file input."""

        model_config = SettingsConfigDict(
            case_sensitive=False,
            env_nested_delimiter="__",
            env_prefix=f"{APP_NAME.upper()}_",
            extra="forbid",
            yaml_file=config_path,
        )

    return CLIConfig


def _present_config_error(validation_error: ValidationError) -> str:
    """Format validation errors for configuration sources (env vars, config files)."""
    errors = validation_error.errors()
    n_errors = len(errors)

    def show_error(error: ErrorDetails) -> str:
        location = " -> ".join(map(str, error["loc"]))
        message = error["msg"]
        return f"{location}: {message}"

    formatted_errors = "\n".join(show_error(e) for e in errors)

    return (
        f"{n_errors} validation error{'s' if n_errors > 1 else ''} in {APP_NAME.capitalize()} "
        f"configuration:\n{formatted_errors}\n\n"
        f"Configuration can be provided via:\n"
        f"  - Environment variables (e.g., {APP_NAME.upper()}_RUNTIME__CONCURRENCY_LIMIT=5)\n"
        f"  - CLI --config-file option\n"
        f"  - Config files ({', '.join(CONFIG_FILE_PATHS)})"
    )


def _present_settings_error(settings_error: SettingsError) -> str:
    """Format pydantic-settings source errors into an actionable message."""
    msg = str(settings_error)
    # SettingsError format: 'error parsing value for field "http" from source "EnvSettingsSource"'
    field = msg.split('"')[1] if '"' in msg else "unknown"

    return (
        f"Failed to parse environment variable for the '{field}' section. "
        f"Nested sections must be valid JSON when set via environment variables."
    )


def _extract_specific_from_yaml_error(exc: Exception) -> tuple[str, str]:
    """Extract safe source path and location from a YAML error."""
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return "", ""
    source = getattr(mark, "name", "") or ""
    location = f"line {mark.line + 1}, column {mark.column + 1}"
    return source, location


def get_config() -> Config:
    """Get the configuration singleton."""
    global config

    if not config:
        try:
            config = Config()
        except ValidationError as e:
            raise InvalidInput(_present_config_error(e)) from e
        except SettingsError as e:
            raise InvalidInput(_present_settings_error(e)) from e
        except yaml.YAMLError as e:
            source, location = _extract_specific_from_yaml_error(e)
            at = f" at {location}" if location else ""
            in_file = f" in {source}" if source else ""
            raise InvalidInput(
                f"Invalid YAML{in_file}{at}. Check your configuration files for syntax errors."
            ) from e

    return config


def set_config(path: Path) -> Config:
    """Set global config variable using input from file."""
    global config
    # Validate beforehand for a friendlier error message: https://github.com/pydantic/pydantic-settings/pull/432
    try:
        Config.model_validate(yaml.safe_load(path.read_text()))
    except ValidationError as e:
        raise InvalidInput(_present_config_error(e)) from e
    except yaml.YAMLError as e:
        # Only report file path and location, never raw snippets — secrets
        # in the file must not leak into error output (GHSA-w9qc-9m5h-qqmh).
        _, location = _extract_specific_from_yaml_error(e)
        at = f" at {location}" if location else ""
        raise InvalidInput(f"Invalid YAML in configuration file {path}{at}") from e

    # Workaround for https://github.com/pydantic/pydantic-settings/issues/259
    cli_config_class = create_cli_config_class(path)
    try:
        config = cli_config_class()
    except SettingsError as e:
        raise InvalidInput(_present_settings_error(e)) from e
    return config


def get_source_data(
    cli_config_path: Path | None = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return config values from each source with secrets and unknown fields masked."""

    try:
        env_data = dict(EnvSettingsSource(Config)())
    except Exception as exc:
        # Malformed complex env values (e.g. invalid JSON in HERMETO_HTTP)
        # must not prevent the config command from showing diagnostics.
        log.debug("Failed to parse environment settings: %s", exc)
        env_data = {}

    # Merge all file sources; later sources in the list have higher priority,
    # mirroring the priority order in settings_customise_sources.
    file_paths: list[Path | str] = list(CONFIG_FILE_PATHS)
    if cli_config_path is not None:
        file_paths.append(cli_config_path)

    merged_file_data: dict[str, Any] = {}
    for path in file_paths:
        try:
            source_data = dict(YamlConfigSettingsSource(Config, yaml_file=path)())
        except Exception as exc:
            # Non-existent or unreadable files are silently skipped.
            # Avoid logging the raw exception — YAML parser errors can
            # include file content snippets that may contain secrets.
            _, location = _extract_specific_from_yaml_error(exc)
            at = f" ({location})" if location else ""
            log.debug("Skipping config file %s%s: %s", path, at, type(exc).__name__)
            continue
        _deep_merge(merged_file_data, source_data)

    _mask_secrets(env_data, _SECRET_FIELDS)
    _mask_secrets(merged_file_data, _SECRET_FIELDS)

    if defaults is not None:
        _mask_unknown(env_data, defaults)
        _mask_unknown(merged_file_data, defaults)

    return {"env": env_data, "file": merged_file_data}


def _is_secret_type(ann: Any) -> bool:
    """Check whether a type annotation is or contains SecretStr."""
    if ann is SecretStr:
        return True
    args = getattr(ann, "__args__", None)
    if args:
        return any(a is SecretStr for a in args)
    return False


def _get_secret_fields(model: type[BaseModel]) -> dict[str, Any]:
    """Return a nested dict marking SecretStr fields as True."""
    result: dict[str, Any] = {}
    for name, field_info in model.model_fields.items():
        ann = field_info.annotation
        if _is_secret_type(ann):
            result[name] = True
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            sub = _get_secret_fields(ann)
            if sub:
                result[name] = sub
    return result


_UNKNOWN_PLACEHOLDER = "<unrecognized field>"
_SECRET_FIELDS = _get_secret_fields(Config)


def _mask_secrets(data: dict[str, Any], secret_fields: dict[str, Any]) -> None:
    """Replace SecretStr field values with a placeholder, in-place."""
    for key in data:
        if key not in secret_fields:
            continue
        if secret_fields[key] is True:
            data[key] = "**********"
        elif isinstance(data[key], dict) and isinstance(secret_fields[key], dict):
            _mask_secrets(data[key], secret_fields[key])


def _coerce_scalar_value(value: Any, default: Any) -> Any:
    """Coerce supported string scalar values to their schema default type.

    This keeps integer and Boolean environment values consistent with the schema
    while leaving all other raw configuration values unchanged.

    Ref: https://docs.pydantic.dev/latest/api/standard_library_types/#booleans
    """
    if not isinstance(value, str):
        return value
    if type(default) is bool:
        lowered = value.lower()
        if lowered in ("1", "true", "on", "yes", "y", "t"):
            return True
        if lowered in ("0", "false", "off", "no", "n", "f"):
            return False
        return value
    if type(default) is not int:
        return value
    try:
        return int(value)
    except ValueError:
        return value


def _mask_unknown(data: dict[str, Any], defaults: dict[str, Any]) -> None:
    """Mask unknown fields and coerce known fields to their schema type, in-place."""
    for key in list(data):
        if key not in defaults:
            if isinstance(data[key], dict):
                _mask_unknown(data[key], {})
            else:
                data[key] = _UNKNOWN_PLACEHOLDER
        elif isinstance(data[key], dict) and isinstance(defaults.get(key), dict):
            _mask_unknown(data[key], defaults[key])
        elif isinstance(data[key], dict):
            data[key] = _UNKNOWN_PLACEHOLDER
        else:
            # Env vars arrive as strings, coerce to the schema type so the output shows
            # e.g. concurrency_limit: 5 instead of '5', enabled: true instead of 'true'.
            data[key] = _coerce_scalar_value(data[key], defaults[key])


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Merge *override* into *base* in-place, recursing into nested dicts."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
