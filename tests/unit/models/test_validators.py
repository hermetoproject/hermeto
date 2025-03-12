from typing import Any

import pytest

from hermeto.core.models.validators import unique, unique_sorted

DATA_WIHOUT_DUPES = [
    {"a": 2, "b": 1},
    {"a": 1, "b": 1},
]
DATA_WITH_DUPES = [*DATA_WIHOUT_DUPES, {"a": 1, "b": 1}]
DATA_WITH_CONFLICT = [*DATA_WIHOUT_DUPES, {"a": 1, "b": 2}]


def unique_by(x: dict) -> int:
    return x["a"]


def test_make_unique() -> None:
    orig_data = DATA_WITH_DUPES.copy()

    assert unique(DATA_WITH_DUPES, by=unique_by) == [
        {"a": 2, "b": 1},
        {"a": 1, "b": 1},
    ]
    assert unique_sorted(DATA_WITH_DUPES, by=unique_by) == [
        {"a": 1, "b": 1},
        {"a": 2, "b": 1},
    ]

    assert DATA_WITH_DUPES == orig_data


def test_check_unique() -> None:
    assert unique(DATA_WIHOUT_DUPES, by=unique_by, dedupe=False) == DATA_WIHOUT_DUPES
    assert unique_sorted(DATA_WIHOUT_DUPES, by=unique_by, dedupe=False) == [
        {"a": 1, "b": 1},
        {"a": 2, "b": 1},
    ]


def test_uniqueness_conflicts() -> None:
    def assert_raises(fn: Any, data: Any, dedupe: Any) -> None:
        with pytest.raises(ValueError):
            fn(data, by=unique_by, dedupe=dedupe)

    assert_raises(unique, DATA_WITH_CONFLICT, dedupe=True)
    assert_raises(unique_sorted, DATA_WITH_CONFLICT, dedupe=True)

    assert_raises(unique, DATA_WITH_DUPES, dedupe=False)
    assert_raises(unique_sorted, DATA_WITH_DUPES, dedupe=False)

    assert_raises(unique, DATA_WITH_CONFLICT, dedupe=False)
    assert_raises(unique_sorted, DATA_WITH_CONFLICT, dedupe=False)
