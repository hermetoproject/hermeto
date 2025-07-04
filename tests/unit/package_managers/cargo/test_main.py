from typing import Any

import pytest

from hermeto.core.package_managers.cargo.main import CargoPackage, _resolve_main_package
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
    ],
)
def test_cargo_package_purl_generation(pkg: dict[str, Any], expected_purl: str) -> None:
    package = CargoPackage(**pkg)
    assert package.purl.to_string() == expected_purl
