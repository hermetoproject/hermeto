from typing import Any

import pytest

from hermeto.core.errors import UnexpectedFormat
from hermeto.core.package_managers.cargo.main import (
    CargoPackage,
    _resolve_main_package,
    _sanitized_cargo_config_file,
)
from hermeto.core.rooted_path import RootedPath


def write_cargo_toml(rooted_path: RootedPath, content: str) -> None:
    (rooted_path.path / "Cargo.toml").write_text(content)


def test_standard_package_with_name_and_version(rooted_tmp_path: RootedPath) -> None:
    write_cargo_toml(
        rooted_tmp_path,
        """
        [package]
        name = "my-project"
        version = "1.2.3"
        """,
    )

    name, version = _resolve_main_package(rooted_tmp_path)
    assert name == "my-project"
    assert version == "1.2.3"


def test_virtual_workspace_with_workspace_package_version(rooted_tmp_path: RootedPath) -> None:
    write_cargo_toml(
        rooted_tmp_path,
        """
        [workspace]
        members = ["a", "b", "c"]

        [workspace.package]
        version = "1.2.3"
        """,
    )

    expected_name = rooted_tmp_path.path.name
    name, version = _resolve_main_package(rooted_tmp_path)
    assert name == expected_name
    assert version == "1.2.3"


def test_virtual_workspace_without_workspace_version(rooted_tmp_path: RootedPath) -> None:
    write_cargo_toml(
        rooted_tmp_path,
        """
        [workspace]
        members = ["a", "b", "c"]
        """,
    )

    expected_name = rooted_tmp_path.path.name
    name, version = _resolve_main_package(rooted_tmp_path)
    assert name == expected_name
    assert version is None


@pytest.mark.parametrize(
    "pkg, expected_purl",
    [
        pytest.param(
            {
                "name": "foo",
                "version": "0.1.0",
            },
            "pkg:cargo/foo@0.1.0",
            id="simple_package",
        ),
        pytest.param(
            {
                "name": "foo",
                "version": "0.1.0",
                "source": "registry+https://github.com/rust-lang/crates.io-index",
                "checksum": "abc123",
            },
            "pkg:cargo/foo@0.1.0?checksum=abc123",
            id="package_with_registry_source_and_checksum",
        ),
        pytest.param(
            {
                "name": "foo",
                "version": "0.1.0",
                "source": "git+https://github.com/rust-random/rand?rev=abc123#abc123",
            },
            "pkg:cargo/foo@0.1.0?vcs_url=git%2Bhttps://github.com/rust-random/rand%40abc123",
            id="package_with_git_source",
        ),
        pytest.param(
            {
                "name": "substrate-frame",
                "version": "1.0.0",
                "source": "registry+https://github.com/paritytech/crates-parity-io",
                "checksum": "def456",
            },
            "pkg:cargo/substrate-frame@1.0.0?checksum=def456&repository_url=https://github.com/paritytech/crates-parity-io",
            id="package_from_alternative_git_registry",
        ),
        pytest.param(
            {
                "name": "company-crate",
                "version": "2.0.0",
                "source": "registry+sparse+https://cargo.cloudsmith.io/my-org/my-repo/",
                "checksum": "ghi789",
            },
            "pkg:cargo/company-crate@2.0.0?checksum=ghi789&repository_url=sparse%2Bhttps://cargo.cloudsmith.io/my-org/my-repo/",
            id="package_from_sparse_registry",
        ),
        pytest.param(
            {
                "name": "internal-lib",
                "version": "3.1.4",
                "source": "registry+https://artifactory.company.com/artifactory/api/cargo/cargo-local",
            },
            "pkg:cargo/internal-lib@3.1.4?repository_url=https://artifactory.company.com/artifactory/api/cargo/cargo-local",
            id="package_from_corporate_registry_without_checksum",
        ),
        pytest.param(
            {
                "name": "crates-io-package",
                "version": "1.5.0",
                "source": "registry+https://github.com/rust-lang/crates.io-index",
            },
            "pkg:cargo/crates-io-package@1.5.0",
            id="crates_io_package_without_repository_url",
        ),
    ],
)
def test_cargo_package_purl_generation(pkg: dict[str, Any], expected_purl: str) -> None:
    package = CargoPackage(**pkg)
    assert package.purl.to_string() == expected_purl


def test_sanitized_cargo_config_file_raises_unexpected_format_on_invalid_config(
    rooted_tmp_path: RootedPath,
) -> None:
    """Test that UnexpectedFormat is raised when config file processing fails."""
    cargo_dir = rooted_tmp_path.path / ".cargo"
    cargo_dir.mkdir()

    # Create a config where registries is a list instead of a table (causes .items() to fail)
    (cargo_dir / "config.toml").write_text('registries = ["not", "a", "table"]')

    with pytest.raises(UnexpectedFormat, match="containing invalid data, cannot read registries"):
        with _sanitized_cargo_config_file(rooted_tmp_path):
            pass


def test_sanitized_cargo_config_file_extracts_and_sanitizes_registries(
    rooted_tmp_path: RootedPath,
) -> None:
    """Test that sanitized config contains only registry information."""
    cargo_dir = rooted_tmp_path.path / ".cargo"
    cargo_dir.mkdir()

    # Create a config with registries and other potentially insecure settings
    config_content = """
[registries]
parity = { index = "https://github.com/paritytech/crates-parity-io" }
internal = { index = "sparse+https://artifactory.company.com/cargo" }

[net]
git-fetch-with-cli = true

[build]
rustflags = ["--cfg", "feature=\\"unsafe\\""]
"""
    (cargo_dir / "config.toml").write_text(config_content)

    with _sanitized_cargo_config_file(rooted_tmp_path):
        # Verify sanitized config exists and contains only registries
        sanitized_config_path = cargo_dir / "config.toml"
        assert sanitized_config_path.exists()

        sanitized_content = sanitized_config_path.read_text()
        # Check for registry sections in TOML table format
        assert "registries.parity" in sanitized_content or "[registries]" in sanitized_content
        assert "parity" in sanitized_content
        assert "internal" in sanitized_content
        assert "https://github.com/paritytech/crates-parity-io" in sanitized_content
        assert "sparse+https://artifactory.company.com/cargo" in sanitized_content
        # Verify insecure settings are NOT present
        assert "[net]" not in sanitized_content
        assert "[build]" not in sanitized_content
        assert "rustflags" not in sanitized_content

    # Verify original config is restored after exiting context
    restored_content = (cargo_dir / "config.toml").read_text()
    assert "[net]" in restored_content
    assert "[build]" in restored_content
    assert "rustflags" in restored_content


def test_sanitized_cargo_config_file_handles_no_registries(
    rooted_tmp_path: RootedPath,
) -> None:
    """Test that config without registries is simply removed during sanitization."""
    cargo_dir = rooted_tmp_path.path / ".cargo"
    cargo_dir.mkdir()

    # Create a config without registries
    config_content = """
[build]
target = "x86_64-unknown-linux-gnu"
"""
    (cargo_dir / "config.toml").write_text(config_content)

    with _sanitized_cargo_config_file(rooted_tmp_path):
        # Config file should not exist (removed and not replaced with sanitized version)
        sanitized_config_path = cargo_dir / "config.toml"
        assert not sanitized_config_path.exists()

    # Verify original config is restored
    restored_content = (cargo_dir / "config.toml").read_text()
    assert "[build]" in restored_content
