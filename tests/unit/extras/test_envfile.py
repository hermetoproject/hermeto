from pathlib import Path
from textwrap import dedent

import pytest

from hermeto.core.errors import UnsupportedFeature
from hermeto.core.extras.envfile import EnvFormat, generate_envfile
from hermeto.core.models.output import BuildConfig


@pytest.mark.parametrize(
    "filename, expect_format",
    [
        ("cachito.env", EnvFormat.env),
        ("cachito.sh", EnvFormat.env),
        ("cachito.json", EnvFormat.json),
    ],
)
def test_format_based_on_suffix(filename: str, expect_format: EnvFormat) -> None:
    assert EnvFormat.based_on_suffix(Path(filename)) == expect_format


@pytest.mark.parametrize(
    "filename, expect_reason",
    [
        (".env", "file has no suffix: .env"),
        ("file.", "file has no suffix: file."),
        ("file.yaml", "unsupported suffix: yaml"),
    ],
)
def test_cannot_determine_format(filename: str, expect_reason: str) -> None:
    expect_error = f"Cannot determine envfile format, {expect_reason}"
    with pytest.raises(UnsupportedFeature, match=expect_error) as exc_info:
        EnvFormat.based_on_suffix(Path(filename))

    expect_friendly_msg = dedent(
        f"""
        Cannot determine envfile format, {expect_reason}
          Please use one of the supported suffixes: json, env, sh[==env]
          You can also define the format explicitly instead of letting Cachi2 choose.
        """
    ).strip()
    assert exc_info.value.friendly_msg() == expect_friendly_msg


def test_generate_env_as_json() -> None:
    env_vars = [
        {"name": "GOCACHE", "value": "deps/gomod", "kind": "path"},
        {"name": "GOSUMDB", "value": "sum.golang.org", "kind": "literal"},
    ]
    build_config = BuildConfig(environment_variables=env_vars, project_files=[])

    gocache = '{"name": "GOCACHE", "value": "/output/dir/deps/gomod"}'
    gosumdb = '{"name": "GOSUMDB", "value": "sum.golang.org"}'
    expect_content = f"[{gocache}, {gosumdb}]"

    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/output/dir"))
    assert content == expect_content


def test_generate_env_as_env() -> None:
    env_vars = [
        {"name": "GOCACHE", "value": "deps/gomod", "kind": "path"},
        {"name": "GOSUMDB", "value": "sum.golang.org", "kind": "literal"},
        {"name": "SNEAKY", "value": "foo; echo hello there", "kind": "literal"},
    ]
    build_config = BuildConfig(environment_variables=env_vars, project_files=[])

    expect_content = dedent(
        """
        export GOCACHE=/output/dir/deps/gomod
        export GOSUMDB=sum.golang.org
        export SNEAKY='foo; echo hello there'
        """
    ).strip()

    content = generate_envfile(build_config, EnvFormat.env, relative_to_path=Path("/output/dir"))
    assert content == expect_content
