import enum
import logging
from typing import Iterable

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class LogLevel(str, enum.Enum):
    """Valid log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def setup_logging(level: LogLevel, additional_modules: Iterable[str] = ()) -> None:
    """Set up logging. By default, enables only the application root logger."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))

    for module in ["hermeto", *additional_modules]:
        logger = logging.getLogger(module)
        logger.setLevel(level.value)

        if not logger.hasHandlers():
            logger.addHandler(handler)
