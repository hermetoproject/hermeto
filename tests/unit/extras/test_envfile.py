# SPDX-License-Identifier: GPL-3.0-only
import re
from pathlib import Path
from textwrap import dedent

import pytest

from hermeto import APP_NAME
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
          You can also define the format explicitly instead of letting {APP_NAME} choose.
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


# ──────────────────────────────────────────────────────────────────────────────
# Regression tests for issue #1424 – stale path-variable resolution
# ──────────────────────────────────────────────────────────────────────────────


def _make_config(env_vars: list[dict]) -> BuildConfig:  # type: ignore[type-arg]
    """Convenience wrapper so tests stay short."""
    return BuildConfig(environment_variables=env_vars, project_files=[])


# 1 ── path var referenced by a plain var ─────────────────────────────────────
def test_path_var_referenced_by_plain_var() -> None:
    """GOMODCACHE (path) must be fully resolved before GOPROXY (plain) uses it."""
    env_vars = [
        {"name": "GOMODCACHE", "value": "deps/gomod/pkg/mod", "kind": "path"},
        {"name": "GOPROXY", "value": "file://${GOMODCACHE}/cache/download", "kind": "literal"},
    ]
    build_config = _make_config(env_vars)
    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/tmp/output"))

    import json

    result = {item["name"]: item["value"] for item in json.loads(content)}
    assert result["GOMODCACHE"] == "/tmp/output/deps/gomod/pkg/mod"
    assert result["GOPROXY"] == "file:///tmp/output/deps/gomod/pkg/mod/cache/download"


# 2 ── chained path vars (path → path dependency) ─────────────────────────────
def test_chained_path_vars() -> None:
    """GOMODBASE (path) references GOMODCACHE (path); both must get absolute paths."""
    env_vars = [
        {"name": "GOMODCACHE", "value": "deps/gomod/pkg/mod", "kind": "path"},
        {"name": "GOMODBASE", "value": "${GOMODCACHE}/cache", "kind": "path"},
        {"name": "GOPROXY", "value": "file://${GOMODBASE}/download", "kind": "literal"},
    ]
    build_config = _make_config(env_vars)
    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/out"))

    import json

    result = {item["name"]: item["value"] for item in json.loads(content)}
    assert result["GOMODCACHE"] == "/out/deps/gomod/pkg/mod"
    assert result["GOMODBASE"] == "/out/deps/gomod/pkg/mod/cache"
    assert result["GOPROXY"] == "file:///out/deps/gomod/pkg/mod/cache/download"


# 3 ── circular reference raises ValueError ────────────────────────────────────
def test_circular_reference_raises() -> None:
    """A self-referencing or mutually-cyclic variable must raise ValueError."""
    env_vars = [
        {"name": "MYVAR", "value": "${MYVAR}/suffix", "kind": "literal"},
    ]
    build_config = _make_config(env_vars)
    with pytest.raises(ValueError, match="Circular variable reference detected"):
        generate_envfile(build_config, EnvFormat.env, relative_to_path=Path("/out"))


def test_mutual_circular_reference_raises() -> None:
    """Two variables that reference each other must raise ValueError."""
    env_vars = [
        {"name": "AVAR", "value": "${BVAR}/x", "kind": "literal"},
        {"name": "BVAR", "value": "${AVAR}/y", "kind": "literal"},
    ]
    build_config = _make_config(env_vars)
    with pytest.raises(ValueError, match="Circular variable reference detected"):
        generate_envfile(build_config, EnvFormat.env, relative_to_path=Path("/out"))


# 4 ── undefined reference raises ValueError ───────────────────────────────────
def test_undefined_reference_raises() -> None:
    """A reference to a variable that does not exist must raise ValueError."""
    env_vars = [
        {"name": "GOPROXY", "value": "file://${NONEXISTENT}/cache/download", "kind": "literal"},
    ]
    build_config = _make_config(env_vars)
    with pytest.raises(ValueError, match="NONEXISTENT"):
        generate_envfile(build_config, EnvFormat.env, relative_to_path=Path("/out"))


# 5 ── plain → plain substitution still works ─────────────────────────────────
def test_plain_to_plain_substitution_unchanged() -> None:
    """Non-path vars referencing other non-path vars must continue to resolve."""
    env_vars = [
        {"name": "BASE_URL", "value": "https://example.com", "kind": "literal"},
        {"name": "FULL_URL", "value": "${BASE_URL}/api/v1", "kind": "literal"},
    ]
    build_config = _make_config(env_vars)
    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/out"))

    import json

    result = {item["name"]: item["value"] for item in json.loads(content)}
    assert result["BASE_URL"] == "https://example.com"
    assert result["FULL_URL"] == "https://example.com/api/v1"


# 6 ── already-absolute path not double-prefixed ───────────────────────────────
def test_already_absolute_path_not_double_prefixed() -> None:
    """A kind='path' variable with an absolute value must not get output_dir prepended."""
    env_vars = [
        {"name": "SOME_PATH", "value": "/already/absolute/path", "kind": "path"},
    ]
    build_config = _make_config(env_vars)
    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/out"))

    import json

    result = {item["name"]: item["value"] for item in json.loads(content)}
    assert result["SOME_PATH"] == "/already/absolute/path"


# 7 ── empty path var raises ValueError ───────────────────────────────────────
def test_empty_path_var_raises() -> None:
    """A kind='path' variable with an empty value must raise ValueError."""
    env_vars = [
        {"name": "EMPTY_PATH", "value": "", "kind": "path"},
    ]
    build_config = _make_config(env_vars)
    with pytest.raises(ValueError, match="EMPTY_PATH"):
        generate_envfile(build_config, EnvFormat.env, relative_to_path=Path("/out"))


# 8 ── multiple references in a single value ───────────────────────────────────
def test_multiple_references_in_single_value() -> None:
    """All ${VAR} occurrences within a single value must be resolved."""
    env_vars = [
        {"name": "GOMODCACHE", "value": "deps/gomod", "kind": "path"},
        {"name": "GOPATH", "value": "deps/go", "kind": "path"},
        # Two distinct refs + a repeated ref in one value:
        {
            "name": "COMBO",
            "value": "${GOMODCACHE}/cache:${GOPATH}/pkg:${GOMODCACHE}/dl",
            "kind": "literal",
        },
    ]
    build_config = _make_config(env_vars)
    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/out"))

    import json

    result = {item["name"]: item["value"] for item in json.loads(content)}
    assert result["COMBO"] == "/out/deps/gomod/cache:/out/deps/go/pkg:/out/deps/gomod/dl"


# 9 ── substring variable names not corrupted ─────────────────────────────────
def test_substring_variable_names_not_corrupted() -> None:
    """Substituting ${PATH} must not corrupt ${GOPATH} (substring match guard)."""
    env_vars = [
        {"name": "GOPATH", "value": "/go", "kind": "literal"},
        {"name": "PATH", "value": "/usr/bin", "kind": "literal"},
        {"name": "RESULT", "value": "${PATH}:${GOPATH}/bin", "kind": "literal"},
    ]
    build_config = _make_config(env_vars)
    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/out"))

    import json

    result = {item["name"]: item["value"] for item in json.loads(content)}
    assert result["GOPATH"] == "/go"
    assert result["PATH"] == "/usr/bin"
    assert result["RESULT"] == "/usr/bin:/go/bin"


# 10 ── unused path var still resolved ────────────────────────────────────────
def test_unused_path_var_resolved() -> None:
    """A path var that no other variable references must still get an absolute path."""
    env_vars = [
        {"name": "STANDALONE_CACHE", "value": "deps/cache", "kind": "path"},
    ]
    build_config = _make_config(env_vars)
    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/base"))

    import json

    result = {item["name"]: item["value"] for item in json.loads(content)}
    assert result["STANDALONE_CACHE"] == "/base/deps/cache"


# 11 ── output order matches (alphabetically sorted) declaration order ─────────
def test_output_declaration_order_preserved() -> None:
    """Variables must be emitted in the same order they appear in build_config.

    BuildConfig sorts vars alphabetically, so the output order should be
    alphabetical — verifying that generate_envfile does not reorder them.
    """
    env_vars = [
        {"name": "GOMODCACHE", "value": "deps/gomod/pkg/mod", "kind": "path"},
        {"name": "GOPROXY", "value": "file://${GOMODCACHE}/cache", "kind": "literal"},
        {"name": "GOSUMDB", "value": "sum.golang.org", "kind": "literal"},
    ]
    build_config = _make_config(env_vars)
    content = generate_envfile(build_config, EnvFormat.json, relative_to_path=Path("/out"))

    import json

    items = json.loads(content)
    names = [item["name"] for item in items]
    # BuildConfig sorts alphabetically: GOMODCACHE < GOPROXY < GOSUMDB
    assert names == sorted(names)
    assert names == ["GOMODCACHE", "GOPROXY", "GOSUMDB"]

