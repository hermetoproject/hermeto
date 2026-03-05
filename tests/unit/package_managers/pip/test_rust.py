# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from hermeto.core.package_managers.pip.rust import _get_rust_root_dir, _shortest_path_parent


@pytest.mark.parametrize(
    "cargo_files,expected_rust_root_dir",
    [
        pytest.param(
            (Path("/tmp/foo/Cargo.toml"), Path("/tmp/bar/baz/Cargo.toml")),
            Path("/tmp/foo"),
            id="simple_ordering",
        ),
        pytest.param(
            (Path("/tmp/bar/baz/Cargo.toml"), Path("/tmp/foo/Cargo.toml")),
            Path("/tmp/foo"),
            id="reversed_simple_ordering",
        ),
        pytest.param(
            (
                Path("/tmp/bar/baz/Cargo.toml"),
                Path("/tmp/foo/Cargo.toml"),
                Path("/tmp/foo/quux/Cargo.toml"),
            ),
            Path("/tmp/foo"),
            id="tricky_ordering",
        ),
    ],
)
def test_the_shortest_path_in_cargo_package_is_inferred_as_root(
    cargo_files: tuple, expected_rust_root_dir: Path
) -> None:
    inferred_rust_root_dir = _shortest_path_parent(cargo_files)
    assert inferred_rust_root_dir == expected_rust_root_dir


def test_get_rust_root_dir_returns_none_if_no_rust_files_exist(tmp_path: Path) -> None:
    assert _get_rust_root_dir(tmp_path) is None


def test_get_rust_root_dir_falls_back_to_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").touch()
    assert _get_rust_root_dir(tmp_path) == tmp_path


def test_get_rust_root_dir_prefers_cargo_lock_over_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").touch()

    subdir = tmp_path / "workspace-package"
    subdir.mkdir()

    (subdir / "Cargo.lock").touch()
    (subdir / "Cargo.toml").touch()
    assert _get_rust_root_dir(tmp_path) == subdir
