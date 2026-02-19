# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from hermeto.core.package_managers.pip.rust import _get_rust_root_dir


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
    inferred_rust_root_dir = _get_rust_root_dir(cargo_files)

    assert inferred_rust_root_dir == expected_rust_root_dir
