# SPDX-License-Identifier: GPL-3.0-only
import textwrap
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import tomlkit

from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, UnexpectedFormat
from hermeto.core.models.input import Request
from hermeto.core.package_managers.cargo.main import (
    CargoPackage,
    _generate_sbom_components,
    _resolve_main_package,
    _sanitize_cargo_config,
    _use_vendored_sources,
)
from hermeto.core.rooted_path import RootedPath


def write_cargo_toml(rooted_path: RootedPath, content: str) -> None:
    (rooted_path.path / "Cargo.toml").write_text(content)


def write_cargo_lock(rooted_path: RootedPath, content: str) -> None:
    (rooted_path.path / "Cargo.lock").write_text(content)


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


# This is something I wanted to try for a long time. Long parameter lists in
# tests are hard to comprehend, tables are much better for that.  My initial
# plan was to use standalone .md files with doctests which turned out to be too
# cumbersome: resulting "examples" became really hard to follow real fast.
# Gherkin-based test cases do not cut it either (or I cannot figure out how to
# use the language correctly). As always in such cases I tried to invent my own
# ad-hoc format.  What I got so far is a (for now) rigidly formatted table (see
# parameters section in test_cargo_package_purl_generation() below) each visual
# line group in which represents a test case. It is not pretty as it is now:
# turns out some entries in such tables could be a mouthful, borders undulate
# and I do not have a good idea of what to do about that. Maybe untabulate()
# could accept **kwargs to substitute at least the longest fields in the table
# (however that would defeat the goal of a visually compact and uninterrupted
# test case description). The helper itself is essentially a MWP, a stream of
# conciousness with a lot to improve, I would prefer to get some feedback on
# the approach first and then polish it and separate into logical pieces.  This
# location is pretty random, if the idea is appreciated then I'll look for a
# better place for the helper.
def untabulate(table: str) -> tuple:
    # ruff is upset about name 'l' which is local to the next line
    lines = [ls for l in table.split("\n") if (ls := l.strip())]  # noqa: E741
    header, separator, raw_examples = lines[0], lines[1], lines[2:]
    # Want to preserve empty spaces from the table, thus no stripping before filtering
    examples = [[k.strip() for k in e.split("|") if k] for e in raw_examples]
    fields = [fs for f in header.split("|") if (fs := f.strip())]
    if not fields[0] == "id":
        raise ValueError("id must be present in the table and specified for every test case")
    if not (set(separator) <= set("+-|")):
        raise ValueError(
            f"Separator must consist of +-| symbols only, no idea how to interpret this: {separator}"
        )
    all_results = []
    # mypy is ridiculous
    current_result: dict = dict(zip(fields, [list() for _ in fields]))
    while examples:
        current_line = examples.pop(0)
        id_, rest_of_line = current_line[0], current_line[1:]
        if id_:
            if current_result["id"]:
                all_results.append(current_result)
            current_result = dict()
            current_result["id"] = id_  # type: ignore
            for fn, val in zip(fields[1:], rest_of_line):
                current_result[fn] = [val]
        else:
            for fn, val in zip(fields[1:], rest_of_line):
                current_result[fn].append(val)
    all_results.append(current_result)  # the last result after hitting the last line of the table

    for r in all_results:
        for k, v in r.items():
            if isinstance(v, list):
                # because of course ruff was going to get spooked by eval
                r[k] = eval("".join(v))  # noqa: S307

    ready_params = []
    for r in all_results:
        id_ = r.pop("id")  # type: ignore
        interm = [r[f] for f in fields[1:]]
        ready_params.append(pytest.param(*interm, id=id_))
    # id is a special field
    return ",".join(fields[1:]), ready_params


@pytest.mark.parametrize(
    *untabulate(
        """
| id                                        | pkg                                                               | expected_purl                         |
+-------------------------------------------+-------------------------------------------------------------------+---------------------------------------+
| simple_package                            |{"name": "foo",                                                    | "pkg:cargo/foo@0.1.0"                 |
|                                           | "version": "0.1.0"}                                               |                                       |
| package_with_registry_source_and_checksum |{"name": "foo",                                                    | "pkg:cargo/foo@0.1.0?checksum=abc123" |
|                                           | "version": "0.1.0",                                               |                                       |
|                                           | "source": "registry+https://github.com/rust-lang/crates.io-index",|                                       |
|                                           | "checksum": "abc123"}                                             |                                       |
|                                           |                                                                   |                                       |
| package_with_git_source                   |{"name": "foo", "version": "0.1.0",                                | "pkg:cargo/foo@0.1.0?vcs_url=git%2Bhttps://github.com/rust-random/rand%40abc123"|
|                                           | "source": "git+https://github.com/rust-random/rand?rev=abc123#abc123"}|                                   |
|                                           |                                                                   |                                       |
| package_with_alternate_registry           |{"name": "foo","version": "0.1.0",                                 | "pkg:cargo/foo@0.1.0?repository_url=https://my-registry.example.com/index" |
|                                           | "source": "registry+https://my-registry.example.com/index"}       |
|                                           |                                                                   |                                       |
| package_with_crates_io_in_subdomain_no_repository_url |{"name": "foo","version": "0.1.0",                     | "pkg:cargo/foo@0.1.0?checksum=abc123" |
|                                           | "source": "registry+https://my-crates.io-mirror.example.com/index",|                                      |
|                                           | "checksum": "abc123"}                                             |                                       |
"""
    )
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


_MINIMAL_CARGO_TOML = """[package]
name = "my-crate"
version = "0.1.0"
"""

_MINIMAL_CARGO_LOCK = """version = 3

[[package]]
name = "my-crate"
version = "0.1.0"
"""


def _make_request(source_dir: Path, output_dir: Path) -> Request:
    """Build a minimal cargo Request for the given directories."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return Request(
        source_dir=source_dir,
        output_dir=output_dir,
        packages=[{"type": "cargo", "path": "."}],
    )


@mock.patch("hermeto.core.package_managers.cargo.main.get_config")
@mock.patch("hermeto.core.package_managers.cargo.main.get_repo_id")
def test_generate_sbom_components_permissive_no_git_vcs_url_is_none(
    mock_get_repo_id: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """PERMISSIVE mode + NotAGitRepo: no exception raised and vcs_url absent from PURL."""
    mock_get_config.return_value.mode = Mode.PERMISSIVE
    mock_get_repo_id.side_effect = NotAGitRepo("not a git repo", solution=None)

    write_cargo_toml(rooted_tmp_path, _MINIMAL_CARGO_TOML)
    write_cargo_lock(rooted_tmp_path, _MINIMAL_CARGO_LOCK)

    output_dir = rooted_tmp_path.path / "output"
    request = _make_request(rooted_tmp_path.path, output_dir)

    # Must not raise; vcs_url should be absent from the component PURL
    components = _generate_sbom_components(rooted_tmp_path, request)

    assert len(components) == 1
    assert "vcs_url" not in components[0].purl


@mock.patch("hermeto.core.package_managers.cargo.main.get_config")
@mock.patch("hermeto.core.package_managers.cargo.main.get_repo_id")
def test_generate_sbom_components_permissive_with_git_vcs_url_populated(
    mock_get_repo_id: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """PERMISSIVE mode + git repo present: vcs_url is populated in the component PURL."""
    mock_get_config.return_value.mode = Mode.PERMISSIVE
    fake_vcs_url = "git+https://github.com/example/my-crate@abc1234"
    mock_get_repo_id.return_value.as_vcs_url_qualifier.return_value = fake_vcs_url

    write_cargo_toml(rooted_tmp_path, _MINIMAL_CARGO_TOML)
    write_cargo_lock(rooted_tmp_path, _MINIMAL_CARGO_LOCK)

    output_dir = rooted_tmp_path.path / "output"
    request = _make_request(rooted_tmp_path.path, output_dir)

    components = _generate_sbom_components(rooted_tmp_path, request)

    assert len(components) == 1
    # The PURL serializer percent-encodes the vcs_url value; just verify the qualifier key is present.
    assert "vcs_url=" in components[0].purl


@mock.patch("hermeto.core.package_managers.cargo.main.get_config")
@mock.patch("hermeto.core.package_managers.cargo.main.get_repo_id")
def test_generate_sbom_components_strict_source_inside_output_no_git_no_raise(
    mock_get_repo_id: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """STRICT mode + source_dir inside output_dir + NotAGitRepo: short-circuit suppresses error."""
    mock_get_config.return_value.mode = Mode.STRICT
    mock_get_repo_id.side_effect = NotAGitRepo("not a git repo", solution=None)

    # Place cargo files inside a subdirectory of the output dir to trigger the short-circuit.
    output_dir = rooted_tmp_path.path / "output"
    package_dir_path = output_dir / "src"
    package_dir_path.mkdir(parents=True)
    package_dir = RootedPath(package_dir_path)

    write_cargo_toml(package_dir, _MINIMAL_CARGO_TOML)
    write_cargo_lock(package_dir, _MINIMAL_CARGO_LOCK)

    # source_dir IS inside output_dir -- the pip->cargo sdist scenario
    request = Request(
        source_dir=package_dir_path,
        output_dir=output_dir,
        packages=[{"type": "cargo", "path": "."}],
    )

    # Must not raise even though mode is STRICT
    components = _generate_sbom_components(package_dir, request)

    assert len(components) == 1
    assert "vcs_url" not in components[0].purl


@mock.patch("hermeto.core.package_managers.cargo.main.get_config")
@mock.patch("hermeto.core.package_managers.cargo.main.get_repo_id")
def test_generate_sbom_components_strict_mode_raises_without_git_repo(
    mock_get_repo_id: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """STRICT mode + NotAGitRepo + source NOT inside output_dir: exception propagates."""
    mock_get_config.return_value.mode = Mode.STRICT
    mock_get_repo_id.side_effect = NotAGitRepo("not a git repo", solution=None)

    write_cargo_toml(rooted_tmp_path, _MINIMAL_CARGO_TOML)
    write_cargo_lock(rooted_tmp_path, _MINIMAL_CARGO_LOCK)

    # output_dir is separate from source_dir to avoid the short-circuit path
    output_dir = rooted_tmp_path.path.parent / "output"
    request = _make_request(rooted_tmp_path.path, output_dir)

    with pytest.raises(NotAGitRepo):
        _generate_sbom_components(rooted_tmp_path, request)
