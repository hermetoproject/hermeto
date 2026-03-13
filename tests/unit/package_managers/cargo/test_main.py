# SPDX-License-Identifier: GPL-3.0-only
import textwrap
from typing import Any
from unittest import mock

import pytest

from hermeto.core.errors import UnexpectedFormat
from hermeto.core.models.input import Request
from hermeto.core.package_managers.cargo.main import (
    CARGO_OFFLINE_ENV_VAR_NAME,
    CARGO_OFFLINE_ENV_VAR_VALUE,
    CargoPackage,
    CargoVendorResult,
    _resolve_main_package,
    _sanitize_cargo_config,
    fetch_cargo_source,
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


@mock.patch("hermeto.core.package_managers.cargo.main._resolve_main_package")
@mock.patch("hermeto.core.package_managers.cargo.main._generate_sbom_components")
@mock.patch("hermeto.core.package_managers.cargo.main._fetch_dependencies")
@mock.patch("hermeto.core.package_managers.cargo.main._verify_lockfile_is_present")
def test_fetch_cargo_source_generates_offline_env_var(
    mock_verify: mock.Mock,
    mock_fetch: mock.Mock,
    mock_generate_sbom: mock.Mock,
    mock_resolve_main: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_verify.return_value = None
    config_template = """
    [source.vendored-sources]
    directory = "/tmp/old-path"
    """
    mock_fetch.return_value = CargoVendorResult(
        config_template=config_template, lockfile_was_generated=False
    )
    mock_generate_sbom.return_value = []
    mock_resolve_main.return_value = CargoPackage(name="test", version="0.1.0")

    request = Request(
        source_dir=rooted_tmp_path,
        output_dir=rooted_tmp_path.join_within_root("output"),
        packages=[{"type": "cargo", "path": "."}],
    )

    output = fetch_cargo_source(request)

    env_vars = output.build_config.environment_variables
    assert any(
        env.name == CARGO_OFFLINE_ENV_VAR_NAME and env.value == CARGO_OFFLINE_ENV_VAR_VALUE
        for env in env_vars
    )
