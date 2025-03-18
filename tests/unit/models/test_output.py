from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List

import pydantic
import pytest

from cachi2.core.errors import BaseError
from cachi2.core.models.output import BuildConfig, EnvironmentVariable, ProjectFile, RequestOutput


class TestProjectFile:
    def test_resolve_content(self) -> None:
        template = dedent(
            """
            no placeholders
            $unknown_placeholder
            invalid placeholder: $5
            ${output_dir}/deps/gomod
            file://$output_dir/deps/pip
            """
        )
        expect_content = dedent(
            """
            no placeholders
            $unknown_placeholder
            invalid placeholder: $5
            /some/output/deps/gomod
            file:///some/output/deps/pip
            """
        )
        project_file = ProjectFile(abspath="/some/path", template=template)
        assert project_file.resolve_content(Path("/some/output")) == expect_content


class TestBuildConfig:
    def test_conflicting_env_vars(self) -> None:
        expect_error = (
            "conflict by GOSUMDB: "
            "name='GOSUMDB' value='on' kind=None "
            "X name='GOSUMDB' value='sum.golang.org' kind=None"
        )
        with pytest.raises(pydantic.ValidationError, match=expect_error):
            BuildConfig(
                environment_variables=[
                    {"name": "GOSUMDB", "value": "on"},
                    {"name": "GOSUMDB", "value": "sum.golang.org"},
                ],
                project_files=[],
            )

    def test_sort_and_dedupe_env_vars(self) -> None:
        build_config = BuildConfig(
            environment_variables=[
                {"name": "B", "value": "y"},
                {"name": "A", "value": "x"},
                {"name": "B", "value": "y"},
            ],
            project_files=[],
        )
        assert build_config.environment_variables == [
            EnvironmentVariable(name="A", value="x"),
            EnvironmentVariable(name="B", value="y"),
        ]

    def test_conflicting_project_files(self) -> None:
        expect_error = "conflict by /some/path:"
        with pytest.raises(pydantic.ValidationError, match=expect_error):
            BuildConfig(
                environment_variables=[],
                project_files=[
                    {"abspath": "/some/path", "template": "foo"},
                    {"abspath": "/some/path", "template": "bar"},
                ],
            )

    def test_sort_and_dedupe_project_files(self) -> None:
        build_config = BuildConfig(
            environment_variables=[],
            project_files=[
                {"abspath": "/second/path", "template": "bar"},
                {"abspath": "/first/path", "template": "foo"},
                {"abspath": "/second/path", "template": "bar"},
            ],
        )
        assert build_config.project_files == [
            ProjectFile(abspath="/first/path", template="foo"),
            ProjectFile(abspath="/second/path", template="bar"),
        ]


class TestRequestOutput:
    @pytest.mark.parametrize(
        "input_data, expected_data",
        [
            (
                {"components": [{"name": "mypkg", "purl": "pkg:generic/mypkg"}]},
                RequestOutput(
                    components=[{"name": "mypkg", "purl": "pkg:generic/mypkg"}],
                    build_config=BuildConfig(),
                ),
            ),
            (
                {
                    "components": [{"name": "mypkg", "purl": "pkg:generic/mypkg"}],
                    "environment_variables": [{"name": "a", "value": "y"}],
                    "project_files": [{"abspath": "/first/path", "template": "foo"}],
                },
                RequestOutput(
                    components=[{"name": "mypkg", "purl": "pkg:generic/mypkg"}],
                    build_config=BuildConfig(
                        environment_variables=[EnvironmentVariable(name="a", value="y")],
                        project_files=[ProjectFile(abspath="/first/path", template="foo")],
                    ),
                ),
            ),
        ],
    )
    def test_create_from_obj_lists(
        self, input_data: dict[str, Any], expected_data: RequestOutput
    ) -> None:
        request_output = RequestOutput.from_obj_list(**input_data)
        assert request_output == expected_data


ENVVAR_TEMPLATE_MAPPINGS = {
    "NESTED": "monty_${FOO}",
    "BAZ": "holy_grail",
    "BAR": "and",
    "BARR": "the_${BAZ}",
    "FOO": "python_${BAR}_${BARR}",
    "LEGACY_LITERAL": "foobar",
    "LEGACY_PATH": "relative/path",
    "SIMPLE": "${deadbeef}",
}


class TestEnvironmentVariable:
    @pytest.fixture(scope="class")
    def env_variables(self) -> Dict[str, EnvironmentVariable]:
        ret = {k: EnvironmentVariable(name=k, value=v) for k, v in ENVVAR_TEMPLATE_MAPPINGS.items()}

        # need to inject 'kind' for legacy variable templates
        ret["LEGACY_LITERAL"].kind = "literal"
        ret["LEGACY_PATH"].kind = "path"
        return ret

    @pytest.mark.parametrize(
        "var, expected, mappings",
        [
            pytest.param(
                "LEGACY_LITERAL",
                "foobar",
                {"output_dir": "/absolute/path"},
                id="compatibility_test_legacy_literal",
            ),
            pytest.param(
                "LEGACY_PATH",
                "/absolute/path/relative/path",
                {"output_dir": "/absolute/path"},
                id="compatibility_test_legacy_path",
            ),
            pytest.param(
                "SIMPLE", "badf00d", {"deadbeef": "badf00d"}, id="simple_template_variable"
            ),
            pytest.param(
                "NESTED",
                "monty_python_and_the_holy_grail",
                ENVVAR_TEMPLATE_MAPPINGS,
                id="nested_template_variable",
            ),
        ],
    )
    def test_resolution(
        self,
        env_variables: Dict[str, EnvironmentVariable],
        var: str,
        expected: str,
        mappings: Dict[str, str],
    ) -> None:

        assert env_variables[var].resolve_value(mappings) == expected
        assert "kind" not in env_variables[var].model_dump()

    @pytest.mark.parametrize(
        "envs",
        [
            pytest.param(
                [EnvironmentVariable(name="FOO", value="$FOO")],
                id="recursive_variable",
            ),
            pytest.param(
                [
                    EnvironmentVariable(name="VAR1", value="$VAR2"),
                    EnvironmentVariable(name="VAR2", value="$VAR3"),
                    EnvironmentVariable(name="VAR3", value="$VAR1"),
                ],
                id="indirect_cycle",
            ),
        ],
    )
    def test_nested_resolution_failure(self, envs: List[EnvironmentVariable]) -> None:
        mappings = {e.name: e.value for e in envs}

        err_msg = f"Detected a cycle in environment variable expansion of '{envs[0].name}'"
        with pytest.raises(BaseError, match=err_msg):
            envs[0].resolve_value(mappings)
