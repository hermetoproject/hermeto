# SPDX-License-Identifier: GPL-3.0-only
import datetime
import importlib.metadata
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import Any
from unittest import mock

import pytest
import typer.testing
import yaml

import hermeto.core.config as config_file
from hermeto import APP_NAME
from hermeto.core.constants import Mode
from hermeto.core.models.input import Request
from hermeto.core.models.output import (
    BuildConfig,
    Component,
    EnvironmentVariable,
    RequestOutput,
    Sbom,
)
from hermeto.core.models.sbom import SPDXSbom
from hermeto.interface.cli import DEFAULT_OUTPUT, DEFAULT_SOURCE, app

runner = typer.testing.CliRunner()


SPDX_EPOCH_STRFTIME = datetime.datetime.fromtimestamp(0).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def tmp_cwd(tmp_path: Path) -> Iterator[Path]:
    """Temporarily change working directory to a pytest tmpdir."""
    cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)


@contextmanager
def mock_fetch_deps(
    expect_request: Request | None = None, output: RequestOutput | None = None
) -> Iterator[mock.MagicMock]:
    output = output or RequestOutput.empty()

    with mock.patch("hermeto.interface.cli.resolve_packages") as mock_resolve_packages:
        mock_resolve_packages.return_value = output
        yield mock_resolve_packages

    if expect_request is not None:
        mock_resolve_packages.assert_called_once_with(expect_request)


def invoke_expecting_sucess(app: typer.Typer, args: list[str]) -> typer.testing.Result:
    result = runner.invoke(app, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result


def invoke_expecting_invalid_usage(app: typer.Typer, args: list[str]) -> typer.testing.Result:
    result = runner.invoke(app, args)
    assert result.exit_code == 2, (
        f"expected exit_code=2, got exit_code={result.exit_code}\ncommand output:\n{result.output}"
    )
    return result


def assert_pattern_in_output(pattern: str | re.Pattern, output: str) -> None:
    if isinstance(pattern, re.Pattern):
        match = bool(pattern.search(output))
    else:
        match = pattern in output

    assert match, f"pattern {pattern!r} not found!\noutput:\n{output}"


class TestTopLevelOpts:
    def test_version_option(self) -> None:
        expect_version = importlib.metadata.version(f"{APP_NAME}")
        result = invoke_expecting_sucess(app, ["--version"])
        assert expect_version in result.output

    @mock.patch(
        "hermeto.core.resolver._package_managers",
        {"foo": mock.Mock(), "bar": mock.Mock(), "x-baz": mock.Mock()},
    )
    def test_list_backends_option(self) -> None:
        result = invoke_expecting_sucess(app, ["list-backends"])
        lines = result.output.splitlines()
        assert "bar, foo" in lines[0]
        assert "x-baz" in lines[1]

    @pytest.mark.parametrize(
        "config_values",
        [
            {"gomod": {"download_max_tries": 1000000}},
            {"gomod": {"download_max_tries": 1000000, "proxy_url": "https://example.com"}},
        ],
    )
    def test_config_file_option(
        self,
        config_values: dict[str, dict[str, Any]],
        tmp_cwd: Path,
    ) -> None:
        config_file_path = tmp_cwd / "config.yaml"
        config_file_path.write_text(yaml.dump(config_values))

        args = ["--config-file", str(config_file_path), "fetch-deps", "gomod"]

        output = RequestOutput.from_obj_list(
            components=[
                Component(
                    name="cool-package",
                    version="v1.0.0",
                    purl="pkg:generic/cool-package@v1.0.0",
                    type="library",
                )
            ],
            environment_variables=[
                EnvironmentVariable(name="GOMOD_SOMETHING", value="yes"),
            ],
            project_files=[],
        )

        def side_effect(whatever: Any) -> RequestOutput:
            config = config_file.get_config()

            for namespace, fields in config_values.items():
                namespace_obj = getattr(config, namespace)
                for field, expected_value in fields.items():
                    assert getattr(namespace_obj, field) == expected_value

            return output

        with mock.patch("hermeto.interface.cli.resolve_packages") as mock_resolve_packages:
            mock_resolve_packages.side_effect = side_effect
            invoke_expecting_sucess(app, args)

    @pytest.mark.parametrize(
        "file_create, file, file_text, error_expectation",
        [
            (
                True,
                "config.yaml",
                "goproxy_url",
                "Error: InvalidInput: 1 validation error in Hermeto configuration:\n: Input should be a valid dictionary or instance of Config",
            ),
            (
                True,
                "config.yaml",
                "non_existing_option: True",
                "Error: InvalidInput: 1 validation error in Hermeto configuration:\nnon_existing_option: Extra inputs are not permitted\n",
            ),
            (
                False,
                "config.yaml",
                "",
                "Invalid value for '--config-file': File 'config.yaml' does not exist.",
            ),
        ],
    )
    def test_config_file_option_invalid(
        self,
        file_create: bool,
        file: str,
        file_text: str,
        error_expectation: str,
        tmp_cwd: Path,
    ) -> None:
        if file_create:
            tmp_cwd.joinpath(file).touch()
            tmp_cwd.joinpath(file).write_text(file_text)

        args = ["--config-file", file, "fetch-deps", "gomod"]
        with mock_fetch_deps():
            result = invoke_expecting_invalid_usage(app, args)
            assert error_expectation in result.output

    @pytest.mark.parametrize(
        "mode",
        [
            "strict",
            "permissive",
        ],
    )
    def test_mode_option_is_valid(self, mode: str) -> None:
        args = ["--mode", mode, "fetch-deps", "gomod"]
        with mock_fetch_deps():
            invoke_expecting_sucess(app, args)

    def test_mode_permissive_propagates_to_config(self) -> None:
        args = ["--mode", "permissive", "fetch-deps", "gomod"]

        def side_effect(request: Any) -> RequestOutput:
            assert config_file.get_config().mode == Mode.PERMISSIVE
            return RequestOutput.empty()

        with mock.patch("hermeto.interface.cli.resolve_packages") as mock_resolve:
            mock_resolve.side_effect = side_effect
            invoke_expecting_sucess(app, args)

    @pytest.mark.parametrize(
        "mode",
        [
            "bad",
            "ugly",
        ],
    )
    def test_mode_option_is_not_valid(self, mode: str) -> None:
        args = ["--mode", mode, "fetch-deps", "gomod"]
        with mock_fetch_deps():
            result = invoke_expecting_invalid_usage(app, args)
            assert f"Invalid value for '--mode': '{mode}' is not one of" in result.output

    @pytest.mark.parametrize(
        "loglevel_args, expected_level",
        [
            ([], "INFO"),
            (["--log-level=debug"], "DEBUG"),
            (["--log-level", "WARNING"], "WARNING"),
        ],
    )
    def test_loglevel_option(
        self,
        loglevel_args: list[str],
        expected_level: str,
        tmp_cwd: Path,
    ) -> None:
        args = [*loglevel_args, "fetch-deps", "gomod"]

        with mock_fetch_deps():
            invoke_expecting_sucess(app, args)

        loglevel = logging.getLogger(f"{APP_NAME}").getEffectiveLevel()
        loglevel_name = logging.getLevelName(loglevel)
        assert loglevel_name == expected_level

    def test_unknown_loglevel(self, tmp_cwd: Path) -> None:
        args = ["--log-level=unknown", "fetch-deps", "gomod"]
        result = invoke_expecting_invalid_usage(app, args)
        assert "Invalid value for '--log-level': 'unknown' is not one of" in result.output


class TestFetchDeps:
    @pytest.mark.parametrize(
        "path_args, expect_source, expect_output",
        [
            (
                [],
                f"{{cwd}}/{DEFAULT_SOURCE}",
                f"{{cwd}}/{DEFAULT_OUTPUT}",
            ),
            (
                ["--source=./source/dir", "--output=./output/dir"],
                "{cwd}/source/dir",
                "{cwd}/output/dir",
            ),
            (
                ["--source={cwd}/source/dir", "--output={cwd}/output/dir"],
                "{cwd}/source/dir",
                "{cwd}/output/dir",
            ),
        ],
    )
    def test_specify_paths(
        self,
        path_args: list[str],
        expect_source: str,
        expect_output: str,
        tmp_cwd: Path,
    ) -> None:
        tmp_cwd.joinpath("source", "dir").mkdir(parents=True, exist_ok=True)

        source_abspath = expect_source.format(cwd=tmp_cwd)
        output_abspath = expect_output.format(cwd=tmp_cwd)
        expect_request = Request(
            source_dir=source_abspath,
            output_dir=output_abspath,
            packages=[{"type": "gomod"}],
        )

        path_args = [arg.format(cwd=tmp_cwd) for arg in path_args]

        with mock_fetch_deps(expect_request):
            invoke_expecting_sucess(app, ["fetch-deps", *path_args, "gomod"])

    @pytest.mark.parametrize(
        "path_args, expect_error",
        [
            (
                ["--source=no-such-dir"],
                "'--source': Directory 'no-such-dir' does not exist",
            ),
            (
                ["--source=/no-such-dir"],
                "'--source': Directory '/no-such-dir' does not exist",
            ),
            (
                ["--source=not-a-directory"],
                "'--source': Directory 'not-a-directory' is a file",
            ),
            (
                ["--output=not-a-directory"],
                "'--output': Directory 'not-a-directory' is a file",
            ),
        ],
    )
    def test_invalid_paths(self, path_args: list[str], expect_error: str, tmp_cwd: Path) -> None:
        tmp_cwd.joinpath("not-a-directory").touch()

        result = invoke_expecting_invalid_usage(app, ["fetch-deps", *path_args])
        assert expect_error in result.output

    def test_no_packages(self) -> None:
        result = invoke_expecting_invalid_usage(app, ["fetch-deps"])
        assert "Missing argument 'PKG'" in result.output

    @pytest.mark.parametrize(
        "package_arg, expect_packages",
        [
            # specify a single basic package
            ("gomod", [{"type": "gomod"}]),
            ('{"type": "gomod"}', [{"type": "gomod"}]),
            ('[{"type": "gomod"}]', [{"type": "gomod"}]),
            # specify multiple packages
            (
                '[{"type": "gomod"}, {"type": "gomod", "path": "pkg_a"}]',
                [{"type": "gomod"}, {"type": "gomod", "path": "pkg_a"}],
            ),
            (
                dedent(
                    """
                    [
                        {"type": "gomod"},
                        {"type": "gomod", "path": "pkg_a"},
                        {"type": "gomod", "path": "pkg_b"}
                    ]
                    """
                ),
                [
                    {"type": "gomod"},
                    {"type": "gomod", "path": "pkg_a"},
                    {"type": "gomod", "path": "pkg_b"},
                ],
            ),
            # specify using a 'packages' key
            (
                '{"packages": [{"type": "gomod"}]}',
                [{"type": "gomod"}],
            ),
            (
                dedent(
                    """
                    {"packages": [
                        {"type": "gomod", "path": "pkg_a"},
                        {"type": "gomod", "path": "pkg_b"}
                    ]}
                    """
                ),
                [
                    {"type": "gomod", "path": "pkg_a"},
                    {"type": "gomod", "path": "pkg_b"},
                ],
            ),
            pytest.param(
                dedent(
                    """
                    [{"type": "rpm", "path": ".", "options": {"dnf": {
                        "some-very-long-repo-id-aarch64-rpms": {"gpgcheck": 0},
                        "some-very-long-repo-id-ppc64le-rpms": {"gpgcheck": 0},
                        "some-very-long-repo-id-x86_64-rpms": {"gpgcheck": 0},
                        "some-very-long-repo-id-s390x-rpms": {"gpgcheck": 0}
                    }}}
                    ]
                    """
                ),
                [
                    {
                        "type": "rpm",
                        "path": ".",
                        "options": {
                            "dnf": {
                                "some-very-long-repo-id-aarch64-rpms": {"gpgcheck": 0},
                                "some-very-long-repo-id-ppc64le-rpms": {"gpgcheck": 0},
                                "some-very-long-repo-id-x86_64-rpms": {"gpgcheck": 0},
                                "some-very-long-repo-id-s390x-rpms": {"gpgcheck": 0},
                            }
                        },
                    }
                ],
                id="very-long-input-string",
            ),
        ],
    )
    def test_specify_packages(
        self, package_arg: str, expect_packages: list[dict], tmp_cwd: Path
    ) -> None:
        tmp_cwd.joinpath("pkg_a").mkdir(exist_ok=True)
        tmp_cwd.joinpath("pkg_b").mkdir(exist_ok=True)

        expect_request = Request(
            source_dir=tmp_cwd / DEFAULT_SOURCE,
            output_dir=tmp_cwd / DEFAULT_OUTPUT,
            packages=expect_packages,
        )
        with mock_fetch_deps(expect_request):
            invoke_expecting_sucess(app, ["fetch-deps", package_arg])

    @pytest.mark.parametrize(
        "package_arg, expect_packages",
        [
            pytest.param(
                {"type": "gomod"},
                [{"type": "gomod"}],
                id="single-package",
            ),
            pytest.param(
                {"packages": [{"type": "gomod"}]},
                [{"type": "gomod"}],
                id="single-package-in-packages-key",
            ),
            pytest.param(
                {
                    "packages": [
                        {"type": "gomod", "path": "pkg_a"},
                        {"type": "gomod", "path": "pkg_b"},
                    ]
                },
                [
                    {"type": "gomod", "path": "pkg_a"},
                    {"type": "gomod", "path": "pkg_b"},
                ],
                id="multiple-packages",
            ),
        ],
    )
    def test_specifiy_packages_as_json_file(
        self,
        package_arg: dict,
        expect_packages: list[dict],
        tmp_cwd: Path,
    ) -> None:
        tmp_cwd.joinpath("pkg_a").mkdir(exist_ok=True)
        tmp_cwd.joinpath("pkg_b").mkdir(exist_ok=True)

        input_file = tmp_cwd.joinpath("input.json")
        input_file.write_text(json.dumps(package_arg))

        expect_request = Request(
            source_dir=tmp_cwd / DEFAULT_SOURCE,
            output_dir=tmp_cwd / DEFAULT_OUTPUT,
            packages=expect_packages,
        )

        with mock_fetch_deps(expect_request):
            invoke_expecting_sucess(app, ["fetch-deps", str(input_file)])

    @pytest.mark.parametrize(
        "package_arg, expect_error_lines",
        [
            # Invalid JSON
            (
                "{notjson}",
                ["'PKG': Looks like JSON but is not valid JSON: '{notjson}'"],
            ),
            (
                "[notjson]",
                ["'PKG': Looks like JSON but is not valid JSON: '[notjson]'"],
            ),
            # Invalid package type
            (
                "idk",
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages -> 0",
                    "Requested backend type 'idk' doesn't match expected ones: 'bundler', 'cargo', 'generic', 'gomod', 'npm', 'pip', 'rpm', 'yarn'",
                ],
            ),
            (
                '[{"type": "idk"}]',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages -> 0",
                    "Requested backend type 'idk' doesn't match expected ones: 'bundler', 'cargo', 'generic', 'gomod', 'npm', 'pip', 'rpm', 'yarn'",
                ],
            ),
            (
                '{"packages": [{"type": "idk"}]}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages -> 0",
                    "Requested backend type 'idk' doesn't match expected ones: 'bundler', 'cargo', 'generic', 'gomod', 'npm', 'pip', 'rpm', 'yarn'",
                ],
            ),
            # Missing package type
            (
                "{}",
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages -> 0",
                    "Unable to extract tag using discriminator 'type'",
                ],
            ),
            (
                '[{"type": "gomod"}, {}]',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages -> 1",
                    "Unable to extract tag using discriminator 'type'",
                ],
            ),
            (
                '{"packages": [{}]}',
                [
                    "1 validation error for user input",
                    "packages -> 0",
                    "Unable to extract tag using discriminator 'type'",
                ],
            ),
            # Invalid path
            (
                '{"type": "gomod", "path": "/absolute"}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages -> 0 -> gomod -> path",
                    "Value error, path must be relative: /absolute",
                ],
            ),
            (
                '{"type": "gomod", "path": "weird/../subpath"}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages -> 0 -> gomod -> path",
                    "Value error, path contains ..: weird/../subpath",
                ],
            ),
            (
                '{"type": "gomod", "path": "suspicious-symlink"}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages",
                    "Value error, package path (a symlink?) leads outside source directory: suspicious-symlink",
                ],
            ),
            (
                '{"type": "gomod", "path": "no-such-dir"}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages",
                    "Value error, package path does not exist (or is not a directory): no-such-dir",
                ],
            ),
            # Extra fields
            (
                '{"type": "gomod", "what": "dunno"}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages -> 0 -> gomod -> what",
                    "Extra inputs are not permitted",
                ],
            ),
            # Invalid format using 'packages' key
            (
                '{"packages": "gomod"}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages",
                    "Input should be a valid list",
                ],
            ),
            (
                '{"packages": {"type":"gomod"}}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages",
                    "Input should be a valid list",
                ],
            ),
            (
                '{"packages": ["gomod"]}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "packages -> 0",
                    "Input should be a valid dictionary or object to extract fields from",
                ],
            ),
            (
                '{"packages": [{"type": "gomod"}], "what": "dunno"}',
                [
                    "Error: InvalidInput: 1 validation error for user input",
                    "what",
                    "Extra inputs are not permitted",
                ],
            ),
        ],
    )
    def test_invalid_packages(
        self, package_arg: str, expect_error_lines: list[str], tmp_cwd: Path
    ) -> None:
        tmp_cwd.joinpath("suspicious-symlink").symlink_to("..")

        result = invoke_expecting_invalid_usage(app, ["fetch-deps", package_arg])

        for pattern in expect_error_lines:
            assert_pattern_in_output(pattern, result.output)

    def test_invalid_input_json_file(self, tmp_cwd: Path) -> None:
        input_file = tmp_cwd.joinpath("input.json")
        input_file.write_text("}abc{")

        result = invoke_expecting_invalid_usage(app, ["fetch-deps", str(input_file)])
        assert_pattern_in_output(
            "'PKG': Looks like JSON file but is not valid JSON file", result.output
        )

    @pytest.mark.parametrize(
        "cli_args, expect_flags",
        [
            (["gomod"], {}),
            (["gomod", "--gomod-vendor"], {"gomod-vendor"}),
            (
                ['{"packages": [{"type":"gomod"}], "flags": ["gomod-vendor"]}'],
                {"gomod-vendor"},
            ),
            (
                [
                    '{"packages": [{"type":"gomod"}], "flags": ["gomod-vendor"]}',
                    "--gomod-vendor",
                ],
                {"gomod-vendor"},
            ),
            (
                [
                    "gomod",
                    "--gomod-vendor",
                    "--gomod-vendor-check",
                    "--cgo-disable",
                    "--force-gomod-tidy",
                ],
                {
                    "gomod-vendor",
                    "gomod-vendor-check",
                    "cgo-disable",
                    "force-gomod-tidy",
                },
            ),
            (
                [
                    '{"packages": [{"type":"gomod"}], "flags": ["gomod-vendor", "cgo-disable"]}',
                    "--gomod-vendor-check",
                    "--force-gomod-tidy",
                ],
                {
                    "gomod-vendor",
                    "gomod-vendor-check",
                    "cgo-disable",
                    "force-gomod-tidy",
                },
            ),
            (
                [
                    '{"packages": [{"type":"gomod"}]}',
                    "--dev-package-managers",
                ],
                {"dev-package-managers"},
            ),
        ],
    )
    def test_specify_flags(
        self, cli_args: list[str], expect_flags: set[str], tmp_cwd: Path
    ) -> None:
        expect_request = Request(
            source_dir=tmp_cwd / DEFAULT_SOURCE,
            output_dir=tmp_cwd / DEFAULT_OUTPUT,
            packages=[{"type": "gomod"}],
            flags=frozenset(expect_flags),
        )
        with mock_fetch_deps(expect_request):
            invoke_expecting_sucess(app, ["fetch-deps", *cli_args])

    @pytest.mark.parametrize(
        "cli_args, expect_error",
        [
            (["gomod", "--no-such-flag"], "No such option: --no-such-flag"),
            (
                ['{"packages": [{"type": "gomod"}], "flags": "not-a-list"}'],
                "Input should be a valid list",
            ),
            (
                ['{"packages": [{"type": "gomod"}], "flags": {"dict": "no-such-flag"}}'],
                "Input should be a valid list",
            ),
            (
                ['{"packages": [{"type": "gomod"}], "flags": ["no-such-flag"]}'],
                "Input should be 'cgo-disable', 'dev-package-managers', 'force-gomod-tidy', 'gomod-vendor' or 'gomod-vendor-check'",
            ),
        ],
    )
    def test_invalid_flags(self, cli_args: list[str], expect_error: str) -> None:
        result = invoke_expecting_invalid_usage(app, ["fetch-deps", *cli_args])
        assert_pattern_in_output(expect_error, result.output)

    @pytest.mark.parametrize(
        "request_output",
        [
            RequestOutput.empty(),
            RequestOutput.from_obj_list(
                components=[
                    Component(
                        name="cool-package",
                        version="v1.0.0",
                        purl="pkg:generic/cool-package@v1.0.0",
                        type="library",
                    )
                ],
                environment_variables=[
                    EnvironmentVariable(name="GOMOD_SOMETHING", value="yes"),
                ],
                project_files=[],
            ),
        ],
    )
    def test_write_json_output(
        self,
        request_output: RequestOutput,
        tmp_cwd: Path,
    ) -> None:
        with mock_fetch_deps(output=request_output):
            invoke_expecting_sucess(app, ["fetch-deps", "gomod"])

        build_config_path = tmp_cwd / DEFAULT_OUTPUT / ".build-config.json"
        sbom_path = tmp_cwd / DEFAULT_OUTPUT / "bom.json"

        written_build_config = BuildConfig.model_validate_json(build_config_path.read_text())
        written_sbom = Sbom.model_validate_json(sbom_path.read_text())

        assert written_build_config == request_output.build_config
        assert written_sbom == request_output.generate_sbom()

    @pytest.mark.parametrize(
        "request_output",
        [
            RequestOutput.empty(),
            RequestOutput.from_obj_list(
                components=[
                    Component(
                        name="cool-package",
                        version="v1.0.0",
                        purl="pkg:generic/cool-package@v1.0.0",
                        type="library",
                    )
                ],
                environment_variables=[
                    EnvironmentVariable(name="GOMOD_SOMETHING", value="yes"),
                ],
                project_files=[],
            ),
        ],
    )
    @mock.patch("hermeto.core.models.sbom.spdx_now", return_value=SPDX_EPOCH_STRFTIME)
    def test_write_json_output_spdx(
        self,
        mock_spdx_now: str,
        request_output: RequestOutput,
        tmp_cwd: Path,
    ) -> None:
        with mock_fetch_deps(output=request_output):
            invoke_expecting_sucess(app, ["fetch-deps", "--sbom-output-type", "spdx", "gomod"])

        build_config_path = tmp_cwd / DEFAULT_OUTPUT / ".build-config.json"
        sbom_path = tmp_cwd / DEFAULT_OUTPUT / "bom.json"

        written_build_config = BuildConfig.model_validate_json(build_config_path.read_text())
        sbom_text = sbom_path.read_text()
        written_sbom = SPDXSbom.model_validate_json(sbom_text)

        assert written_build_config == request_output.build_config
        assert written_sbom == request_output.generate_sbom().to_spdx("NOASSERTION")

    def test_delete_existing_deps_dir(self, tmp_cwd: Path) -> None:
        ouput_dir = tmp_cwd / DEFAULT_OUTPUT
        pip_deps_dir = ouput_dir / "deps" / "pip"
        unrelated_dir = ouput_dir / "unrelated_dir"

        pip_deps_dir.mkdir(parents=True)
        unrelated_dir.mkdir()
        (pip_deps_dir / "some-pip-file.py").touch()

        with mock_fetch_deps(output=RequestOutput.empty()):
            invoke_expecting_sucess(app, ["fetch-deps", "pip"])

        assert pip_deps_dir.exists() is False
        assert unrelated_dir.exists() is True
        assert (ouput_dir / "bom.json").exists() is True
        assert (ouput_dir / ".build-config.json").exists() is True


def env_file_as_json(for_output_dir: Path) -> str:
    gocache = f'{{"name": "GOCACHE", "value": "{for_output_dir}/deps/gomod"}}'
    gosumdb = '{"name": "GOSUMDB", "value": "sum.golang.org"}'
    return f"[{gocache}, {gosumdb}]\n"


def env_file_as_env(for_output_dir: Path) -> str:
    return dedent(
        f"""
        export GOCACHE={for_output_dir}/deps/gomod
        export GOSUMDB=sum.golang.org
        """
    ).lstrip()


class TestGenerateEnv:
    ENV_VARS = [
        {"name": "GOCACHE", "value": "${output_dir}/deps/gomod"},
        {"name": "GOSUMDB", "value": "sum.golang.org"},
    ]

    @pytest.fixture
    def tmp_cwd_as_output_dir(self, tmp_cwd: Path) -> Path:
        """Change working directory to a tmpdir and write .build-config.json into it."""
        build_config = BuildConfig(environment_variables=self.ENV_VARS, project_files=[])
        tmp_cwd.joinpath(".build-config.json").write_text(build_config.model_dump_json())
        return tmp_cwd

    @pytest.mark.parametrize("use_relative_path", [True, False])
    @pytest.mark.parametrize(
        "extra_args, make_output, output_file",
        [
            ([], env_file_as_json, None),
            (["--format=env"], env_file_as_env, None),
            ([f"--output={APP_NAME}-env.json"], env_file_as_json, f"{APP_NAME}-env.json"),
            ([f"--output={APP_NAME}.env"], env_file_as_env, f"{APP_NAME}.env"),
            ([f"--output={APP_NAME}-env.sh"], env_file_as_env, f"{APP_NAME}-env.sh"),
            (["--format=json", f"--output={APP_NAME}.env"], env_file_as_json, f"{APP_NAME}.env"),
        ],
    )
    def test_generate_env(
        self,
        extra_args: list[str],
        make_output: Callable[[Path], str],
        output_file: str | None,
        use_relative_path: bool,
        tmp_cwd_as_output_dir: Path,
    ) -> None:
        if use_relative_path:
            from_output_dir = "."
        else:
            from_output_dir = str(tmp_cwd_as_output_dir)

        result = invoke_expecting_sucess(app, ["generate-env", from_output_dir, *extra_args])

        expect_output = make_output(tmp_cwd_as_output_dir)
        if output_file is None:
            assert result.output == expect_output
        else:
            assert result.output == ""
            assert Path(output_file).read_text() == expect_output

    @pytest.mark.parametrize("fmt", ["env", "json"])
    @pytest.mark.parametrize(
        "for_output_dir, expect_output_dir",
        [
            ("relative/dir", "{cwd}/relative/dir"),
            ("/absolute/dir", "/absolute/dir"),
        ],
    )
    def test_generate_for_different_output_dir(
        self,
        fmt: str,
        for_output_dir: str,
        expect_output_dir: str,
        tmp_cwd_as_output_dir: Path,
    ) -> None:
        result = invoke_expecting_sucess(
            app,
            [
                "generate-env",
                str(tmp_cwd_as_output_dir),
                "--for-output-dir",
                for_output_dir,
                "--format",
                fmt,
            ],
        )

        resolved_output_dir = Path(expect_output_dir.format(cwd=tmp_cwd_as_output_dir))
        if fmt == "env":
            expect_output = env_file_as_env(resolved_output_dir)
        else:
            expect_output = env_file_as_json(resolved_output_dir)

        assert result.stdout == expect_output

    def test_invalid_format(self) -> None:
        # Note: .sh is a recognized suffix, but the --format option accepts only 'json' and 'env'
        result = invoke_expecting_invalid_usage(app, ["generate-env", ".", "-f", "sh"])
        assert "Invalid value for '-f' / '--format': 'sh' is not one of" in result.output

    def test_unsupported_suffix(self, caplog: pytest.LogCaptureFixture) -> None:
        result = invoke_expecting_invalid_usage(app, ["generate-env", ".", "-o", "env.yaml"])

        msg = "Cannot determine envfile format, unsupported suffix: yaml"
        assert msg in result.output
        assert "  Please use one of the supported suffixes: " in result.output

        # Error message should also be logged, but the extra info should not
        assert msg in caplog.text
        assert "  Please use one of the supported suffixes: " not in caplog.text


class TestInjectFiles:
    @pytest.fixture
    def tmp_cwd_as_output_dir(self, tmp_cwd: Path) -> Path:
        """Change working directory to a tmpdir and write .build-config.json into it.

        Also create one of the project files in the output to test overwriting vs. creating.
        """
        tmp_cwd.joinpath("requirements.txt").touch()
        project_files = [
            {
                "abspath": tmp_cwd / "requirements.txt",
                "template": "foo @ file://${output_dir}/deps/pip/foo.tar.gz",
            },
            {
                "abspath": tmp_cwd / "some-dir" / "requirements-extra.txt",
                "template": "bar @ file://${output_dir}/deps/pip/bar.tar.gz",
            },
        ]
        build_config = BuildConfig(environment_variables=[], project_files=project_files)
        tmp_cwd.joinpath(".build-config.json").write_text(build_config.model_dump_json())
        return tmp_cwd

    @pytest.mark.parametrize("for_output_dir", [None, "/hermeto/output"])
    def test_inject_files(
        self,
        for_output_dir: str | None,
        tmp_cwd_as_output_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        tmp_path = tmp_cwd_as_output_dir

        if not for_output_dir:
            invoke_expecting_sucess(app, ["inject-files", str(tmp_path)])
        else:
            invoke_expecting_sucess(
                app, ["inject-files", str(tmp_path), "--for-output-dir", for_output_dir]
            )

        expect_output_dir = for_output_dir or tmp_path

        assert (
            tmp_path.joinpath("requirements.txt").read_text()
            == f"foo @ file://{expect_output_dir}/deps/pip/foo.tar.gz"
        )
        assert (
            tmp_path.joinpath("some-dir/requirements-extra.txt").read_text()
            == f"bar @ file://{expect_output_dir}/deps/pip/bar.tar.gz"
        )

        assert f"Overwriting {tmp_path / 'requirements.txt'}" in caplog.text
        assert f"Creating {tmp_path / 'some-dir' / 'requirements-extra.txt'}" in caplog.text


class TestMergeSboms:
    # Below is a high-level description of tests defined in this class.
    #
    # Feature: user-input errors are handled gracefully.
    #     Scenario outine: a user tries to merge SBOMs, but does not provide correct SBOM files.
    #     # A user can merge SBOMs with "hermeto merge-sboms" subcommand.
    #         When a user invokes "hermeto merge-sboms" with "incorrect_arguments"
    #         Then a user sees "error_pattern" in cacho2 response
    #     Examples:
    #             | incorrect_arguments         | error_message             |
    #             | no files                    | Missing argument          |
    #             | single file                 | Need at least two         |
    #             | same file multiple times    | Need at least two         |
    #             | not a JSON among some JSONS | does not look like        |
    #             | unsupported SBOM format     | a valid Hermeto SBOM       |
    #
    # Feature: user can merge SBOMS with a CLI command.
    #     Scenario outline: a user can merge several SBOMs.
    #         When a user invokes "hermeto merge-sboms" with "some_sbom_filenames"
    #         Then hermeto exits with return code of success.
    #     Examples:
    #             | some_sbom_filenames         |
    #             | two valid file names        |
    #             | three valid file names      |
    #
    #     Scenario outline: a user can merge several SBOMs and save results to a file.
    #         When a user invokes "hermeto merge-sboms -o tempfile" with "some_sbom_filenames".
    #         Then hermeto exits with return code of success.
    #          And tempfile contains merge result.
    #     Examples:
    #             | some_sbom_filenames         |
    #             | two valid file names        |
    #             | three valid file names      |
    @pytest.mark.parametrize(
        "sbom_files_to_merge, pattern",
        [
            ([], "Missing argument"),
            (["./tests/unit/data/sboms/hermeto.bom.json"], "Need at least two"),
            (
                [
                    "./tests/unit/data/sboms/hermeto.bom.json",
                    "./tests/unit/data/sboms/hermeto.bom.json",
                ],
                "Need at least two",
            ),
        ],
    )
    def test_a_user_sees_error_when_they_dont_provide_enough_unique_sboms_for_a_merge(
        self,
        sbom_files_to_merge: list[str],
        pattern: str,
    ) -> None:
        result = invoke_expecting_invalid_usage(app, ["merge-sboms", *sbom_files_to_merge])
        assert pattern in result.output

    @pytest.mark.parametrize(
        "sbom_files_to_merge, pattern",
        [
            (["./tests/unit/data/sboms/hermeto.bom.json", "./README.md"], "does not look like"),
        ],
    )
    def test_a_user_sees_error_when_they_provide_a_non_json_file_for_a_merge(
        self,
        sbom_files_to_merge: list[str],
        pattern: str,
    ) -> None:
        result = invoke_expecting_invalid_usage(app, ["merge-sboms", *sbom_files_to_merge])
        assert pattern in result.output

    @pytest.mark.parametrize(
        "sbom_files_to_merge, pattern",
        [
            (
                [
                    "./tests/unit/data/sboms/hermeto.bom.json",
                    "./tests/unit/data/sboms/syft.bom.json",
                ],
                f"a valid {APP_NAME} SBOM",
            ),
        ],
    )
    def test_a_user_sees_error_when_they_provide_a_non_hermeto_sbom_for_a_merge(
        self,
        sbom_files_to_merge: list[str],
        pattern: str,
    ) -> None:
        result = invoke_expecting_invalid_usage(app, ["merge-sboms", *sbom_files_to_merge])
        assert pattern in result.output

    @pytest.mark.parametrize(
        "sbom_files_to_merge",
        [
            [
                "./tests/unit/data/sboms/hermeto.bom.json",
                "./tests/unit/data/sboms/cachito_gomod.bom.json",
            ],
            [
                "./tests/unit/data/sboms/hermeto.bom.json",
                "./tests/unit/data/sboms/cachito_gomod.bom.json",
                "./tests/unit/data/sboms/cachito_gomod_nodeps.bom.json",
            ],
        ],
    )
    def test_a_user_can_successfully_merge_several_hermeto_sboms(
        self,
        sbom_files_to_merge: list[str],
    ) -> None:
        # Asserts exit code is 0. All subcomponents are tested elsewhere.
        invoke_expecting_sucess(app, ["merge-sboms", *sbom_files_to_merge])

    @pytest.mark.parametrize(
        "sbom_files_to_merge",
        [
            pytest.param(
                [
                    "./tests/unit/data/sboms/hermeto.bom.json",
                    "./tests/unit/data/sboms/cachito_gomod.bom.json",
                ],
                id="merge_our_own_cyclonedx",
            ),
            pytest.param(
                [
                    "./tests/unit/data/sboms/hermeto.bom.json",
                    "./tests/unit/data/sboms/cachito_gomod.bom.json",
                    "./tests/unit/data/sboms/cachito_gomod_nodeps.bom.json",
                ],
                id="merge_our_own_cyclonedx_more",
            ),
        ],
    )
    def test_a_user_can_successfully_save_sboms_merge_results_to_a_file(
        self,
        request: pytest.FixtureRequest,
        sbom_files_to_merge: list[str],
    ) -> None:
        prefix = f"{APP_NAME}-{Path(__file__).stem}-{request.node.callspec.id}-"
        with tempfile.NamedTemporaryFile(prefix=prefix) as fp:
            invoke_expecting_sucess(app, ["merge-sboms", "-o", fp.name, *sbom_files_to_merge])
            assert Path(fp.name).lstat().st_size > 0, "SBOM failed to be written to output file!"

    @pytest.mark.parametrize(
        "sbom_files_to_merge",
        [
            [
                "./tests/unit/data/sboms/hermeto.bom.spdx.json",
                "./tests/unit/data/sboms/something.simple0.100.0.spdx.pretty.json",
            ],
        ],
    )
    def test_a_user_can_successfully_save_sboms_merge_results_to_a_file_in_spdx_format(
        self,
        request: pytest.FixtureRequest,
        sbom_files_to_merge: list[str],
    ) -> None:
        prefix = f"{APP_NAME}-{Path(__file__).stem}-{request.node.callspec.id}-"
        with tempfile.NamedTemporaryFile(prefix=prefix) as fp:
            invoke_expecting_sucess(
                app,
                ["merge-sboms", "-o", fp.name, "--sbom-output-type", "spdx", *sbom_files_to_merge],
            )
            assert Path(fp.name).lstat().st_size > 0, "SBOM failed to be written to output file!"

    @pytest.mark.parametrize(
        "sbom_files_to_merge",
        [
            pytest.param(
                [
                    "./tests/unit/data/sboms/hermeto.bom.json",
                    "./tests/unit/data/sboms/syft.bom.spdx.json",
                ],
                id="merge_mixed_format",
            ),
        ],
    )
    def test_a_user_can_successfully_save_mixed_sboms_merge_results_to_a_file_in_spdx_format(
        self,
        request: pytest.FixtureRequest,
        sbom_files_to_merge: list[str],
    ) -> None:
        prefix = f"{APP_NAME}-{Path(__file__).stem}-{request.node.callspec.id}-"
        with tempfile.NamedTemporaryFile(prefix=prefix) as fp:
            invoke_expecting_sucess(
                app,
                ["merge-sboms", "-o", fp.name, "--sbom-output-type", "spdx", *sbom_files_to_merge],
            )
            assert Path(fp.name).lstat().st_size > 0, "SBOM failed to be written to output file!"
