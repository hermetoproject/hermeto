# SPDX-License-Identifier: GPL-3.0-only
import logging
from io import StringIO

import pytest

from hermeto.interface.logging import (
    RESET,
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
            (logging.CRITICAL, "\033[1;31m"),
        ],
    )
    def test_all_levels_have_color_mapping(self, level: int, expected_color: str) -> None:
        fmt = ColoredFormatter(FMT, stream=FakeTTY())
        result = fmt.format(_make_record(level))
        level_name = logging.getLevelName(level)
        assert f"{expected_color}{level_name}{RESET}" in result

    @pytest.mark.parametrize(
        "color, stream, expect_color",
        [
            (None, FakeTTY(), True),
            (None, FakeNonTTY(), False),
            (None, None, False),
            (True, FakeNonTTY(), True),
            (False, FakeTTY(), False),
        ],
    )
    def test_color_mode(
        self, color: bool | None, stream: StringIO | None, expect_color: bool
    ) -> None:
        fmt = ColoredFormatter(FMT, stream=stream, color=color)
        result = fmt.format(_make_record(logging.INFO))
        if expect_color:
            assert f"\033[34mINFO{RESET}" in result
        else:
            assert "\033[" not in result
            assert "INFO" in result

    def test_does_not_mutate_original_record(self) -> None:
        fmt = ColoredFormatter(FMT, stream=FakeTTY(), color=True)
        record = _make_record(logging.ERROR)
        original_levelname = record.levelname
        fmt.format(record)
        assert record.levelname == original_levelname

    def test_message_is_not_colorized(self) -> None:
        fmt = ColoredFormatter(FMT, stream=FakeTTY(), color=True)
        result = fmt.format(_make_record(logging.INFO, "hello world"))
        assert result.endswith("hello world")
