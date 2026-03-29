# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound, UnsupportedFeature
from hermeto.core.models.input import Request
from hermeto.core.package_managers.uv.lockfile import parse_uv_lockfile
from hermeto.core.package_managers.uv.main import fetch_uv_source
from hermeto.core.rooted_path import RootedPath


def test_parse_uv_lockfile_missing(tmp_path: Path) -> None:
    with pytest.raises(LockfileNotFound, match=r"Required files not found"):
        parse_uv_lockfile(RootedPath(tmp_path))


def test_parse_uv_lockfile_invalid_toml(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("not = [valid")

    with pytest.raises(InvalidLockfileFormat, match=r"uv.lock"):
        parse_uv_lockfile(RootedPath(tmp_path))


def test_parse_uv_lockfile_missing_version(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("[[package]]\nname = \"foo\"\n")

    with pytest.raises(InvalidLockfileFormat, match=r"version"):
        parse_uv_lockfile(RootedPath(tmp_path))


def test_parse_uv_lockfile_valid(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("version = 1\n")

    parsed = parse_uv_lockfile(RootedPath(tmp_path))
    assert parsed["version"] == 1


def test_fetch_uv_source_currently_not_implemented(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("version = 1\n")

    request = Request(
        source_dir=tmp_path,
        output_dir=tmp_path,
        packages=[{"type": "x-uv"}],
    )

    with pytest.raises(UnsupportedFeature, match=r"x-uv"):
        fetch_uv_source(request)
