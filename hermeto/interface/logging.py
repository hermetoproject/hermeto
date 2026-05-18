# SPDX-License-Identifier: GPL-3.0-only
import enum
import logging
from collections.abc import Iterable
from typing import Any

from hermeto.core.constants import Mode

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[90m",  # gray
    logging.INFO: "\033[34m",  # blue
    logging.WARNING: "\033[33m",  # orange/yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[31m",  # red
}
RESET = "\033[0m"


class ColorMode(str, enum.Enum):
    """Control whether log output uses colors."""

    AUTO = "auto"
    ON = "on"
    OFF = "off"


class ColoredFormatter(logging.Formatter):
    """Formatter that colorizes the log level name when the output stream supports color."""

    def __init__(self, fmt: str, stream: object = None, color_mode: ColorMode = ColorMode.AUTO) -> None:
        super().__init__(fmt)
        if color_mode == ColorMode.ON:
            self._use_color = True
        elif color_mode == ColorMode.OFF:
            self._use_color = False
        else:
            self._use_color = hasattr(stream, "isatty") and stream.isatty()

    def format(self, record: logging.LogRecord) -> str:
        if self._use_color:
            color = LEVEL_COLORS.get(record.levelno, "")
            record = logging.makeLogRecord(record.__dict__)
            record.levelname = f"{color}{record.levelname}{RESET}"
        return super().format(record)


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


def setup_logging(
    level: LogLevel,
    color_mode: ColorMode = ColorMode.AUTO,
    additional_modules: Iterable[str] = (),
) -> None:
    """Set up logging. By default, enables only the application root logger."""
    handler = logging.StreamHandler()
    handler.setFormatter(ColoredFormatter(LOG_FORMAT, stream=handler.stream, color_mode=color_mode))

    for module in ["hermeto", *additional_modules]:
        logger = logging.getLogger(module)
        logger.setLevel(level.value)

        if not logger.hasHandlers():
            logger.addHandler(handler)
