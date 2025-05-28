# Needed due to https://github.com/python/mypy/issues/17535
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Type

import yaml
from pydantic import model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from hermeto import APP_NAME
from hermeto.core.models.input import parse_user_input

CONFIG_FILE_PATHS = [f"~/.config/{APP_NAME.lower()}/config.yaml", "config.yaml"]
log = logging.getLogger(__name__)
config = None


class Config(BaseSettings):
    """Singleton that provides default configuration for the application process."""

    model_config = SettingsConfigDict(env_prefix=f"{APP_NAME.upper()}_", extra="forbid")

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
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Control allowed settings sources and priority.

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


def create_cli_config_class(config_path: Path) -> Type[Config]:
    """Return a subclass of Config that uses the CLI YAML file input.

    This is necessary because the path of the YAML config file from the CLI is not known
    ahead of time: https://github.com/pydantic/pydantic-settings/issues/259
    """

    class CLIConfig(Config):
        """A subclass of Config that uses the CLI YAML file input."""

        model_config = SettingsConfigDict(
            env_prefix=f"{APP_NAME.upper()}_", extra="forbid", yaml_file=config_path
        )

    return CLIConfig


def get_config() -> Config:
    """Get the configuration singleton."""
    global config

    if not config:
        config = Config()

    return config


def set_config(path: Path) -> None:
    """Set global config variable using input from file."""
    global config
    # Validate beforehand for a friendlier error message: https://github.com/pydantic/pydantic-settings/pull/432
    parse_user_input(Config.model_validate, yaml.safe_load(path.read_text()))
    # Workaround for https://github.com/pydantic/pydantic-settings/issues/259
    cli_config_class = create_cli_config_class(path)
    config = cli_config_class()
