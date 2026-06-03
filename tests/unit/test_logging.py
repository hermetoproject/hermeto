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
        "color, stream, env, expect_color",
        [
            (None, FakeTTY(), {}, True),
            (None, FakeNonTTY(), {}, False),
            (None, None, {}, False),
            (True, FakeNonTTY(), {}, True),
            (False, FakeTTY(), {}, False),
            (None, FakeTTY(), {"NO_COLOR": "1"}, False),
            (None, FakeNonTTY(), {"FORCE_COLOR": "1"}, True),
            (None, FakeNonTTY(), {"NO_COLOR": "1", "FORCE_COLOR": "1"}, True),
            (None, FakeTTY(), {"NO_COLOR": ""}, True),
            (True, FakeNonTTY(), {"NO_COLOR": "1"}, True),
            (False, FakeTTY(), {"FORCE_COLOR": "1"}, False),
        ],
    )
    def test_color_mode(
        self,
        color: bool | None,
        stream: StringIO | None,
        env: dict[str, str],
        expect_color: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for var in ("NO_COLOR", "FORCE_COLOR"):
            monkeypatch.delenv(var, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
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
