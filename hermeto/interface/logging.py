# SPDX-License-Identifier: GPL-3.0-only
import enum
import logging
from collections.abc import Iterable
from typing import Any

from hermeto.core.constants import Mode

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class LogLevel(str, enum.Enum):
    """Valid log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class EnforcingModeLoggerAdapter(logging.LoggerAdapter):
    """
    Enforcing mode aware logger adapter.

    This adapter is to be used as the logger wrapper providing functionality to
    decide whether to log a warning or an error based on context and on the CLI mode setting.
    """

    def error_or_warn(self, msg: str, *args: Any, enforcing_mode: Mode, **kwargs: Any) -> None:
        """
        Log an error or a warning based on the CLI enforcing mode setting.

        We don't want all errors converted to warnings, most of them will always
        be fatal, so this warning/error wrapper is just an addition to the standard set of logger
        methods for cases where context is the decisive factor.
        """
        # NOTE: We should probably drop the enforcing_mode argument in favour of e.g. a Singleton
        # settings instance.
        msg = f"[mode:{str(enforcing_mode).upper()}] {msg}"
        if enforcing_mode == Mode.PERMISSIVE:
            self.warning(msg, *args, **kwargs)
        else:
            self.error(msg, *args, **kwargs)


def setup_logging(level: LogLevel, additional_modules: Iterable[str] = ()) -> None:
    """Set up logging. By default, enables only the application root logger."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))

    for module in ["hermeto", *additional_modules]:
        logger = logging.getLogger(module)
        logger.setLevel(level.value)

        if not logger.hasHandlers():
            logger.addHandler(handler)
