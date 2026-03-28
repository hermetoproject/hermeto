# SPDX-License-Identifier: GPL-3.0-only
import textwrap
from typing import Any
from unittest.mock import patch

import pytest
import tomlkit
from packageurl import PackageURL

from hermeto.core.errors import UnexpectedFormat
from hermeto.core.package_managers.cargo.main import (
    CargoPackage,
    _generate_sbom_components,
    _resolve_main_package,
    _sanitize_cargo_config,
    _use_vendored_sources,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID


def write_cargo_toml(rooted_path: RootedPath, content: str) -> None:
    (rooted_path.path / "Cargo.toml").write_text(content)


def write_cargo_lock(rooted_path: RootedPath, packages: list[dict]) -> None:
    lines = ["version = 4", ""]
    for pkg in packages:
        lines.append("[[package]]")
        for key, value in pkg.items():
            lines.append(f'{key} = "{value}"')
        lines.append("")
    (rooted_path.path / "Cargo.lock").write_text("\n".join(lines))


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
                "name": "foo",
                "version": "0.1.0",
                "source": "registry+https://my-registry.example.com/index",
            },
            "pkg:cargo/foo@0.1.0?repository_url=https://my-registry.example.com/index",
            id="package_with_alternate_registry",
        ),
        pytest.param(
            {
                "name": "foo",
                "version": "0.1.0",
                "source": "registry+https://my-crates.io-mirror.example.com/index",
                "checksum": "abc123",
            },
            "pkg:cargo/foo@0.1.0?checksum=abc123",
            id="package_with_crates_io_in_subdomain_no_repository_url",
        ),
    ],
)
def test_cargo_package_purl_generation(pkg: dict[str, Any], expected_purl: str) -> None:
    package = CargoPackage(**pkg)
    assert package.purl.to_string() == expected_purl


@pytest.mark.parametrize(
    "config_input, expected_registries",
    [
        pytest.param(
            """
            [registries.example-registry]
            index = "https://my-registry.example.com:8080/index"

            """,
            textwrap.dedent(
                """
                [registries.example-registry]
                index = "https://my-registry.example.com:8080/index"
                """,
            ).lstrip(),
            id="single_registries_with_only_safe_fields",
        ),
        pytest.param(
            """
            [registries.my-registry]
            index =     "https://my-intranet:8080/git/index"
            token =     "secret-token"
            credential-provider = "cargo:token"
            dangerous-field = "should-be-removed"

            [registries.other-registry]
            index = "https://other.example.com/index"
            custom-field = "should-be-removed"

            [build]
            jobs = 4
            """,
            textwrap.dedent(
                """
                [registries.my-registry]
                index = "https://my-intranet:8080/git/index"
                token = "secret-token"
                credential-provider = "cargo:token"

                [registries.other-registry]
                index = "https://other.example.com/index"
                """
            ).lstrip(),
            id="multiple_registries_with_safe_and_unsafe_fields",
        ),
    ],
)
def test_cargo_config_with_correctly_defined_registries(
    config_input: str, expected_registries: str
) -> None:
    result = _sanitize_cargo_config(config_input)
    assert result == expected_registries


@pytest.mark.parametrize(
    "config_input",
    [
        pytest.param(
            """
            [registries]
            """,
            id="single_invalid_registries_with_no_index",
        ),
        pytest.param(
            """
            [registries.example-registry]
            """,
            id="single_invalid_registries_with_no_value",
        ),
        pytest.param(
            """
            [build]
            jobs = 4

            [net]
            git-fetch-with-cli = true
            """,
            id="no_registries_section",
        ),
        pytest.param(
            "",
            id="empty_config",
        ),
    ],
)
def test_cargo_config_without_registries_gets_sanitized(config_input: str) -> None:
    result = _sanitize_cargo_config(config_input)
    assert result == ""


@pytest.mark.parametrize(
    "invalid_config",
    [
        pytest.param(
            """
            [registries.my-registry
            index = "https://example.com"
            """,
            id="malformed_toml_missing_closing_bracket",
        ),
        pytest.param(
            """
            [registries.my-registry]
            index = "https://example.com"
            token = [this is invalid without quotes
            """,
            id="malformed_toml_invalid_array_syntax",
        ),
    ],
)
def test_sanitize_cargo_config_raises_unexpected_format(invalid_config: str) -> None:
    with pytest.raises(UnexpectedFormat):
        _sanitize_cargo_config(invalid_config)


@pytest.mark.parametrize(
    "existing_config, expected_keys",
    [
        pytest.param(
            None,
            ["source"],
            id="no_existing_config",
        ),
        pytest.param(
            """
            [build]
            target = "x86_64-unknown-linux-gnu"

            [net]
            retry = 3
            """,
            ["build", "net", "source"],
            id="existing_config_is_preserved",
        ),
    ],
)
def test_use_vendored_sources(
    rooted_tmp_path: RootedPath,
    existing_config: str | None,
    expected_keys: list[str],
) -> None:
    config_template = {
        "source": {
            "crates-io": {"replace-with": "vendored-sources"},
            "vendored-sources": {"directory": "${output_dir}/deps/cargo"},
        }
    }
    cargo_dir = rooted_tmp_path.path / ".cargo"
    cargo_dir.mkdir()

    if existing_config is not None:
        (cargo_dir / "config.toml").write_text(textwrap.dedent(existing_config))

    result = _use_vendored_sources(rooted_tmp_path, config_template)
    result_toml = tomlkit.loads(result.template).unwrap()

    for key in expected_keys:
        assert key in result_toml, f"[{key}] section was silently dropped"

    assert result_toml["source"]["crates-io"]["replace-with"] == "vendored-sources"
    assert result_toml["source"]["vendored-sources"]["directory"] == "${output_dir}/deps/cargo"


MOCK_REPO_ID = RepoID("ssh://git@github.com/test/repo.git", "abc123")


def test_workspace_members_get_local_package_treatment(
    rooted_tmp_path: RootedPath,
) -> None:
    write_cargo_toml(
        rooted_tmp_path,
        """
        [package]
        name = "bug"
        version = "0.1.0"
        [workspace]
        members = ["","a", "b"]
    """,
    )
    write_cargo_lock(
        rooted_tmp_path,
        [
            {"name": "a", "version": "0.0.0"},
            {"name": "b", "version": "0.0.0"},
            {"name": "bug", "version": "0.1.0"},
            {
                "name": "mock",
                "version": "0.0.1",
                "source": "registry+https://github.com/rust-lang/crates.io-index",
                "checksum": "abc123",
            },
        ],
    )

    with patch(
        "hermeto.core.package_managers.cargo.main.get_repo_id",
        return_value=MOCK_REPO_ID,
    ):
        components = _generate_sbom_components(rooted_tmp_path)

    by_name = {c.name: PackageURL.from_string(c.purl) for c in components}

    assert "vcs_url" in by_name["a"].qualifiers
    assert by_name["a"].subpath == "a"

    assert "vcs_url" in by_name["b"].qualifiers
    assert by_name["b"].subpath == "b"

    # registry package must have no vcs_url and no subpath
    assert "vcs_url" not in by_name["mock"].qualifiers
    assert by_name["mock"].subpath is None


def test_registry_packages_unaffected_by_workspace_fix(
    rooted_tmp_path: RootedPath,
) -> None:
    write_cargo_toml(
        rooted_tmp_path,
        """
        [package]
        name = "bug"
        version = "0.1.0"
    """,
    )
    write_cargo_lock(
        rooted_tmp_path,
        [
            {"name": "bug", "version": "0.1.0"},
            {
                "name": "mock",
                "version": "0.0.1",
                "source": "registry+https://github.com/rust-lang/crates.io-index",
                "checksum": "abc123",
            },
        ],
    )
    with patch(
        "hermeto.core.package_managers.cargo.main.get_repo_id",
        return_value=MOCK_REPO_ID,
    ):
        components = _generate_sbom_components(rooted_tmp_path)

    mock = next(c for c in components if c.name == "mock")
    purl = PackageURL.from_string(mock.purl)
    assert "vcs_url" not in purl.qualifiers
    assert purl.subpath is None
