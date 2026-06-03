# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from textwrap import dedent

import pytest

from hermeto.core.errors import UnsupportedFeature
from hermeto.core.extras.envfile import EnvFormat, generate_envfile
from hermeto.core.models.output import BuildConfig


@pytest.mark.parametrize(
    "filename, expect_format",
    [
        ("hermeto.env", EnvFormat.env),
        ("hermeto.sh", EnvFormat.env),
        ("hermeto.json", EnvFormat.json),
    ],
)
def test_format_based_on_suffix(filename: str, expect_format: EnvFormat) -> None:
    assert EnvFormat.based_on_suffix(Path(filename)) == expect_format


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param(".env", id="no_suffix_dotenv"),
        pytest.param("file.", id="no_suffix_trailing_dot"),
        pytest.param("file.yaml", id="unsupported_suffix"),
    ],
)
def test_cannot_determine_format(filename: str) -> None:
    with pytest.raises(UnsupportedFeature):
        EnvFormat.based_on_suffix(Path(filename))


def test_generate_env_as_json() -> None:
    env_vars = [
        {"name": "GOCACHE", "value": "deps/gomod", "kind": "path"},
        {"name": "GOSUMDB", "value": "off", "kind": "literal"},
    ]
    build_config = BuildConfig(environment_variables=env_vars, project_files=[])

    gocache = '{"name": "GOCACHE", "value": "/output/dir/deps/gomod"}'
    gosumdb = '{"name": "GOSUMDB", "value": "off"}'
    expect_content = f"[{gocache}, {gosumdb}]"

    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/output/dir"))
    assert content == expect_content


def test_generate_env_as_env() -> None:
    env_vars = [
        {"name": "GOCACHE", "value": "deps/gomod", "kind": "path"},
        {"name": "GOSUMDB", "value": "off", "kind": "literal"},
        {"name": "SNEAKY", "value": "foo; echo hello there", "kind": "literal"},
    ]
    build_config = BuildConfig(environment_variables=env_vars, project_files=[])

    expect_content = dedent(
        """
        export GOCACHE=/output/dir/deps/gomod
        export GOSUMDB=off
        export SNEAKY='foo; echo hello there'
        """
    ).strip()

    content = generate_envfile(build_config, EnvFormat.env, relative_to_path=Path("/output/dir"))
    assert content == expect_content
