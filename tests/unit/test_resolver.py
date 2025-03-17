import re
from pathlib import Path
from typing import Callable
from unittest import mock

import pytest

from cachi2.core import resolver
from cachi2.core.errors import UnsupportedFeature
from cachi2.core.models.input import Request
from cachi2.core.models.output import BuildConfig, EnvironmentVariable, ProjectFile, RequestOutput
from cachi2.core.models.sbom import Component
from cachi2.core.rooted_path import RootedPath

GOMOD_OUTPUT = RequestOutput.from_obj_list(
    components=[
        Component(
            type="library",
            name="github.com/foo/bar",
            version="v1.0.0",
            purl="pkg:golang/github.com/foo/bar@v1.0.0",
        )
    ],
    environment_variables=[
        EnvironmentVariable(name="GOMODCACHE", value="deps/gomod/pkg/mod", kind="path"),
    ],
    project_files=[
        ProjectFile(abspath="/your/project/go.mod", template="Hello gomod my old friend.")
    ],
)

PIP_OUTPUT = RequestOutput.from_obj_list(
    components=[
        Component(type="library", name="spam", version="1.0.0", purl="pkg:pypi/spam@1.0.0")
    ],
    environment_variables=[
        EnvironmentVariable(name="PIP_INDEX_URL", value="file:///some/path", kind="literal"),
    ],
    project_files=[
        ProjectFile(
            abspath="/your/project/requirements.txt", template="I've come to talk with you again."
        ),
    ],
)

NPM_OUTPUT = RequestOutput.from_obj_list(
    components=[Component(type="library", name="eggs", version="1.0.0", purl="pkg:npm/eggs@1.0.0")],
    environment_variables=[
        EnvironmentVariable(name="CHROMEDRIVER_SKIP_DOWNLOAD", value="true", kind="literal"),
    ],
    project_files=[
        ProjectFile(
            abspath="/your/project/package-lock.json", template="Because a vision softly creeping."
        )
    ],
)

COMBINED_OUTPUT = RequestOutput.from_obj_list(
    components=GOMOD_OUTPUT.components + NPM_OUTPUT.components + PIP_OUTPUT.components,
    environment_variables=(
        GOMOD_OUTPUT.build_config.environment_variables
        + PIP_OUTPUT.build_config.environment_variables
        + NPM_OUTPUT.build_config.environment_variables
    ),
    project_files=(
        GOMOD_OUTPUT.build_config.project_files
        + PIP_OUTPUT.build_config.project_files
        + NPM_OUTPUT.build_config.project_files
    ),
)


def test_resolve_packages(tmp_path: Path) -> None:
    request = Request(
        source_dir=tmp_path,
        output_dir=tmp_path,
        packages=[{"type": "pip"}, {"type": "npm"}, {"type": "gomod"}],
    )

    calls_by_pkgtype = []

    def mock_fetch(pkgtype: str, output: RequestOutput) -> Callable[[Request], RequestOutput]:
        def fetch(req: Request) -> RequestOutput:
            assert req == request
            calls_by_pkgtype.append(pkgtype)
            return output

        return fetch

    with mock.patch.dict(
        resolver._package_managers,
        {
            "gomod": mock_fetch("gomod", GOMOD_OUTPUT),
            "npm": mock_fetch("npm", NPM_OUTPUT),
            "pip": mock_fetch("pip", PIP_OUTPUT),
        },
    ):
        assert resolver.resolve_packages(request) == COMBINED_OUTPUT

    assert calls_by_pkgtype == ["gomod", "npm", "pip"]


@pytest.mark.parametrize(
    "packages, copy_exists",
    [
        pytest.param([{"type": "yarn"}], True, id="single_package"),
        pytest.param(
            [{"type": "gomod"}, {"type": "pip"}, {"type": "npm"}], False, id="multiple_packages"
        ),
    ],
)
@mock.patch("cachi2.core.resolver._resolve_packages")
def test_source_dir_copy(
    mock_resolve_packages: mock.Mock,
    packages: list[dict[str, str]],
    copy_exists: bool,
    tmp_path: Path,
) -> None:
    request = Request(
        source_dir=tmp_path,
        output_dir=tmp_path,
        packages=packages,
    )

    def _resolve_packages(request: Request) -> RequestOutput:
        if copy_exists:
            tmp_dir_name = request.source_dir.path.name

            # assert a temporary directory is being used
            assert tmp_dir_name != tmp_path.name
            assert tmp_dir_name.startswith("tmp")
            assert tmp_dir_name.endswith(".cachi2-source-copy")
        else:
            # assert the original source_dir is being used
            assert request.source_dir == RootedPath(tmp_path)

        return RequestOutput.empty()

    mock_resolve_packages.side_effect = _resolve_packages

    resolver.resolve_packages(request)

    # assert source_dir is restored to the original value
    assert request.source_dir == RootedPath(tmp_path)


@mock.patch("cachi2.core.resolver._resolve_packages")
def test_project_files_fix_for_work_copy(
    mock_resolve_packages: mock.Mock,
    tmp_path: Path,
) -> None:
    request = Request(
        source_dir=tmp_path,
        output_dir=tmp_path,
        packages=[{"type": "yarn"}],
    )

    def _resolve_packages(request: Request) -> RequestOutput:
        # assert request is based on a copy of the source dir
        assert request.source_dir.path != tmp_path
        assert request.source_dir.path.name.endswith(".cachi2-source-copy")

        # return a project file pointing to the source dir copy
        return RequestOutput(
            components=[],
            build_config=BuildConfig(
                environment_variables=[],
                project_files=[
                    ProjectFile(abspath=request.source_dir.path / "package.json", template="n/a"),
                ],
            ),
        )

    mock_resolve_packages.side_effect = _resolve_packages
    output = resolver.resolve_packages(request)

    # assert the project file path was corrected to point to the original source dir
    assert output.build_config.project_files[0].abspath == tmp_path / "package.json"


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(["dev-package-managers"], id="dev-package-managers-true"),
        pytest.param([], id="dev-package-managers-false"),
    ],
)
def test_dev_mode(flags: list[str], tmp_path: Path) -> None:
    mock_resolver = mock.Mock()
    mock_resolver.return_value = RequestOutput.empty()
    with (
        mock.patch.dict(
            resolver._package_managers,
            values={"gomod": mock_resolver},
            clear=True,
        ),
        mock.patch.dict(
            resolver._dev_package_managers,
            values={"shrubbery": mock_resolver},
            clear=True,
        ),
    ):
        dev_package_input = mock.Mock()
        dev_package_input.type = "shrubbery"

        request = mock.Mock()
        request.source_dir = RootedPath(tmp_path)
        request.flags = flags
        request.packages = [dev_package_input]

        if flags:
            assert resolver.resolve_packages(request) == RequestOutput(
                components=[], build_config=BuildConfig(environment_variables=[], project_files=[])
            )
        else:
            expected_error = re.escape("Package manager(s) not yet supported: shrubbery")
            with pytest.raises(UnsupportedFeature, match=expected_error):
                resolver.resolve_packages(request)


def test_resolve_with_released_and_dev_package_managers(tmp_path: Path) -> None:
    mock_resolve_gomod = mock.Mock(return_value=RequestOutput.empty())
    mock_resolve_pip = mock.Mock(return_value=RequestOutput.empty())

    with (
        mock.patch.dict(
            resolver._package_managers,
            values={"gomod": mock_resolve_gomod},
            clear=True,
        ),
        mock.patch.dict(
            resolver._dev_package_managers,
            values={"pip": mock_resolve_pip},
            clear=True,
        ),
    ):
        dev_package_input = mock.Mock()
        dev_package_input.type = "pip"

        released_package_input = mock.Mock()
        released_package_input.type = "gomod"

        request = mock.Mock()
        request.source_dir = RootedPath(tmp_path)
        request.flags = ["dev-package-managers"]
        request.packages = [released_package_input, dev_package_input]

        resolver.resolve_packages(request)

        mock_resolve_gomod.assert_has_calls([mock.call(request)])
        mock_resolve_pip.assert_has_calls([mock.call(request)])
