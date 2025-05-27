import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from hermeto import APP_NAME
from hermeto.core.models.input import parse_user_input

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
        settings_cls: type[BaseSettings],  # noqa: ARG003
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Control allowed settings sources and priority.

        Priority (highest to lowest): init_settings (for programmatic/test overrides)

        https://docs.pydantic.dev/2.11/concepts/pydantic_settings/#customise-settings-sources
        """
        return (init_settings,)


def get_config() -> Config:
    """Get the configuration singleton."""
    global config

    if not config:
        config = Config()

    return config


def set_config(path: Path) -> None:
    """Set global config variable using input from file."""
    global config

    config = parse_user_input(Config.model_validate, yaml.safe_load(path.read_text()))
