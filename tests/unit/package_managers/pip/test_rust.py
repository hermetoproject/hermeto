from pathlib import Path

import pytest

from hermeto.core.package_managers.pip import rust
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


def test_filter_packages_with_rust_code_warns_on_missing_cargo_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    package_path = tmp_path / "foo-1.0.0.tar.gz"
    package_path.touch()

    def _mock_unpack_archive(
        filename: Path, extract_dir: str | Path, filter: str | None = None
    ) -> None:
        _ = filename, filter
        extract_path = Path(extract_dir)
        source_dir = extract_path / "foo-1.0.0"
        source_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(rust.shutil, "unpack_archive", _mock_unpack_archive)
    monkeypatch.setattr(rust, "_depends_on_rust", lambda _source_dir: True)
    caplog.set_level("WARNING", logger=rust.log.name)

    packages = rust.filter_packages_with_rust_code([{"path": package_path}])

    assert packages == []
    assert f"package {package_path.name} without Cargo.lock" in caplog.text
    assert not (tmp_path / "foo-1.0.0").exists()


def test_filter_packages_with_rust_code_skips_non_rust_without_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    package_path = tmp_path / "foo-1.0.0.tar.gz"
    package_path.touch()

    def _mock_unpack_archive(
        filename: Path, extract_dir: str | Path, filter: str | None = None
    ) -> None:
        _ = filename, filter
        extract_path = Path(extract_dir)
        source_dir = extract_path / "foo-1.0.0"
        source_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(rust.shutil, "unpack_archive", _mock_unpack_archive)
    monkeypatch.setattr(rust, "_depends_on_rust", lambda _source_dir: False)
    caplog.set_level("WARNING", logger=rust.log.name)

    packages = rust.filter_packages_with_rust_code([{"path": package_path}])

    assert packages == []
    assert "without Cargo.lock" not in caplog.text
