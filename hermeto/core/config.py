import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError, model_validator
from pydantic_core import ErrorDetails
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from hermeto import APP_NAME
from hermeto.core.errors import InvalidInput

log = logging.getLogger(__name__)
config = None


class Config(BaseSettings):
    """Singleton that provides default configuration for the application process."""

    model_config = SettingsConfigDict(extra="forbid")

    goproxy_url: str = "https://proxy.golang.org,direct"
    default_environment_variables: dict = {}
    gomod_download_max_tries: int = 5
    gomod_strict_vendor: bool = True
    subprocess_timeout: int = 3600

    # matches aiohttp default timeout:
    # https://docs.aiohttp.org/en/v3.9.5/client_reference.html#aiohttp.ClientSession
    requests_timeout: int = 300
    concurrency_limit: int = 5

    # The flags below are for legacy use-cases compatibility only, must not be
    # relied upon and will be eventually removed.
    allow_yarnberry_processing: bool = True
    ignore_pip_dependencies_crates: bool = False

    @model_validator(mode="before")
    @classmethod
    def _print_deprecation_warning(cls, data: Any) -> Any:
        if "gomod_strict_vendor" in data:
            log.warning(
                "The `gomod_strict_vendor` config option is deprecated and will be removed in "
                f"future versions. Note that it no longer has any effect when set, {APP_NAME} will "
                "always check the vendored contents and fail if they are not up-to-date."
            )

        return data

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Control allowed settings sources and priority.

        Priority (highest to lowest): init_settings (for programmatic/test overrides),
        CLI config file.

        https://docs.pydantic.dev/2.11/concepts/pydantic_settings/#customise-settings-sources
        """
        return (
            init_settings,
            YamlConfigSettingsSource(
                settings_cls
            ),  # The CLI config path from yaml_file in model_config
        )


def create_cli_config_class(config_path: Path) -> type[Config]:
    """Return a subclass of Config that uses the CLI YAML file input.

    This is necessary because the path of the YAML config file from the CLI is not known
    ahead of time: https://github.com/pydantic/pydantic-settings/issues/259
    """

    class CLIConfig(Config):
        """A subclass of Config that uses the CLI YAML file input."""

        model_config = SettingsConfigDict(extra="forbid", yaml_file=config_path)

    return CLIConfig


def _present_config_error(validation_error: ValidationError) -> str:
    """Format validation errors for configuration sources"""
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
        f"  - CLI --config-file option"
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


def set_config(path: Path) -> None:
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
