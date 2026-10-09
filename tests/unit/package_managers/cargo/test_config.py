# SPDX-License-Identifier: GPL-3.0-only
import textwrap

import pytest
import tomlkit

from hermeto.core.errors import UnexpectedFormat
from hermeto.core.package_managers.cargo.config import (
    _sanitize_cargo_config,
    _use_vendored_sources,
)
from hermeto.core.rooted_path import RootedPath


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
                [registries]
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
                [registries]
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
        pytest.param(
            """
            [registries.my-registry]
            index =     "https://my-intranet:8080/git/index"
            token =     "secret-token"
            credential-provider = ["cargo:token"]
            dangerous-field = "should-be-removed"

            [registries.other-registry]
            index = "https://other.example.com/index"
            custom-field = "should-be-removed"

            [build]
            jobs = 4
            """,
            textwrap.dedent(
                """
                [registries]
                [registries.my-registry]
                index = "https://my-intranet:8080/git/index"
                token = "secret-token"
                credential-provider = ["cargo:token"]

                [registries.other-registry]
                index = "https://other.example.com/index"
                """
            ).lstrip(),
            id="multiple_registries_with_safe_and_unsafe_fields_and_a_safe_list_of_providers",
        ),
        pytest.param(
            """
            [registries.my-registry]
            index =     "https://my-intranet:8080/git/index"
            token =     "secret-token"
            credential-provider = ["./dangerousexploit.sh"]
            dangerous-field = "should-be-removed"

            [registries.other-registry]
            index = "https://other.example.com/index"
            custom-field = "should-be-removed"

            [build]
            jobs = 4
            """,
            textwrap.dedent(
                """
                [registries]
                [registries.my-registry]
                index = "https://my-intranet:8080/git/index"
                token = "secret-token"

                [registries.other-registry]
                index = "https://other.example.com/index"
                """
            ).lstrip(),
            id="multiple_registries_with_safe_and_unsafe_fields_and_an_unsafe_list_of_providers",
        ),
        pytest.param(
            """
            [registries.my-registry]
            index =     "https://my-intranet:8080/git/index"
            token =     "secret-token"
            credential-provider = "./dangerousexploit.sh"
            dangerous-field = "should-be-removed"

            [registries.other-registry]
            index = "https://other.example.com/index"
            custom-field = "should-be-removed"

            [build]
            jobs = 4
            """,
            textwrap.dedent(
                """
                [registries]
                [registries.my-registry]
                index = "https://my-intranet:8080/git/index"
                token = "secret-token"

                [registries.other-registry]
                index = "https://other.example.com/index"
                """
            ).lstrip(),
            id="multiple_registries_with_safe_and_unsafe_fields_an_unsafe_provider",
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
