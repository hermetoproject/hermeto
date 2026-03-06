# SPDX-License-Identifier: GPL-3.0-only
import logging
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import AfterValidator, BaseModel, HttpUrl, ValidationError, model_validator
from pydantic_core import ErrorDetails
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)
from typing_extensions import Self

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


class PipSettings(BaseModel, extra="forbid"):
    """Settings for Pip."""

    # This setting exists for legacy use-cases only and must not be relied upon
    ignore_dependencies_crates: bool = False


class YarnSettings(BaseModel, extra="forbid"):
    """Settings for Yarn v2+."""

    # This setting exists for legacy use-cases only and must not be relied upon
    enabled: bool = True


class GomodSettings(BaseModel, extra="forbid"):
    """Settings for Go modules."""

    proxy_url: str = "https://proxy.golang.org,direct"
    download_max_tries: int = 5
    environment_variables: dict[str, str] = {}


class HttpSettings(BaseModel, extra="forbid"):
    """HTTP-related settings."""

    connect_timeout: int = 30
    read_timeout: int = 300


class RuntimeSettings(BaseModel, extra="forbid"):
    """General runtime execution settings."""

    # This is how an environment variable name should look like:
    #   HERMETO_RUNTIME__CONCURRENCY_LIMIT
    # Note single underscore after application name, then name of the section
    # as it appears in Config class definition, then double underscore and
    # field name after that.
    subprocess_timeout: int = 3600
    concurrency_limit: int = 5


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
    proxy_password: str | None = None

    @model_validator(mode="after")
    def _validate_login_and_password_both_set(self) -> Self:
        if self.proxy_login is not None and self.proxy_password is None:
            raise InvalidInput("Proxy password must be set when proxy login is set")
        if self.proxy_login is None and self.proxy_password is not None:
            raise InvalidInput("Proxy login must be set when proxy password is set")
        return self

    @model_validator(mode="after")
    def _validate_proxy_url_is_set_when_proxy_credentials_are_set(self) -> Self:
        if self.proxy_login is not None and self.proxy_url is None:
            raise InvalidInput("Proxy URL must be set when proxy credentials are set")
        return self


class NpmSettings(ProxyMixin, extra="forbid"):
    """Npm settings."""


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

    pip: PipSettings = PipSettings()
    yarn: YarnSettings = YarnSettings()
    npm: NpmSettings = NpmSettings()
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


def get_config() -> Config:
    """Get the configuration singleton."""
    global config

    if not config:
        try:
            config = Config()
        except ValidationError as e:
            raise InvalidInput(_present_config_error(e)) from e

    return config


def set_config(path: Path) -> Config:
    """Set global config variable using input from file."""
    global config
    # Validate beforehand for a friendlier error message: https://github.com/pydantic/pydantic-settings/pull/432
    try:
        Config.model_validate(yaml.safe_load(path.read_text()))
    except ValidationError as e:
        raise InvalidInput(_present_config_error(e)) from e

    # Workaround for https://github.com/pydantic/pydantic-settings/issues/259
    cli_config_class = create_cli_config_class(path)
    config = cli_config_class()
    return config
