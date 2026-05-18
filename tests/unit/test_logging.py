# SPDX-License-Identifier: GPL-3.0-only
import logging
from io import StringIO

import pytest

from hermeto.interface.logging import (
    LEVEL_COLORS,
    RESET,
    ColorMode,
    ColoredFormatter,
)


class FakeTTY(StringIO):
    def isatty(self) -> bool:
        return True


class FakeNonTTY(StringIO):
    def isatty(self) -> bool:
        return False


def _make_record(level: int, message: str = "test message") -> logging.LogRecord:
    return logging.LogRecord("test", level, "", 0, message, (), None)


FMT = "%(levelname)s %(message)s"


class TestColoredFormatter:
    @pytest.mark.parametrize(
        "level, expected_color",
        [
            (logging.DEBUG, "\033[90m"),
            (logging.INFO, "\033[34m"),
            (logging.WARNING, "\033[33m"),
            (logging.ERROR, "\033[31m"),
            (logging.CRITICAL, "\033[31m"),
        ],
    )
    def test_auto_mode_tty_colorizes_level(
        self, level: int, expected_color: str
    ) -> None:
        fmt = ColoredFormatter(FMT, stream=FakeTTY(), color_mode=ColorMode.AUTO)
        result = fmt.format(_make_record(level))
        level_name = logging.getLevelName(level)
        assert f"{expected_color}{level_name}{RESET}" in result

    def test_auto_mode_non_tty_no_color(self) -> None:
        fmt = ColoredFormatter(FMT, stream=FakeNonTTY(), color_mode=ColorMode.AUTO)
        result = fmt.format(_make_record(logging.WARNING))
        assert "\033[" not in result
        assert "WARNING" in result

    def test_on_mode_forces_color_without_tty(self) -> None:
        fmt = ColoredFormatter(FMT, stream=FakeNonTTY(), color_mode=ColorMode.ON)
        result = fmt.format(_make_record(logging.INFO))
        assert f"\033[34mINFO{RESET}" in result

    def test_off_mode_disables_color_with_tty(self) -> None:
        fmt = ColoredFormatter(FMT, stream=FakeTTY(), color_mode=ColorMode.OFF)
        result = fmt.format(_make_record(logging.ERROR))
        assert "\033[" not in result
        assert "ERROR" in result

    def test_does_not_mutate_original_record(self) -> None:
        fmt = ColoredFormatter(FMT, stream=FakeTTY(), color_mode=ColorMode.ON)
        record = _make_record(logging.ERROR)
        original_levelname = record.levelname
        fmt.format(record)
        assert record.levelname == original_levelname

    def test_message_is_not_colorized(self) -> None:
        fmt = ColoredFormatter(FMT, stream=FakeTTY(), color_mode=ColorMode.ON)
        result = fmt.format(_make_record(logging.INFO, "hello world"))
        assert result.endswith("hello world")

    def test_auto_mode_no_stream(self) -> None:
        fmt = ColoredFormatter(FMT, stream=None, color_mode=ColorMode.AUTO)
        result = fmt.format(_make_record(logging.INFO))
        assert "\033[" not in result

    @pytest.mark.parametrize("level", list(LEVEL_COLORS.keys()))
    def test_all_levels_have_color_mapping(self, level: int) -> None:
        assert level in LEVEL_COLORS
