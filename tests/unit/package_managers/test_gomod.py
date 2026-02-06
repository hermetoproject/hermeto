# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import re
import subprocess
import textwrap
from collections.abc import Iterator
from pathlib import Path
from string import Template
from typing import Any, Literal
from unittest import mock

import git
import pytest

from hermeto import APP_NAME
from hermeto.core.constants import Mode
from hermeto.core.errors import (
    FetchError,
    LockfileNotFound,
    NotAGitRepo,
    PackageManagerError,
    UnexpectedFormat,
)
from hermeto.core.models.input import Flag, Request
from hermeto.core.models.output import BuildConfig, EnvironmentVariable, RequestOutput
from hermeto.core.models.sbom import Annotation, Component, Property, PropertyEnum
from hermeto.core.package_managers.gomod import (
    Go,
    GoVersion,
    GoWork,
    Module,
    ModuleDict,
    ModuleID,
    ModuleVersionResolver,
    Package,
    ParsedGoWork,
    ParsedModule,
    ParsedPackage,
    ResolvedGoModule,
    StandardPackage,
    _create_main_module_from_parsed_data,
    _create_modules_from_parsed_data,
    _create_packages_from_parsed_data,
    _deduplicate_resolved_modules,
    _disable_telemetry,
    _get_go_sum_files,
    _get_go_work_path,
    _get_gomod_version,
    _get_repository_name,
    _go_exec_env,
    _go_list_deps,
    _list_installed_toolchains,
    _list_toolchain_files,
    _parse_go_sum,
    _parse_local_modules,
    _parse_packages,
    _parse_vendor,
    _parse_workspace_module,
    _process_modules_json_stream,
    _resolve_gomod,
    _select_toolchain,
    _validate_local_replacements,
    _vendor_changed,
    _vendor_deps,
    fetch_gomod_source,
)
from hermeto.core.rooted_path import PathOutsideRoot, RootedPath
from hermeto.core.scm import GitRepo, RepoID
from hermeto.core.utils import GIT_PRISTINE_ENV, load_json_stream
from tests.common_utils import GIT_REF, Symlink, write_file_tree

GO_CMD_PATH = "/usr/bin/go"


@pytest.fixture(scope="module")
def env_variables() -> list[EnvironmentVariable]:
    return [
        EnvironmentVariable(name="GOCACHE", value="${output_dir}/deps/gomod"),
        EnvironmentVariable(name="GOMODCACHE", value="${output_dir}/deps/gomod/pkg/mod"),
        EnvironmentVariable(name="GOPATH", value="${output_dir}/deps/gomod"),
        EnvironmentVariable(name="GOPROXY", value="file://${GOMODCACHE}/cache/download"),
    ]


@pytest.fixture(scope="module", autouse=True)
def mock_which_go() -> Iterator[None]:
    """Make shutil.which return GO_CMD_PATH for all the tests in this file.

    Whenever we execute a command, we use shutil.which to look for it first. To ensure
    that these tests don't depend on the state of the developer's machine, the returned
    go path must be mocked.
    """
    with mock.patch("shutil.which") as mock_which:
        mock_which.return_value = GO_CMD_PATH
        yield


@pytest.fixture(autouse=True)
def mock_go_release() -> Iterator[mock.MagicMock]:
    with mock.patch("hermeto.core.package_managers.gomod.Go._get_release") as _mock:
        # Using a side_effect instead of return_value because return_value always takes precedence
        # and we would not be able to override this easily.
        _mock.side_effect = lambda: "go1.21.0"
        yield _mock


@pytest.fixture
def gomod_input_packages() -> list[dict[str, str]]:
    return [{"type": "gomod"}]


@pytest.fixture
def gomod_request(tmp_path: Path, gomod_input_packages: list[dict[str, str]]) -> Request:
    # Create folder in the specified path, otherwise Request validation would fail
    for package in gomod_input_packages:
        if "path" in package:
            (tmp_path / package["path"]).mkdir(exist_ok=True)

    return Request(
        source_dir=tmp_path,
        output_dir=tmp_path / "output",
        packages=gomod_input_packages,
    )


@pytest.fixture
def go_mod_file(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    output_file = tmp_path / "go.mod"

    with open(output_file, "w") as f:
        f.write(request.param)


def mock_go_class(binary: str) -> mock.Mock:
    """Create a mock Go instance with a specific binary path."""
    mock_go = mock.Mock(spec=Go)
    mock_go.binary = binary
    return mock_go


def proc_mock(
    args: str | list[str] = "", *, returncode: int, stdout: str | None
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, returncode=returncode, stdout=stdout)


def get_mock_dir(data_dir: Path) -> Path:
    return data_dir / "gomod-mocks"


def get_mocked_data(data_dir: Path, filepath: str | Path) -> str:
    return get_mock_dir(data_dir).joinpath(filepath).read_text()


def _parse_mocked_data(data_dir: Path, file_path: str) -> ResolvedGoModule:
    mocked_data = json.loads(get_mocked_data(data_dir, file_path))

    main_module = ParsedModule(**mocked_data["main_module"])
    modules = {ParsedModule(**module) for module in mocked_data["modules"]}
    packages = {ParsedPackage(**package) for package in mocked_data["packages"]}
    modules_in_go_sum = frozenset(
        (name, version) for name, version in mocked_data["modules_in_go_sum"]
    )

    return ResolvedGoModule(main_module, modules, packages, modules_in_go_sum)


def _parse_go_list_deps_data(data_dir: Path, file_path: str) -> list[ParsedPackage]:
    mocked_data = load_json_stream(get_mocked_data(data_dir, file_path))
    return [ParsedPackage.model_validate(pkg) for pkg in mocked_data]


@pytest.mark.parametrize(
    "cgo_disable, has_workspaces",
    (
        pytest.param(False, False, id="cgo_disabled"),
        pytest.param(True, False, id="cgo_enabled"),
        pytest.param(False, True, id="has_workspaces"),
    ),
)
@mock.patch("hermeto.core.package_managers.gomod.GoWork._get_go_work")
@mock.patch("hermeto.core.package_managers.gomod._go_list_deps")
@mock.patch("hermeto.core.package_managers.gomod._parse_packages")
@mock.patch("hermeto.core.package_managers.gomod._disable_telemetry")
@mock.patch("hermeto.core.package_managers.gomod._get_gomod_version")
@mock.patch("hermeto.core.package_managers.gomod.ModuleVersionResolver")
@mock.patch("hermeto.core.package_managers.gomod._validate_local_replacements")
@mock.patch("subprocess.run")
def test_resolve_gomod(
    mock_run: mock.Mock,
    mock_validate_local_replacements: mock.Mock,
    mock_version_resolver: mock.Mock,
    mock_get_gomod_version: mock.Mock,
    mock_disable_telemetry: mock.Mock,
    mock_parse_packages: mock.Mock,
    mock_go_list_deps: mock.Mock,
    mock_get_go_work: mock.Mock,
    cgo_disable: bool,
    has_workspaces: bool,
    tmp_path: Path,
    data_dir: Path,
    gomod_request: Request,
) -> None:
    module_dir = gomod_request.source_dir.join_within_root("path/to/module")
    module_dir.path.mkdir(parents=True, exist_ok=True)
    mocked_data_folder = "non-vendored" if not has_workspaces else "workspaces"
    mock_disable_telemetry.return_value = None

    go_work = None
    go = Go()

    # Mock the "subprocess.run" calls
    run_side_effects = []

    run_side_effects.append(
        proc_mock(
            "go mod download -json",
            returncode=0,
            stdout=get_mocked_data(data_dir, f"{mocked_data_folder}/go_mod_download.json"),
        )
    )

    run_side_effects.append(
        proc_mock(
            "go list -e -m",
            returncode=0,
            stdout=get_mocked_data(data_dir, f"{mocked_data_folder}/go_list_modules.json").replace(
                "{repo_dir}", str(module_dir)
            ),
        )
    )
    mock_run.side_effect = run_side_effects
    mock_go_list_deps.side_effect = [
        _parse_go_list_deps_data(data_dir, f"{mocked_data_folder}/go_list_deps_all.json"),
        _parse_go_list_deps_data(data_dir, f"{mocked_data_folder}/go_list_deps_threedot.json"),
    ]

    mock_version_resolver.get_golang_version.return_value = "v0.1.0"
    mock_get_gomod_version.return_value = ("0.1.1", "0.1.2")

    parse_packages_mocked_data: list[ParsedPackage] = []
    mock_parse_packages.return_value = parse_packages_mocked_data

    if has_workspaces:
        go_work_path = module_dir.join_within_root("go.work")
        go_work_path.path.symlink_to(get_mock_dir(data_dir) / "workspaces/go_work.json")
        module_dir.join_within_root("go.sum").path.write_text(
            get_mocked_data(data_dir, "workspaces/go.sum")
        )
        mock_get_go_work.return_value = go_work_path.path.read_text()
        go_work = GoWork.from_file(go_work_path, go)

        # we need to mock _parse_packages queries to all workspace module directories
        for wsp in go_work.workspace_paths:
            fp = f"{wsp.relative_to(go_work.path.parent)}/go_list_deps_threedot.json"
            mocked_data = _parse_go_list_deps_data(data_dir, f"workspaces/{fp}")
            parse_packages_mocked_data.extend(mocked_data)

    flags: list[Flag] = []
    if cgo_disable:
        flags.append("cgo-disable")

    gomod_request.flags = frozenset(flags)

    module_dir.path.mkdir(parents=True, exist_ok=True)
    module_dir.join_within_root("go.sum").path.write_text(
        get_mocked_data(data_dir, f"{mocked_data_folder}/go.sum")
    )

    resolve_result = _resolve_gomod(
        module_dir, gomod_request, tmp_path, mock_version_resolver, go, go_work
    )

    assert mock_run.call_args_list[0][1]["env"]["GOMODCACHE"] == f"{tmp_path}/pkg/mod"

    # Assert that _parse_packages was called exactly once.
    # Assert that the module-parsing _go_list_deps call was called with the 'all' pattern. The
    # other _go_list_deps invocations from resolve_gomod are wrapped by _parse_packages and tested
    # in test_parse_packages.
    mock_parse_packages.assert_called_once()
    mock_go_list_deps.assert_called_once()
    assert "all" in mock_go_list_deps.call_args[0]

    for call in mock_run.call_args_list:
        env = call.kwargs["env"]
        if cgo_disable:
            assert env["CGO_ENABLED"] == "0"
        else:
            assert "CGO_ENABLED" not in env

    if has_workspaces:
        expect_result = _parse_mocked_data(
            data_dir, "expected-results/resolve_gomod_workspaces.json"
        )
    else:
        expect_result = _parse_mocked_data(data_dir, "expected-results/resolve_gomod.json")

    assert resolve_result.parsed_main_module == expect_result.parsed_main_module
    assert set(resolve_result.parsed_modules) == expect_result.parsed_modules
    # skip comparing parsed packages, these are tested using the same data in test_parse_packages
    assert resolve_result.modules_in_go_sum == expect_result.modules_in_go_sum

    mock_validate_local_replacements.assert_called_once_with(
        resolve_result.parsed_modules, module_dir
    )


@mock.patch("hermeto.core.package_managers.gomod._disable_telemetry")
@mock.patch("hermeto.core.package_managers.gomod._get_gomod_version")
@mock.patch("hermeto.core.package_managers.gomod.ModuleVersionResolver")
@mock.patch("hermeto.core.package_managers.gomod._validate_local_replacements")
@mock.patch("hermeto.core.package_managers.gomod._vendor_changed")
@mock.patch("subprocess.run")
def test_resolve_gomod_vendor_dependencies(
    mock_run: mock.Mock,
    mock_vendor_changed: mock.Mock,
    mock_validate_local_replacements: mock.Mock,
    mock_version_resolver: mock.Mock,
    mock_get_gomod_version: mock.Mock,
    mock_disable_telemetry: mock.Mock,
    tmp_path: Path,
    data_dir: Path,
    gomod_request: Request,
) -> None:
    module_dir = gomod_request.source_dir.join_within_root("path/to/module")
    mock_disable_telemetry.return_value = None

    # Mock the "subprocess.run" calls
    run_side_effects = []
    run_side_effects.append(proc_mock("go mod vendor", returncode=0, stdout=None))
    run_side_effects.append(
        proc_mock(
            "go list -e -m -json",
            returncode=0,
            stdout=get_mocked_data(data_dir, "non-vendored/go_list_modules.json").replace(
                "{repo_dir}", str(module_dir)
            ),
        )
    )
    run_side_effects.append(
        proc_mock(
            "go list -e -deps -json all",
            returncode=0,
            stdout=get_mocked_data(data_dir, "vendored/go_list_deps_all.json"),
        )
    )
    run_side_effects.append(
        proc_mock(
            "go list -e -deps -json ./...",
            returncode=0,
            stdout=get_mocked_data(data_dir, "vendored/go_list_deps_threedot.json"),
        )
    )
    mock_run.side_effect = run_side_effects

    mock_version_resolver.get_golang_version.return_value = "v0.1.0"
    mock_get_gomod_version.return_value = ("0.1.1", "0.1.2")
    mock_vendor_changed.return_value = False

    module_dir.join_within_root("vendor").path.mkdir(parents=True)
    module_dir.join_within_root("vendor/modules.txt").path.write_text(
        get_mocked_data(data_dir, "vendored/modules.txt")
    )
    module_dir.join_within_root("go.sum").path.write_text(
        get_mocked_data(data_dir, "vendored/go.sum")
    )

    resolve_result = _resolve_gomod(
        module_dir, gomod_request, tmp_path, mock_version_resolver, Go(), None
    )

    assert mock_run.call_args_list[0][0][0] == [GO_CMD_PATH, "mod", "vendor"]
    assert mock_run.call_args_list[0][1]["env"]["GOMODCACHE"] == f"{tmp_path}/vendor-cache"
    assert mock_run.call_args_list[-2][0][0] == [
        GO_CMD_PATH,
        "list",
        "-e",
        "-deps",
        "-json=ImportPath,Module,Standard,Deps",
        "all",
    ]

    expect_result = _parse_mocked_data(data_dir, "expected-results/resolve_gomod_vendored.json")

    assert resolve_result.parsed_main_module == expect_result.parsed_main_module
    assert set(resolve_result.parsed_modules) == expect_result.parsed_modules
    assert set(resolve_result.parsed_packages) == expect_result.parsed_packages
    assert resolve_result.modules_in_go_sum == expect_result.modules_in_go_sum


@mock.patch("hermeto.core.package_managers.gomod._disable_telemetry")
@mock.patch("hermeto.core.package_managers.gomod._get_gomod_version")
@mock.patch("hermeto.core.package_managers.gomod.ModuleVersionResolver")
@mock.patch("subprocess.run")
def test_resolve_gomod_no_deps(
    mock_run: mock.Mock,
    mock_version_resolver: mock.Mock,
    mock_get_gomod_version: mock.Mock,
    mock_disable_telemetry: mock.Mock,
    tmp_path: Path,
    gomod_request: Request,
) -> None:
    module_path = gomod_request.source_dir.join_within_root("path/to/module")
    module_path.path.mkdir(parents=True, exist_ok=True)
    mock_disable_telemetry.return_value = None

    mock_pkg_deps_no_deps = textwrap.dedent(
        """
        {
            "ImportPath": "github.com/release-engineering/retrodep/v2",
            "Module": {
                "Path": "github.com/release-engineering/retrodep/v2",
                "Main": true
            }
        }
        """
    )

    mock_go_list_modules = Template(
        """
        {
            "Path": "github.com/release-engineering/retrodep/v2",
            "Main": true,
            "Dir": "$repo_dir",
            "GoMod": "$repo_dir/go.mod",
            "GoVersion": "1.19"
        }
        """
    ).substitute({"repo_dir": str(module_path)})

    # Mock the "subprocess.run" calls
    run_side_effects = []
    run_side_effects.append(proc_mock("go mod download -json", returncode=0, stdout=""))
    run_side_effects.append(
        proc_mock(
            "go list -e -m",
            returncode=0,
            stdout=mock_go_list_modules,
        )
    )
    run_side_effects.append(
        proc_mock("go list -e -deps -json all", returncode=0, stdout=mock_pkg_deps_no_deps)
    )
    run_side_effects.append(
        proc_mock("go list -e -deps -json ./...", returncode=0, stdout=mock_pkg_deps_no_deps)
    )
    mock_run.side_effect = run_side_effects

    mock_version_resolver.get_golang_version.return_value = "v1.21.4"
    mock_get_gomod_version.return_value = ("1.21.4", None)

    main_module, modules, packages, _ = _resolve_gomod(
        module_path, gomod_request, tmp_path, mock_version_resolver, Go(), None
    )
    packages_list = list(packages)

    assert main_module == ParsedModule(
        path="github.com/release-engineering/retrodep/v2",
        version="v1.21.4",
        main=True,
    )

    assert not modules
    assert len(packages_list) == 1
    assert packages_list[0] == ParsedPackage(
        import_path="github.com/release-engineering/retrodep/v2",
        module=ParsedModule(
            path="github.com/release-engineering/retrodep/v2",
            main=True,
        ),
    )


@pytest.mark.parametrize(
    "symlinked_file",
    [
        "go.mod",
        "go.sum",
        "vendor/modules.txt",
        "some-package/foo.go",
        "vendor/github.com/foo/bar/main.go",
    ],
)
def test_resolve_gomod_suspicious_symlinks(symlinked_file: str, gomod_request: Request) -> None:
    tmp_path = gomod_request.source_dir.path
    tmp_path.joinpath(symlinked_file).parent.mkdir(parents=True, exist_ok=True)
    tmp_path.joinpath(symlinked_file).symlink_to("/foo")
    version_resolver = mock.Mock(spec=ModuleVersionResolver)
    go_work = mock.Mock(spec=GoWork)

    app_dir = gomod_request.source_dir

    with pytest.raises(PathOutsideRoot):
        _resolve_gomod(app_dir, gomod_request, tmp_path, version_resolver, Go(), go_work)


@pytest.mark.parametrize(
    "go_sum_content, expect_modules",
    [
        (None, set()),
        ("", set()),
        (
            textwrap.dedent(
                """
                github.com/creack/pty v1.1.18 h1:n56/Zwd5o6whRC5PMGretI4IdRLlmBXYNjScPaBgsbY=

                github.com/davecgh/go-spew v1.1.0/go.mod h1:J7Y8YcW2NihsgmVo/mv3lAwl/skON4iLHjSsI+c5H38=

                github.com/davecgh/go-spew v1.1.1 h1:vj9j/u1bqnvCEfJOwUhtlOARqs3+rkHYY13jYWTU97c=
                github.com/davecgh/go-spew v1.1.1/go.mod h1:J7Y8YcW2NihsgmVo/mv3lAwl/skON4iLHjSsI+c5H38=

                github.com/moby/term v0.0.0-20221205130635-1aeaba878587 h1:HfkjXDfhgVaN5rmueG8cL8KKeFNecRCXFhaJ2qZ5SKA=
                github.com/moby/term v0.0.0-20221205130635-1aeaba878587/go.mod h1:8FzsFHVUBGZdbDsJw/ot+X+d5HLUbvklYLJ9uGfcI3Y=
                """
            ),
            {
                ("github.com/creack/pty", "v1.1.18"),  # has the .zip checksum => include it
                # ("github.com/davecgh/go-spew", "v1.1.0"),  # only the .mod checksum => exclude it
                ("github.com/davecgh/go-spew", "v1.1.1"),
                ("github.com/moby/term", "v0.0.0-20221205130635-1aeaba878587"),
            },
        ),
    ],
)
def test_parse_go_sum(
    go_sum_content: str | None,
    expect_modules: set[ModuleID],
    rooted_tmp_path: RootedPath,
) -> None:
    go_sum_file = rooted_tmp_path.join_within_root("go.sum")

    if go_sum_content is not None:
        go_sum_file.path.write_text(go_sum_content)

    parsed_modules = _parse_go_sum(go_sum_file)
    assert frozenset(expect_modules) == parsed_modules


def test_parse_broken_go_sum(rooted_tmp_path: RootedPath, caplog: pytest.LogCaptureFixture) -> None:
    go_sum_content = textwrap.dedent(
        """\
        github.com/creack/pty v1.1.18 h1:n56/Zwd5o6whRC5PMGretI4IdRLlmBXYNjScPaBgsbY=
        github.com/davecgh/go-spew v1.1.0/go.mod
        github.com/davecgh/go-spew v1.1.1 h1:vj9j/u1bqnvCEfJOwUhtlOARqs3+rkHYY13jYWTU97c=
        github.com/davecgh/go-spew v1.1.1/go.mod h1:J7Y8YcW2NihsgmVo/mv3lAwl/skON4iLHjSsI+c5H38=
        github.com/moby/term v0.0.0-20221205130635-1aeaba878587 h1:HfkjXDfhgVaN5rmueG8cL8KKeFNecRCXFhaJ2qZ5SKA=
        github.com/moby/term v0.0.0-20221205130635-1aeaba878587/go.mod h1:8FzsFHVUBGZdbDsJw/ot+X+d5HLUbvklYLJ9uGfcI3Y=
        """
    )
    expect_modules = frozenset([("github.com/creack/pty", "v1.1.18")])

    submodule = rooted_tmp_path.join_within_root("submodule")
    submodule.path.mkdir()
    go_sum_file = submodule.join_within_root("go.sum")
    go_sum_file.path.write_text(go_sum_content)

    assert _parse_go_sum(go_sum_file) == expect_modules
    assert caplog.messages == [
        "submodule/go.sum:2: malformed line, skipping the rest of the file: 'github.com/davecgh/go-spew v1.1.0/go.mod'",
    ]


@mock.patch("hermeto.core.package_managers.gomod.ModuleVersionResolver")
def test_parse_local_modules(version_resolver: mock.Mock) -> None:
    go_list_m_json = """
    {
        "Path": "myorg.com/my-project",
        "Main": true,
        "Dir": "/path/to/project"
    }
    {
        "Path": "myorg.com/my-project/workspace/foo",
        "Main": true,
        "Dir": "/path/to/project/workspace/foo"
    }
    """

    app_dir = RootedPath("/path/to/project")
    version_resolver.get_golang_version.return_value = "1.0.0"
    go = mock.Mock(spec=Go)
    go.return_value = go_list_m_json

    go_work = mock.Mock(spec=GoWork)

    # see examples at https://docs.python.org/3/library/unittest.mock.html#unittest.mock.PropertyMock
    type(go_work).path = mock.PropertyMock(return_value=(app_dir.path / "go.work"))
    type(go_work).workspace_paths = mock.PropertyMock(return_value=[app_dir.path / "workspace/foo"])

    main_module, workspace_modules = _parse_local_modules(
        go_work, go, {}, app_dir, version_resolver
    )

    assert main_module == ParsedModule(
        path="myorg.com/my-project",
        version="1.0.0",
        main=True,
    )

    assert workspace_modules[0] == ParsedModule(
        path="myorg.com/my-project/workspace/foo",
        replace=ParsedModule(path="./workspace/foo"),
    )


@pytest.mark.parametrize(
    "project_path, stream, expected_modules",
    (
        pytest.param(
            "/home/my-projects/simple-project",
            textwrap.dedent(
                """
                {
                    "Path": "github.com/my-org/simple-project",
                    "Main": true,
                    "Dir": "/home/my-projects/simple-project",
                    "GoMod": "/home/my-projects/simple-project/go.mod",
                    "GoVersion": "1.19"
                }
                """
            ),
            (
                {
                    "Path": "github.com/my-org/simple-project",
                    "Main": True,
                    "Dir": "/home/my-projects/simple-project",
                    "GoMod": "/home/my-projects/simple-project/go.mod",
                    "GoVersion": "1.19",
                },
                [],
            ),
            id="no_workspaces",
        ),
        pytest.param(
            "/home/my-projects/project-with-workspaces",
            textwrap.dedent(
                """
                {
                    "Path": "github.com/my-org/project-with-workspaces",
                    "Main": true,
                    "Dir": "/home/my-projects/project-with-workspaces",
                    "GoMod": "/home/my-projects/project-with-workspaces/go.mod",
                    "GoVersion": "1.19"
                }
                {
                    "Path": "github.com/my-org/work",
                    "Main": true,
                    "Dir": "/home/my-projects/project-with-workspaces/work",
                    "GoMod": "/home/my-projects/project-with-workspaces/work/go.mod"
                }
                {
                    "Path": "github.com/my-org/space",
                    "Main": true,
                    "Dir": "/home/my-projects/project-with-workspaces/space",
                    "GoMod": "/home/my-projects/project-with-workspaces/space/go.mod"
                }
                """
            ),
            (
                {
                    "Path": "github.com/my-org/project-with-workspaces",
                    "Main": True,
                    "Dir": "/home/my-projects/project-with-workspaces",
                    "GoMod": "/home/my-projects/project-with-workspaces/go.mod",
                    "GoVersion": "1.19",
                },
                [
                    {
                        "Path": "github.com/my-org/work",
                        "Main": True,
                        "Dir": "/home/my-projects/project-with-workspaces/work",
                        "GoMod": "/home/my-projects/project-with-workspaces/work/go.mod",
                    },
                    {
                        "Path": "github.com/my-org/space",
                        "Main": True,
                        "Dir": "/home/my-projects/project-with-workspaces/space",
                        "GoMod": "/home/my-projects/project-with-workspaces/space/go.mod",
                    },
                ],
            ),
            id="with_workspaces",
        ),
    ),
)
def test_process_modules_json_stream(
    project_path: str,
    stream: str,
    expected_modules: tuple[ModuleDict, list[ModuleDict]],
) -> None:
    app_dir = RootedPath(project_path)
    result = _process_modules_json_stream(app_dir, stream)

    assert result == expected_modules


@pytest.mark.parametrize(
    "relative_app_dir, module, expected_module",
    (
        # main module is also the workspace root:
        pytest.param(
            ".",
            {"Dir": "foo", "Path": "example.com/myproject/foo"},
            ParsedModule(
                path="example.com/myproject/foo",
                replace=ParsedModule(path="./foo"),
            ),
            id="app_root_is_workspace",
        ),
    ),
)
def test_parse_workspace_modules(
    relative_app_dir: str,
    module: dict[str, Any],
    expected_module: ParsedModule,
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / relative_app_dir
    go_work = mock.Mock(spec=GoWork)

    # see examples at https://docs.python.org/3/library/unittest.mock.html#unittest.mock.PropertyMock
    type(go_work).path = mock.PropertyMock(return_value=(app_dir / "go.work"))
    type(go_work).workspace_paths = mock.PropertyMock(return_value=[app_dir / "foo"])

    # makes Dir an absolute path based on tmp_path
    module["Dir"] = str(tmp_path / module["Dir"])

    parsed_workspace = _parse_workspace_module(go_work, module)
    assert parsed_workspace == expected_module


@pytest.mark.parametrize(
    "go_work_edit_json, relative_file_paths",
    [
        pytest.param(
            # main module is the same as the source dir, there's one nested workspace
            """
            {
                "Use": [
                    {"DiskPath": "."},
                    {"DiskPath": "./workspace"}
                ]
            }
            """,
            ["./go.sum", "./workspace/go.sum", "./go.work.sum"],
            id="main_module_is_repo_root",
        ),
        pytest.param(
            # go.work is in the source dir, main module and a workspace are nested
            """
            {
                "Use": [
                    {"DiskPath": "./app"},
                    {"DiskPath": "./workspace"}
                ]
            }
            """,
            ["./app/go.sum", "./workspace/go.sum", "./go.work.sum"],
            id="nested_main_module",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.gomod.GoWork._get_go_work")
def test_get_go_sum_files(
    mock_get_go_work: mock.Mock,
    rooted_tmp_path: RootedPath,
    go_work_edit_json: str,
    relative_file_paths: list[str],
) -> None:
    mock_go = mock.Mock(spec=Go)
    mock_get_go_work.return_value = go_work_edit_json
    go_work_path = rooted_tmp_path.join_within_root("go.work")
    files = _get_go_sum_files(GoWork.from_file(go_work_path, mock_go))

    expected_files = [rooted_tmp_path.join_within_root(p) for p in relative_file_paths]
    assert files == expected_files


@pytest.mark.parametrize("has_workspaces", (False, True))
@mock.patch("hermeto.core.package_managers.gomod.ModuleVersionResolver")
def test_create_modules_from_parsed_data(
    mock_version_resolver: mock.Mock,
    has_workspaces: bool,
    rooted_tmp_path: RootedPath,
) -> None:
    main_module_dir = rooted_tmp_path.join_within_root("target-module")
    mock_version_resolver.get_golang_version.return_value = "v1.5.0"

    go_work = None

    main_module = Module(
        name="github.com/my-org/my-repo/target-module",
        version="v1.5.0",
        original_name="github.com/my-org/my-repo/target-module",
        real_path="github.com/my-org/my-repo/target-module",
        main=True,
    )

    parsed_modules = [
        # simple module
        ParsedModule(
            path="golang.org/a/standard-module",
            version="v0.0.0-20190311183353-d8887717615a",
        ),
        # replaced module
        ParsedModule(
            path="github.com/a-neat-org/useful-module",
            version="v1.0.0",
            replace=ParsedModule(
                path="github.com/another-org/useful-module",
                version="v2.0.0",
            ),
        ),
        # locally replaced module, child folder
        ParsedModule(
            path="github.com/some-org/this-other-module",
            version="v0.0.1",
            replace=ParsedModule(
                path="./local-path",
            ),
        ),
        # locally replaced module, sibling folder
        ParsedModule(
            path="github.com/some-org/yet-another-module",
            version="v0.1.0",
            replace=ParsedModule(
                path="../sibling-path",
            ),
        ),
    ]

    modules_in_go_sum = frozenset(
        [
            ("golang.org/a/standard-module", "v0.0.0-20190311183353-d8887717615a"),
            # another-org/useful-module is missing
        ]
    )

    expect_modules = [
        Module(
            name="golang.org/a/standard-module",
            version="v0.0.0-20190311183353-d8887717615a",
            original_name="golang.org/a/standard-module",
            real_path="golang.org/a/standard-module",
        ),
        Module(
            name="github.com/another-org/useful-module",
            version="v2.0.0",
            original_name="github.com/a-neat-org/useful-module",
            real_path="github.com/another-org/useful-module",
            missing_hash_in_file=Path("target-module/go.sum"),
        ),
        Module(
            name="github.com/some-org/this-other-module",
            version="v1.5.0",
            original_name="github.com/some-org/this-other-module",
            real_path="github.com/my-org/my-repo/target-module/local-path",
        ),
        Module(
            name="github.com/some-org/yet-another-module",
            version="v1.5.0",
            original_name="github.com/some-org/yet-another-module",
            real_path="github.com/my-org/my-repo/sibling-path",
        ),
    ]

    if has_workspaces:
        go_work = mock.MagicMock(spec=GoWork)
        go_work.__bool__.return_value = True
        go_work_path = rooted_tmp_path.join_within_root("workspace_dir/go.work")
        type(go_work).rooted_path = mock.PropertyMock(return_value=go_work_path)
        expect_modules[1] = Module(
            name="github.com/another-org/useful-module",
            version="v2.0.0",
            original_name="github.com/a-neat-org/useful-module",
            real_path="github.com/another-org/useful-module",
            missing_hash_in_file=Path("workspace_dir/go.work.sum"),
        )

    modules = _create_modules_from_parsed_data(
        main_module,
        main_module_dir,
        parsed_modules,
        modules_in_go_sum,
        mock_version_resolver,
        go_work,
    )

    assert modules == expect_modules


@pytest.mark.parametrize(
    "input_json,expected",
    [
        pytest.param(
            """{"Use": [], "Replace": []}""",
            {"go": None, "toolchain": None, "use": []},
            id="empty",
        ),
        pytest.param(
            """
            {
                "Go": "1.999.999",
                "Use": [
                    {"DiskPath": "."},
                    {"DiskPath": "./foo/bar"},
                    {"DiskPath": "./bar/baz"}
                ]
            }""",
            {
                "go": "1.999.999",
                "toolchain": None,
                "use": [{"disk_path": "."}, {"disk_path": "./foo/bar"}, {"disk_path": "./bar/baz"}],
            },
            id="simple",
        ),
        pytest.param(
            """
            {
                "Go": "1.999.999",
                "Use": [
                    {"DiskPath": "."},
                    {"DiskPath": "./foo/bar"},
                    {"DiskPath": "./bar/baz"}
                ],
                "Replace": [
                    {
                        "Old": {
                                    "Path": "github.com/foo/bar"
                               },
                        "New": {
                                    "Path": "github.com/bar/baz",
                                    "Version": "v0.999.0"
                               }
                    }
                ]
            }""",
            {
                "go": "1.999.999",
                "toolchain": None,
                "use": [{"disk_path": "."}, {"disk_path": "./foo/bar"}, {"disk_path": "./bar/baz"}],
            },
            id="complex",
        ),
    ],
)
def test_go_work_model(input_json: str, expected: dict) -> None:
    assert ParsedGoWork.model_validate_json(input_json) == ParsedGoWork(**expected)


@pytest.mark.parametrize(
    "input_json",
    [
        pytest.param("", id="invalid_json"),
        pytest.param(
            """
            {
                "Go": "1.999.999",
                "Use": "invalid"
            }""",
            id="invalid_type",
        ),
        pytest.param(
            """
            {
                "Go": "1.999.999",
                "Use": [
                    {"Path": "./foo/bar"},
                ],
            }
            """,
            id="missing_mandatory_attribute",
        ),
    ],
)
def test_go_work_model_fail(input_json: str) -> None:
    with pytest.raises(ValueError):
        ParsedGoWork.model_validate_json(input_json)


def test_module_to_component() -> None:
    expected_component = Component(
        name="github.com/another-org/nice-repo",
        version="v0.0.1",
        purl="pkg:golang/github.com/another-org/nice-repo@v0.0.1?type=module",
    )

    component = Module(
        name="github.com/another-org/nice-repo",
        version="v0.0.1",
        original_name="github.com/my-org/nice-repo",
        real_path="github.com/another-org/nice-repo",
    ).to_component()

    assert component == expected_component


def test_create_packages_from_parsed_data() -> None:
    # modules as they'd be resolved from _create_modules_from_parsed_data
    modules = [
        Module(
            name="github.com/my-org/my-repo",
            version="v1.5.0",
            original_name="github.com/my-org/my-repo",
            real_path="github.com/my-org/my-repo",
            main=True,
        ),
        Module(
            name="github.com/my-org/my-repo/child-module",
            version="v1.0.1",
            original_name="github.com/my-org/my-repo/child-module",
            real_path="github.com/my-org/my-repo/child-module",
        ),
        Module(
            name="github.com/stretchr/testify",
            version="v1.7.1",
            original_name="github.com/stretchr/testify",
            real_path="github.com/stretchr/testify",
        ),
        Module(
            name="github.com/cachito-testing/retrodep/v2",
            version="v2.0.0",
            original_name="github.com/containerbuildsystem/retrodep/v2",
            real_path="github.com/cachito-testing/retrodep/v2",
        ),
    ]

    parsed_packages = [
        # std pkg
        ParsedPackage(
            import_path="internal/cpu",
            standard=True,
        ),
        # normal pkg
        ParsedPackage(
            import_path="github.com/stretchr/testify/assert",
            module=ParsedModule(path="github.com/stretchr/testify", version="v1.7.1"),
        ),
        # main module package
        ParsedPackage(
            import_path="github.com/my-org/my-repo",
            module=ParsedModule(path="github.com/my-org/my-repo", version="v1.5.0"),
        ),
        # package from a replaced module
        ParsedPackage(
            import_path="github.com/containerbuildsystem/retrodep/v2",
            module=ParsedModule(
                path="github.com/containerbuildsystem/retrodep/v2", version="v2.0.0"
            ),
        ),
        # package from a child module, with module reference missing
        ParsedPackage(
            import_path="github.com/my-org/my-repo/child-module/child-pkg",
        ),
    ]

    expect_packages = [
        StandardPackage(name="internal/cpu"),
        Package(
            relative_path="assert",
            module=Module(
                name="github.com/stretchr/testify",
                version="v1.7.1",
                original_name="github.com/stretchr/testify",
                real_path="github.com/stretchr/testify",
            ),
        ),
        Package(
            relative_path="",
            module=Module(
                name="github.com/my-org/my-repo",
                version="v1.5.0",
                original_name="github.com/my-org/my-repo",
                real_path="github.com/my-org/my-repo",
                main=True,
            ),
        ),
        Package(
            relative_path="",
            module=Module(
                name="github.com/cachito-testing/retrodep/v2",
                version="v2.0.0",
                original_name="github.com/containerbuildsystem/retrodep/v2",
                real_path="github.com/cachito-testing/retrodep/v2",
            ),
        ),
        Package(
            relative_path="child-pkg",
            module=Module(
                name="github.com/my-org/my-repo/child-module",
                version="v1.0.1",
                original_name="github.com/my-org/my-repo/child-module",
                real_path="github.com/my-org/my-repo/child-module",
            ),
        ),
    ]

    packages = _create_packages_from_parsed_data(modules, parsed_packages)

    assert packages == expect_packages


@pytest.mark.parametrize(
    "package, expected_component",
    (
        # package is also the main module
        (
            Package(
                relative_path="",
                module=Module(
                    name="github.com/my-org/some-repo",
                    version="v0.0.3",
                    original_name="github.com/my-org/some-repo",
                    real_path="github.com/my-org/some-repo",
                ),
            ),
            Component(
                name="github.com/my-org/some-repo",
                version="v0.0.3",
                purl="pkg:golang/github.com/my-org/some-repo@v0.0.3?type=package",
            ),
        ),
        # package is from a replaced module
        (
            Package(
                relative_path="this-pkg",
                module=Module(
                    name="github.com/another-org/nice-repo",
                    version="v0.0.1",
                    original_name="github.com/my-org/nice-repo",
                    real_path="github.com/another-org/nice-repo",
                ),
            ),
            Component(
                name="github.com/another-org/nice-repo/this-pkg",
                version="v0.0.1",
                purl="pkg:golang/github.com/another-org/nice-repo/this-pkg@v0.0.1?type=package",
            ),
        ),
        # main module is from a forked repo
        (
            Package(
                relative_path="this-pkg",
                module=Module(
                    name="github.com/my-org/nice-repo",
                    version="v0.0.2",
                    original_name="github.com/my-org/nice-repo",
                    real_path="github.com/another-org/forked-repo",
                ),
            ),
            Component(
                name="github.com/my-org/nice-repo/this-pkg",
                version="v0.0.2",
                purl="pkg:golang/github.com/another-org/forked-repo/this-pkg@v0.0.2?type=package",
            ),
        ),
    ),
)
def test_package_to_component(package: Package, expected_component: Component) -> None:
    assert package.to_component() == expected_component


@pytest.mark.parametrize("pattern", ["./...", "all"])
@mock.patch("hermeto.core.package_managers.gomod.run_cmd")
def test_go_list_deps(mock_run_cmd: mock.Mock, pattern: Literal["all", "./..."]) -> None:
    go_list_deps_json = """
        {
            "ImportPath": "time",
            "Standard": true,
            "Deps": [
                "errors",
                "internal/abi"
            ]
        }
        {
            "ImportPath": "github.com/foo",
            "Module": {
                "Path": "github.com/foo",
                "Main": true
            },
            "Deps": [
                "internal/bisect",
                "internal/bytealg"
            ]
        }
    """

    parsed_packages = {
        ParsedPackage(
            import_path="time",
            standard=True,
        ),
        ParsedPackage(
            import_path="github.com/foo",
            module=ParsedModule(
                path="github.com/foo",
                main=True,
            ),
        ),
    }

    mock_run_cmd.return_value = go_list_deps_json
    call_args = [
        GO_CMD_PATH,
        "list",
        "-e",
        "-deps",
        "-json=ImportPath,Module,Standard,Deps",
        pattern,
    ]
    assert set(_go_list_deps(Go(), pattern, {})) == parsed_packages
    mock_run_cmd.assert_called_once_with(call_args, {})


@mock.patch("hermeto.core.package_managers.gomod.run_cmd")
def test_go_list_deps_fail(
    mock_run_cmd: mock.Mock,
) -> None:
    mock_run_cmd.side_effect = subprocess.CalledProcessError(1, cmd="foo")
    expect_error = "Go execution failed: `go list -e -m -json` failed"

    with pytest.raises(PackageManagerError) as ex:
        _go_list_deps(Go(), "./...", {})
        assert expect_error in str(ex)


def test_deduplicate_resolved_modules() -> None:
    # as reported by "go list -deps all"
    package_modules = [
        # local replacement
        ParsedModule(
            path="github.com/my-org/local-replacement",
            version="v1.0.0",
            replace=ParsedModule(path="./local-folder"),
        ),
        # dependency replacement
        ParsedModule(
            path="github.com/my-org/my-dep",
            version="v2.0.0",
            replace=ParsedModule(path="github.com/another-org/another-dep", version="v2.0.1"),
        ),
        # common dependency
        ParsedModule(
            path="github.com/awesome-org/neat-dep",
            version="v2.0.1",
        ),
    ]

    # as reported by "go mod download -json"
    downloaded_modules = [
        # duplicate of dependency replacement
        ParsedModule(
            path="github.com/another-org/another-dep",
            version="v2.0.1",
        ),
        # duplicate of common dependency
        ParsedModule(
            path="github.com/awesome-org/neat-dep",
            version="v2.0.1",
        ),
    ]

    dedup_modules = _deduplicate_resolved_modules(package_modules, downloaded_modules)

    expect_dedup_modules = {
        ParsedModule(
            path="github.com/my-org/local-replacement",
            version="v1.0.0",
            replace=ParsedModule(path="./local-folder"),
        ),
        ParsedModule(
            path="github.com/my-org/my-dep",
            version="v2.0.0",
            replace=ParsedModule(path="github.com/another-org/another-dep", version="v2.0.1"),
        ),
        ParsedModule(
            path="github.com/awesome-org/neat-dep",
            version="v2.0.1",
        ),
    }

    assert set(dedup_modules) == expect_dedup_modules


@pytest.mark.parametrize(
    "module_suffix, ref, expected, subpath",
    (
        # First commit with no tag
        (
            "",
            "78510c591e2be635b010a52a7048b562bad855a3",
            "v0.0.0-20191107200220-78510c591e2b",
            None,
        ),
        # No prior tag at all
        (
            "",
            "5a6e50a1f0e3ce42959d98b3c3a2619cb2516531",
            "v0.0.0-20191107202433-5a6e50a1f0e3",
            None,
        ),
        # Only a non-semver tag (v1)
        (
            "",
            "7911d393ab186f8464884870fcd0213c36ecccaf",
            "v0.0.0-20191107202444-7911d393ab18",
            None,
        ),
        # Directly maps to a semver tag (v1.0.0)
        ("", "d1b74311a7bf590843f3b58bf59ab047a6f771ae", "v1.0.0", None),
        # One commit after a semver tag (v1.0.0)
        (
            "",
            "e92462c73bbaa21540f7385e90cb08749091b66f",
            "v1.0.1-0.20191107202936-e92462c73bba",
            None,
        ),
        # A semver tag (v2.0.0) without the corresponding go.mod bump, which happens after a v1.0.0
        # semver tag
        (
            "",
            "61fe6324077c795fc81b602ee27decdf4a4cf908",
            "v1.0.1-0.20191107202953-61fe6324077c",
            None,
        ),
        # A semver tag (v2.1.0) after the go.mod file was bumped
        ("/v2", "39006a0b5b0654a299cc43f71e0dc1aa50c2bc72", "v2.1.0", None),
        # A pre-release semver tag (v2.2.0-alpha)
        ("/v2", "0b3468852566617379215319c0f4dfe7f5948a8f", "v2.2.0-alpha", None),
        # Two commits after a pre-release semver tag (v2.2.0-alpha)
        (
            "/v2",
            "863073fae6efd5e04bb972a05db0b0706ec8276e",
            "v2.2.0-alpha.0.20191107204050-863073fae6ef",
            None,
        ),
        # Directly maps to a semver non-annotated tag (v2.2.0)
        ("/v2", "709b220511038f443fe1b26ac09c3e6c06c9f7c7", "v2.2.0", None),
        # A non-semver tag (random-tag)
        (
            "/v2",
            "37cea8ddd9e6b6b81c7cfbc3223ce243c078388a",
            "v2.2.1-0.20191107204245-37cea8ddd9e6",
            None,
        ),
        # The go.mod file is bumped but there is no versioned commit
        (
            "/v2",
            "6c7249e8c989852f2a0ee0900378d55d8e1d7fe0",
            "v2.0.0-20191108212303-6c7249e8c989",
            None,
        ),
        # Three semver annotated tags on the same commit
        ("/v2", "a77e08ced4d6ae7d9255a1a2e85bd3a388e61181", "v2.2.5", None),
        # A non-annotated semver tag and an annotated semver tag
        ("/v2", "bf2707576336626c8bbe4955dadf1916225a6a60", "v2.3.3", None),
        # Two non-annotated semver tags
        ("/v2", "729d0e6d60317bae10a71fcfc81af69a0f6c07be", "v2.4.1", None),
        # Two semver tags, with one having the wrong major version and the other with the correct
        # major version
        ("/v2", "3decd63971ed53a5b7ff7b2ca1e75f3915e99cf2", "v2.5.0", None),
        # A semver tag that is incorrectly lower then the preceding semver tag
        ("/v2", "0dd249ad59176fee9b5451c2f91cc859e5ddbf45", "v2.0.1", None),
        # A commit after the incorrect lower semver tag
        (
            "/v2",
            "2883f3ddbbc811b112ff1fe51ba2ee7596ddbf24",
            "v2.5.1-0.20191118190931-2883f3ddbbc8",
            None,
        ),
        # Newest semver tag is applied to a submodule, but the root module is being processed
        (
            "/v2",
            "f3ee3a4a394fb44b055ed5710b8145e6e98c0d55",
            "v2.5.1-0.20211209210936-f3ee3a4a394f",
            None,
        ),
        # Submodule has a semver tag applied to it
        ("/v2", "f3ee3a4a394fb44b055ed5710b8145e6e98c0d55", "v2.5.1", "submodule"),
        # A commit after a submodule tag
        (
            "/v2",
            "cc6c9f554c0982786ff9e077c2b37c178e46828c",
            "v2.5.2-0.20211223131312-cc6c9f554c09",
            "submodule",
        ),
        # A commit with multiple tags in different submodules
        ("/v2", "5401bdd8a8ebfcccd2eea9451d407a5fdae6fc76", "v2.5.3", "submodule"),
        # Malformed semver tag, root module being processed
        ("/v2", "4a481f0bae82adef3ea6eae3d167af6e74499cb2", "v2.6.0", None),
        # Malformed semver tag, submodule being processed
        ("/v2", "4a481f0bae82adef3ea6eae3d167af6e74499cb2", "v2.6.0", "submodule"),
    ),
)
def test_get_golang_version(
    golang_repo_path: Path,
    module_suffix: str,
    ref: str,
    expected: str,
    subpath: str | None,
) -> None:
    module_name = f"github.com/mprahl/test-golang-pseudo-versions{module_suffix}"

    module_dir = RootedPath(golang_repo_path)
    repo = GitRepo(golang_repo_path)
    repo.git.checkout(ref)
    version_resolver = ModuleVersionResolver(repo, repo.commit(ref))

    if subpath:
        module_dir = module_dir.join_within_root(subpath)

    version = version_resolver.get_golang_version(module_name, module_dir)
    assert version == expected


def test_validate_local_replacements(tmpdir: Path) -> None:
    app_path = RootedPath(tmpdir).join_within_root("subpath")

    modules = [
        ParsedModule(
            path="example.org/foo", version="v1.0.0", replace=ParsedModule(path="./another-foo")
        ),
        ParsedModule(
            path="example.org/foo", version="v1.0.0", replace=ParsedModule(path="../sibling-foo")
        ),
    ]

    _validate_local_replacements(modules, app_path)


def test_invalid_local_replacements(tmpdir: Path) -> None:
    app_path = RootedPath(tmpdir)

    modules = [
        ParsedModule(
            path="example.org/foo", version="v1.0.0", replace=ParsedModule(path="../outside-repo")
        ),
    ]

    with pytest.raises(PathOutsideRoot):
        _validate_local_replacements(modules, app_path)


@pytest.mark.parametrize("go_vendor_cmd", ["mod", "work"])
@mock.patch("hermeto.core.package_managers.gomod.Go._run")
def test_vendor_deps(
    mock_run_cmd: mock.Mock,
    go_vendor_cmd: str,
    rooted_tmp_path: RootedPath,
) -> None:
    app_dir = rooted_tmp_path.join_within_root("some/module")
    run_params = {"cwd": app_dir}
    _vendor_deps(Go(), app_dir, go_vendor_cmd == "work", run_params)

    mock_run_cmd.assert_called_once_with([GO_CMD_PATH, go_vendor_cmd, "vendor"], **run_params)


def test_parse_vendor(rooted_tmp_path: RootedPath, data_dir: Path) -> None:
    modules_txt = rooted_tmp_path.join_within_root("vendor/modules.txt")
    modules_txt.path.parent.mkdir(parents=True)
    modules_txt.path.write_text(get_mocked_data(data_dir, "vendored/modules.txt"))
    expect_modules = {
        ParsedModule(
            path="github.com/Azure/go-ansiterm", version="v0.0.0-20210617225240-d185dfc1b5a1"
        ),
        ParsedModule(path="github.com/Masterminds/semver", version="v1.4.2"),
        ParsedModule(path="github.com/Microsoft/go-winio", version="v0.6.0"),
        ParsedModule(
            path="github.com/cachito-testing/gomod-pandemonium/terminaltor",
            version="v0.0.0",
            replace=ParsedModule(path="./terminaltor"),
        ),
        ParsedModule(
            path="github.com/cachito-testing/gomod-pandemonium/weird",
            version="v0.0.0",
            replace=ParsedModule(path="./weird"),
        ),
        ParsedModule(path="github.com/go-logr/logr", version="v1.2.3"),
        ParsedModule(
            path="github.com/go-task/slim-sprig", version="v0.0.0-20230315185526-52ccab3ef572"
        ),
        ParsedModule(path="github.com/google/go-cmp", version="v0.5.9"),
        ParsedModule(path="github.com/google/pprof", version="v0.0.0-20210407192527-94a9f03dee38"),
        ParsedModule(path="github.com/moby/term", version="v0.0.0-20221205130635-1aeaba878587"),
        ParsedModule(path="github.com/onsi/ginkgo/v2", version="v2.9.2"),
        ParsedModule(path="github.com/onsi/gomega", version="v1.27.4"),
        ParsedModule(path="github.com/op/go-logging", version="v0.0.0-20160315200505-970db520ece7"),
        ParsedModule(path="github.com/pkg/errors", version="v0.8.1"),
        ParsedModule(
            path="github.com/release-engineering/retrodep/v2",
            version="v2.1.0",
            replace=ParsedModule(path="github.com/cachito-testing/retrodep/v2", version="v2.1.1"),
        ),
        ParsedModule(path="golang.org/x/mod", version="v0.9.0"),
        ParsedModule(path="golang.org/x/net", version="v0.8.0"),
        ParsedModule(path="golang.org/x/sys", version="v0.6.0"),
        ParsedModule(path="golang.org/x/text", version="v0.8.0"),
        ParsedModule(path="golang.org/x/tools", version="v0.7.0"),
        ParsedModule(path="gopkg.in/yaml.v2", version="v2.2.2"),
        ParsedModule(path="gopkg.in/yaml.v3", version="v3.0.1"),
    }
    assert set(_parse_vendor(rooted_tmp_path)) == expect_modules


@pytest.mark.parametrize(
    "file_content, expect_error_msg",
    [
        ("#invalid-line", "vendor/modules.txt: unexpected format: '#invalid-line'"),
        ("# main-module", "vendor/modules.txt: unexpected module line format: '# main-module'"),
        (
            "github.com/x/package",
            "vendor/modules.txt: package has no parent module: github.com/x/package",
        ),
    ],
)
def test_parse_vendor_unexpected_format(
    file_content: str, expect_error_msg: str, rooted_tmp_path: RootedPath
) -> None:
    vendor = rooted_tmp_path.join_within_root("vendor")
    vendor.path.mkdir()
    vendor.join_within_root("modules.txt").path.write_text(file_content)

    with pytest.raises(UnexpectedFormat, match=expect_error_msg):
        _parse_vendor(rooted_tmp_path)


@pytest.mark.parametrize("subpath", ["", "some/app/"])
@pytest.mark.parametrize(
    "vendor_before, vendor_changes, expected_change",
    [
        pytest.param({}, {}, None, id="no_vendoring"),
        pytest.param({"vendor": {"modules.txt": "foo v1.0.0\n"}}, {}, None, id="no_changes"),
        pytest.param(
            {},
            {"vendor": {"modules.txt": "foo v1.0.0\n"}},
            textwrap.dedent(
                """
                --- /dev/null
                +++ b/{subpath}vendor/modules.txt
                @@ -0,0 +1 @@
                +foo v1.0.0
                """
            ),
            id="modules_txt_added",
        ),
        pytest.param(
            {"vendor": {"modules.txt": "foo v1.0.0\n"}},
            {"vendor": {"modules.txt": "foo v2.0.0\n"}},
            textwrap.dedent(
                """
                --- a/{subpath}vendor/modules.txt
                +++ b/{subpath}vendor/modules.txt
                @@ -1 +1 @@
                -foo v1.0.0
                +foo v2.0.0
                """
            ),
            id="modules_txt_changes",
        ),
        pytest.param(
            {},
            {"vendor": {"some_file": "foo"}},
            textwrap.dedent(
                """
                A\t{subpath}vendor/some_file
                """
            ),
            id="a_file_was_added",
        ),
        pytest.param(
            {"vendor": {"some_file": "foo"}},
            {"vendor": {"some_file": "bar", "other_file": "baz"}},
            textwrap.dedent(
                """
                A\t{subpath}vendor/other_file
                M\t{subpath}vendor/some_file
                """
            ),
            id="multiple_changes",
        ),
        # vendor/ was added but only contains empty dirs => will be ignored
        pytest.param({}, {"vendor": {"empty_dir": {}}}, None, id="vendor_empty_dirs"),
        # change will be tracked even if vendor/ is .gitignore'd
        pytest.param(
            {".gitignore": "vendor/"},
            {"vendor": {"some_file": "foo"}},
            textwrap.dedent(
                """
                A\t{subpath}vendor/some_file
                """
            ),
            id="file_added_in_gitignored_vendor_dir",
        ),
    ],
)
def test_vendor_changed(
    subpath: str,
    vendor_before: dict[str, Any],
    vendor_changes: dict[str, Any],
    expected_change: str | None,
    rooted_tmp_path_repo: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = git.Repo(rooted_tmp_path_repo)

    app_dir = rooted_tmp_path_repo.join_within_root(subpath)
    os.makedirs(app_dir, exist_ok=True)

    write_file_tree(vendor_before, app_dir)
    repo.index.add([app_dir.join_within_root(path) for path in vendor_before])
    repo.index.commit("before vendoring", skip_hooks=True)

    write_file_tree(vendor_changes, app_dir, exist_ok=True)

    assert _vendor_changed(app_dir) == bool(expected_change)
    if expected_change:
        assert expected_change.format(subpath=subpath) in caplog.text

    # The _vendor_changed function should reset the `git add` => added files should not be tracked
    assert not repo.git.diff("--diff-filter", "A")


@pytest.mark.parametrize(
    "file_tree",
    (
        {".": {}},
        {"foo": {}, "bar": {}},
        {"foo": {}, "bar": {"go.mod": ""}},
    ),
)
@mock.patch("hermeto.core.package_managers.gomod._list_installed_toolchains")
@mock.patch("hermeto.core.package_managers.gomod.run_cmd")
def test_missing_gomod_file(
    mock_run_cmd: mock.Mock,
    mock_list_installed_toolchains: mock.Mock,
    file_tree: dict[str, Any],
    tmp_path: Path,
) -> None:
    mock_run_cmd.return_value = "go version go0.0.1"
    mock_list_installed_toolchains.return_value = [mock.Mock(spec=Go)]
    write_file_tree(file_tree, tmp_path, exist_ok=True)

    packages = [{"path": path, "type": "gomod"} for path, _ in file_tree.items()]
    request = Request(source_dir=tmp_path, output_dir=tmp_path, packages=packages)

    with pytest.raises(LockfileNotFound):
        fetch_gomod_source(request)


@pytest.mark.parametrize(
    "gomod_input_packages, packages_output_by_path, expect_components",
    (
        (
            [{"type": "gomod", "path": "."}],
            {
                ".": ResolvedGoModule(
                    ParsedModule(
                        path="github.com/my-org/my-repo",
                        version="v1.0.0",
                    ),
                    [
                        ParsedModule(
                            path="golang.org/x/net",
                            version="v0.0.0-20190311183353-d8887717615a",
                        ),
                        ParsedModule(
                            path="golang.org/x/tools",
                            version="v0.7.0",
                        ),
                    ],
                    [
                        ParsedPackage(
                            import_path="github.com/my-org/my-repo",
                            module=ParsedModule(
                                path="github.com/my-org/my-repo",
                                version="v1.0.0",
                            ),
                        ),
                        ParsedPackage(
                            import_path="golang.org/x/net/http",
                            module=ParsedModule(
                                path="golang.org/x/net",
                                version="v0.0.0-20190311183353-d8887717615a",
                            ),
                        ),
                    ],
                    frozenset([("golang.org/x/tools", "v0.7.0")]),
                ),
            },
            [
                Component(
                    name="github.com/my-org/my-repo",
                    purl="pkg:golang/github.com/my-org/my-repo@v1.0.0?type=module",
                    version="v1.0.0",
                ),
                Component(
                    name="golang.org/x/net",
                    purl="pkg:golang/golang.org/x/net@v0.0.0-20190311183353-d8887717615a?type=module",
                    version="v0.0.0-20190311183353-d8887717615a",
                    properties=[
                        Property(name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="go.sum")
                    ],
                ),
                Component(
                    name="golang.org/x/tools",
                    purl="pkg:golang/golang.org/x/tools@v0.7.0?type=module",
                    version="v0.7.0",
                ),
                Component(
                    name="github.com/my-org/my-repo",
                    purl="pkg:golang/github.com/my-org/my-repo@v1.0.0?type=package",
                    version="v1.0.0",
                ),
                Component(
                    name="golang.org/x/net/http",
                    purl="pkg:golang/golang.org/x/net/http@v0.0.0-20190311183353-d8887717615a?type=package",
                    version="v0.0.0-20190311183353-d8887717615a",
                ),
            ],
        ),
        (
            [{"type": "gomod", "path": "."}, {"type": "gomod", "path": "./path"}],
            {
                ".": ResolvedGoModule(
                    ParsedModule(
                        path="github.com/my-org/my-repo",
                        version="v1.0.0",
                    ),
                    [],
                    [],
                    frozenset(),
                ),
                "path": ResolvedGoModule(
                    ParsedModule(
                        path="github.com/my-org/my-repo/path",
                        version="v1.0.0",
                    ),
                    [
                        ParsedModule(
                            path="golang.org/x/net",
                            version="v0.0.0-20190311183353-d8887717615a",
                        ),
                        ParsedModule(
                            path="golang.org/x/tools",
                            version="v0.7.0",
                        ),
                    ],
                    [],
                    frozenset([("golang.org/x/tools", "v0.7.0")]),
                ),
            },
            [
                Component(
                    name="github.com/my-org/my-repo",
                    purl="pkg:golang/github.com/my-org/my-repo@v1.0.0?type=module",
                    version="v1.0.0",
                ),
                Component(
                    name="github.com/my-org/my-repo/path",
                    purl="pkg:golang/github.com/my-org/my-repo/path@v1.0.0?type=module",
                    version="v1.0.0",
                ),
                Component(
                    name="golang.org/x/net",
                    purl="pkg:golang/golang.org/x/net@v0.0.0-20190311183353-d8887717615a?type=module",
                    version="v0.0.0-20190311183353-d8887717615a",
                    properties=[
                        Property(name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="path/go.sum")
                    ],
                ),
                Component(
                    name="golang.org/x/tools",
                    purl="pkg:golang/golang.org/x/tools@v0.7.0?type=module",
                    version="v0.7.0",
                ),
            ],
        ),
    ),
)
@mock.patch("hermeto.core.package_managers.gomod._list_installed_toolchains")
@mock.patch("hermeto.core.package_managers.gomod._get_repository_name")
@mock.patch("hermeto.core.package_managers.gomod._find_missing_gomod_files")
@mock.patch("hermeto.core.package_managers.gomod._resolve_gomod")
@mock.patch("hermeto.core.package_managers.gomod.GoCacheTemporaryDirectory")
@mock.patch("hermeto.core.package_managers.gomod.ModuleVersionResolver.from_repo_path")
@mock.patch("hermeto.core.package_managers.gomod._select_toolchain")
@mock.patch("hermeto.core.package_managers.gomod._get_go_work_path")
@mock.patch("hermeto.core.package_managers.gomod._vendor_changed")
@mock.patch("hermeto.core.package_managers.gomod.create_backend_annotation")
def test_fetch_gomod_source(
    mock_create_annotation: mock.Mock,
    mock_vendor_changed: mock.Mock,
    mock_get_go_work_path: mock.Mock,
    mock_select_toolchain: mock.Mock,
    mock_version_resolver: mock.Mock,
    mock_tmp_dir: mock.Mock,
    mock_resolve_gomod: mock.Mock,
    mock_find_missing_gomod_files: mock.Mock,
    mock_get_repository_name: mock.Mock,
    mock_list_installed_toolchains: mock.Mock,
    gomod_request: Request,
    packages_output_by_path: dict[str, ResolvedGoModule],
    expect_components: list[Component],
    env_variables: list[EnvironmentVariable],
) -> None:
    def resolve_gomod_mocked(
        app_dir: RootedPath,
        request: Request,
        tmp_dir: Path,
        version_resolver: ModuleVersionResolver,
        go: Go,
        go_work: GoWork,
    ) -> ResolvedGoModule:
        # Find package output based on the path being processed
        return packages_output_by_path[
            app_dir.path.relative_to(gomod_request.source_dir).as_posix()
        ]

    mock_gomod_annotation = Annotation(
        subjects=set(),
        annotator={"organization": {"name": "red hat"}},
        timestamp="2026-01-01T00:00:00Z",
        text="hermeto:backend:gomod",
    )
    mock_create_annotation.return_value = mock_gomod_annotation

    mock_resolve_gomod.side_effect = resolve_gomod_mocked
    mock_find_missing_gomod_files.return_value = []
    mock_get_repository_name.return_value = "github.com/my-org/my-repo"
    mock_vendor_changed.return_value = False

    mock_tmp_dir.name = "tmpdir"
    mock_tmp_dir.return_value.__enter__.return_value = mock_tmp_dir
    mock_tmp_dir.return_value.__exit__.return_value = None
    mock_tmp_dir_path = Path(mock_tmp_dir.name)

    # workspaces are tested in test_resolve_gomod, skip them here
    mock_get_go_work_path.return_value = None
    mock_go = mock.MagicMock(spec=Go)
    mock_select_toolchain.return_value = mock_go
    mock_list_installed_toolchains.return_value = [mock_go]

    output = fetch_gomod_source(gomod_request)
    calls = [
        mock.call(
            gomod_request.source_dir.join_within_root(package.path),
            gomod_request,
            mock_tmp_dir_path,
            mock_version_resolver.return_value,
            mock_go,
            None,
        )
        for package in gomod_request.packages
    ]
    mock_resolve_gomod.assert_has_calls(calls)

    if len(gomod_request.packages) == 0:
        expected_output = RequestOutput.empty()
    else:
        expected_output = RequestOutput(
            annotations=[mock_gomod_annotation],
            components=expect_components,
            build_config=BuildConfig(environment_variables=env_variables),
        )

    assert output == expected_output


@pytest.mark.parametrize(
    "input_url",
    (
        "ssh://github.com/cachito-testing/gomod-pandemonium",
        "ssh://username@github.com/cachito-testing/gomod-pandemonium",
        "github.com:cachito-testing/gomod-pandemonium.git",
        "username@github.com:cachito-testing/gomod-pandemonium.git/",
        "https://github.com/cachito-testing/gomod-pandemonium",
        "https://github.com/cachito-testing/gomod-pandemonium.git",
        "https://github.com/cachito-testing/gomod-pandemonium.git/",
    ),
)
@mock.patch("hermeto.core.scm.GitRepo")
def test_get_repository_name(mock_git_repo: Any, input_url: str) -> None:
    expected_url = "github.com/cachito-testing/gomod-pandemonium"

    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = input_url
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    resolved_url = _get_repository_name(RootedPath("/my-folder/cloned-repo"))

    assert resolved_url == expected_url


@mock.patch("hermeto.core.package_managers.gomod.get_repo_id")
@mock.patch("hermeto.core.package_managers.gomod.get_config")
def test_get_repository_name_permissive_mode(
    mock_get_config: mock.Mock,
    mock_get_repo_id: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """Test that _get_repository_name returns None in PERMISSIVE mode when not a git repo."""
    mock_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.PERMISSIVE

    result = _get_repository_name(rooted_tmp_path)

    assert result is None


@mock.patch("hermeto.core.package_managers.gomod.get_repo_id")
@mock.patch("hermeto.core.package_managers.gomod.get_config")
def test_get_repository_name_strict_mode_raises_without_git_repo(
    mock_get_config: mock.Mock,
    mock_get_repo_id: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """Test that _get_repository_name re-raises NotAGitRepo in STRICT mode."""
    mock_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.STRICT

    with pytest.raises(NotAGitRepo):
        _get_repository_name(rooted_tmp_path)


@mock.patch("hermeto.core.package_managers.gomod.get_repo_id")
@mock.patch("hermeto.core.package_managers.gomod.get_config")
def test_get_repository_name_permissive_mode_with_git_repo(
    mock_get_config: mock.Mock,
    mock_get_repo_id: mock.Mock,
    rooted_tmp_path_repo: RootedPath,
) -> None:
    """Test that _get_repository_name returns repo name in PERMISSIVE mode when git repo is available."""
    repo = git.Repo(rooted_tmp_path_repo)
    repo.create_remote("origin", "https://github.com/org/repo.git")

    repo_id = RepoID("https://github.com/org/repo.git", repo.head.commit.hexsha)
    mock_get_repo_id.return_value = repo_id
    mock_get_config.return_value.mode = Mode.PERMISSIVE

    result = _get_repository_name(rooted_tmp_path_repo)

    assert result == "github.com/org/repo"


def test_create_main_module_from_parsed_data_repo_name_none(
    rooted_tmp_path: RootedPath,
) -> None:
    """PERMISSIVE mode without a git repo: resolved_path falls back to the module path."""
    parsed_main_module = ParsedModule(path="example.com/org/myapp", version="v1.2.3")
    main_module_dir = rooted_tmp_path  # subpath_from_root == "."

    module = _create_main_module_from_parsed_data(
        main_module_dir=main_module_dir,
        repo_name=None,
        parsed_main_module=parsed_main_module,
    )

    assert module.real_path == "example.com/org/myapp"
    assert module.name == "example.com/org/myapp"
    assert module.version == "v1.2.3"


@pytest.fixture
def repo_remote_with_tag(rooted_tmp_path: RootedPath) -> tuple[RootedPath, RootedPath]:
    """
    Return the Paths to two Repos, with the first configured as the remote of the second.

    There are different git tags applied to the first and second commits of the README file
    """
    local_repo_path = rooted_tmp_path.join_within_root("local")
    remote_repo_path = rooted_tmp_path.join_within_root("remote")
    readme_file_path = remote_repo_path.join_within_root("README.md")

    local_repo_path.path.mkdir()
    remote_repo_path.path.mkdir()
    remote_repo = git.Repo.init(remote_repo_path)

    with open(readme_file_path, "wb"):
        pass
    remote_repo.index.add([readme_file_path])
    initial_commit = remote_repo.index.commit("Add README")

    with open(readme_file_path, "w") as f:
        f.write("This is a README")
    remote_repo.index.add([readme_file_path])
    remote_repo.index.commit("Update README")

    git.Repo.clone_from(remote_repo_path, local_repo_path)

    remote_repo.create_tag("v1.0.0", ref=initial_commit, env=GIT_PRISTINE_ENV)
    remote_repo.create_tag("v2.0.0", env=GIT_PRISTINE_ENV)

    return remote_repo_path, local_repo_path


def test_fetch_tags(repo_remote_with_tag: tuple[RootedPath, RootedPath]) -> None:
    _, local_repo_path = repo_remote_with_tag
    assert git.Repo(local_repo_path).tags == []
    version_resolver = ModuleVersionResolver.from_repo_path(local_repo_path)
    assert version_resolver._commit_tags == ["v2.0.0"]
    assert version_resolver._all_tags == ["v1.0.0", "v2.0.0"]


def test_fetch_tags_fail(repo_remote_with_tag: tuple[RootedPath, RootedPath]) -> None:
    # The remote_repo itself has no remote configured, so will fail when fetching tags
    remote_repo_path, _ = repo_remote_with_tag
    error_msg = re.escape(
        f"Failed to fetch the tags on the Git repository (GitRemoteNotFoundError) for {remote_repo_path}"
    )
    with pytest.raises(FetchError, match=error_msg):
        ModuleVersionResolver.from_repo_path(remote_repo_path)


@pytest.mark.parametrize(
    "go_mod_file, go_mod_version, go_toolchain_version",
    [
        pytest.param("go 1.21", "1.21", None, id="go_minor"),
        pytest.param("go 1.21.0", "1.21.0", None, id="go_micro"),
        pytest.param("    go    1.21.4    ", "1.21.4", None, id="go_spaces"),
        pytest.param("go 1.21rc4", "1.21rc4", None, id="go_minor_rc"),
        pytest.param("go 1.21.0rc4", "1.21.0rc4", None, id="go_micro_rc"),
        pytest.param("go 1.21.0  // comment", "1.21.0", None, id="go_commentary"),
        pytest.param("go 1.21.0//commentary", "1.21.0", None, id="go_commentary_no_spaces"),
        pytest.param("go 1.21.0beta2//comment", "1.21.0beta2", None, id="go_rc_commentary"),
        pytest.param("   toolchain   go1.21.4  ", None, "1.21.4", id="toolchain_spaces"),
        pytest.param("go 1.21\ntoolchain go1.21.6", "1.21", "1.21.6", id="go_and_toolchain"),
    ],
    indirect=["go_mod_file"],
)
def test_get_gomod_version(
    rooted_tmp_path: RootedPath, go_mod_file: Path, go_mod_version: str, go_toolchain_version: str
) -> None:
    assert _get_gomod_version(rooted_tmp_path.join_within_root("go.mod")) == (
        go_mod_version,
        go_toolchain_version,
    )


INVALID_VERSION_STRINGS = [
    "go1.21",  # missing space between go and version number
    "go 1.21.0.100",  # non-conforming to the X.Y(.Z)? versioning template
    "1.21",  # missing 'go' at the beginning
    "go 1.21 foo",  # extra characters after version string
    "go 1.21prerelease",  # pre-release with no number
    "go 1.21prerelease_4",  # pre-release with non-alphanum character
    "toolchain 1.21",  # missing 'go' prefix for the toolchain spec
]


@pytest.mark.parametrize(
    "go_mod_file",
    [pytest.param(_, id=_) for _ in INVALID_VERSION_STRINGS],
    indirect=True,
)
def test_get_gomod_version_fail(rooted_tmp_path: RootedPath, go_mod_file: Path) -> None:
    assert _get_gomod_version(rooted_tmp_path.join_within_root("go.mod")) == (None, None)


@pytest.mark.parametrize(
    "go_version,toolchain_version,installed_versions,expected_result",
    [
        pytest.param(None, None, ["1.20.0", "1.21.0"], "1.20.0", id="missing_go_version"),
        pytest.param("1.21.5", None, ["1.21.5", "1.22.0"], "1.21.5", id="go_version_only"),
        pytest.param("1.21", "1.21.4", ["1.21.6", "1.21.4"], "1.21.4", id="exact_match"),
        pytest.param(
            "1.21", "1.22.1", ["1.22.4", "1.22.6", "1.21.2"], "1.22.4", id="closest_match"
        ),
        pytest.param("1.21", "1.21.4", ["1.22.1"], "1.22.1", id="newer_minor"),
        pytest.param("1.22", "1.22.1", ["1.21.0", "1.20"], "1.21.0", id="fallback_to_1_21"),
        pytest.param("1.22", "1.22.1", ["1.20", "1.19.2"], "1.22.1", id="install_missing"),
    ],
)
@mock.patch("hermeto.core.package_managers.gomod.Go.from_missing_toolchain")
@mock.patch("hermeto.core.package_managers.gomod.Go._get_release")
@mock.patch("hermeto.core.package_managers.gomod._get_gomod_version")
def test_select_toolchain(
    mock_get_gomod_version: mock.Mock,
    mock_go_get_release: mock.Mock,
    mock_from_missing_toolchain: mock.Mock,
    go_version: str | None,
    toolchain_version: str | None,
    installed_versions: list[str],
    expected_result: str | None,
    rooted_tmp_path: RootedPath,
) -> None:
    toolchain_release_str = "go" if toolchain_version is None else f"go{toolchain_version}"

    mock_get_gomod_version.return_value = (go_version, toolchain_version)
    mock_from_missing_toolchain.return_value = Go(f"/usr/bin/{toolchain_release_str}")

    effects = [f"go{version_str}" for version_str in installed_versions] + [toolchain_release_str]
    mock_go_get_release.side_effect = effects

    go_mod_file = rooted_tmp_path.join_within_root("go.mod")
    go_mod_file.path.touch()

    # Create mock Go instances with static versions (no subprocess calls)
    installed_toolchains = []
    for version_str in installed_versions:
        go = Go(f"/usr/bin/go{version_str}")
        installed_toolchains.append(go)

    result = _select_toolchain(go_mod_file, installed_toolchains)

    mock_get_gomod_version.assert_called_once_with(go_mod_file)
    if expected_result is None:
        assert result is None
    else:
        assert result is not None
        assert str(result.version) == expected_result


@mock.patch("hermeto.core.package_managers.gomod._get_gomod_version")
def test_select_toolchain_fail(
    mock_get_gomod_version: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_get_gomod_version.return_value = ("9999.999", None)
    mock_go = mock_go_class("/usr/bin/go")
    mock_go.version = GoVersion("1.21.0")

    go_mod_file = rooted_tmp_path.join_within_root("go.mod")
    with pytest.raises(PackageManagerError, match="is not supported yet"):
        _select_toolchain(go_mod_file, {mock_go})


@pytest.mark.parametrize(
    "GOTELEMETRY, telemetry_disable",
    [
        pytest.param("", False, id="telemetry_not_set"),
        pytest.param("off", False, id="telemetry_disabled"),
        pytest.param("local", True, id="telemetry_enabled"),
    ],
)
@mock.patch("hermeto.core.package_managers.gomod.run_cmd")
def test_disable_telemetry(
    mock_run_cmd: mock.Mock,
    GOTELEMETRY: str,
    telemetry_disable: bool,
) -> None:
    mock_run_cmd.side_effect = [GOTELEMETRY, None]

    go = Go()
    cmd = [go.binary, "telemetry", "off"]
    params = {"env": {"GOTOOLCHAIN": "auto"}}
    _disable_telemetry(go, params)

    if not telemetry_disable:
        assert mock_run_cmd.call_count == 1
    else:
        assert mock_run_cmd.call_count == 2
        mock_run_cmd.assert_called_with(cmd, params)


@pytest.mark.parametrize(
    "input_subdir, expected_outfile",
    [
        pytest.param("non-vendored", "resolve_gomod.json", id="without_workspaces"),
        pytest.param("workspaces", "resolve_gomod_workspaces.json", id="with_workspaces"),
    ],
)
@mock.patch("hermeto.core.package_managers.gomod.GoWork._get_go_work")
def test_parse_packages(
    mock_get_go_work: mock.Mock,
    rooted_tmp_path: RootedPath,
    data_dir: Path,
    input_subdir: str,
    expected_outfile: str,
) -> None:
    """Test parsing of packages into ParsedPackage structures with real-like data.

    Calls into _go_list_deps. Low level go command interaction testing was already done in
    test_go_list_deps. Note querying workspaces will return some data duplicated - that's
    expected.
    """
    go_work = None
    mocked_indata: str

    ws_paths: list = []
    mocked_outdata = json.loads(get_mocked_data(data_dir, f"expected-results/{expected_outfile}"))
    expected = {ParsedPackage(**package) for package in mocked_outdata["packages"]}

    go = mock.MagicMock(spec=Go)
    if input_subdir != "workspaces":
        mocked_indata = get_mocked_data(data_dir, f"{input_subdir}/go_list_deps_threedot.json")
        go.return_value = mocked_indata
    else:
        side_effects = []
        mock_get_go_work.return_value = get_mocked_data(data_dir, f"{input_subdir}/go_work.json")
        go_work = GoWork.from_file(rooted_tmp_path.join_within_root("go.work"), go)

        # add each <workspace_module>/go_list_deps_threedot.json as a side-effect to Go() execution
        ws_paths = go_work.workspace_paths
        for wp in ws_paths:
            wp_relative = wp.relative_to(go_work.path.parent)
            indata_relative = f"{input_subdir}/{wp_relative}/go_list_deps_threedot.json"
            mocked_indata = get_mocked_data(data_dir, indata_relative)
            side_effects.append(mocked_indata)

        go.side_effect = side_effects

    run_params = {"env": {"GOMODCACHE": "foo"}}
    pkgs = _parse_packages(go_work, go, run_params)

    calls = go.call_args_list
    if input_subdir != "workspaces":
        go.assert_called_once()
    else:
        calls = go.call_args_list
        assert go.call_count == len(ws_paths)
        assert all([run_params | {"cwd": ws_paths[i]} in c.args for i, c in enumerate(calls)])

    # _parse_packages calls _go_list_deps always with the './...' pattern
    assert all("./..." in call.args[0] for call in calls)
    assert set(pkgs) == expected


@pytest.mark.parametrize(
    "PATH,file_tree,binary_count",
    [
        pytest.param(None, {}, 0, id="no_go_binaries"),
        pytest.param(
            None,
            {"usr": {"local": {"go": {"bin": {"go": ""}}}}},
            1,
            id="none_path_with_usr_local",
        ),
        pytest.param(
            "",
            {"usr": {"local": {"go": {"bin": {"go": ""}}}}},
            1,
            id="empty_path_with_usr_local",
        ),
        pytest.param(
            "/bin:/usr/bin",
            {
                "bin": {},
                "usr": {"bin": {}},
                ".cache": {"go": {"go1.21": {"bin": {"go": ""}}, "go1.22": {"bin": {"go": ""}}}},
            },
            2,
            id="only_in_cache",
        ),
        pytest.param(
            "/bin:/usr/bin",
            {
                "bin": {"go": ""},
                "usr": {"bin": {"go": ""}, "local": {"bin": {"go": ""}}},
                ".cache": {"go": {"go1.21": {"bin": {"go": ""}}, "go1.22": {"bin": {"go": ""}}}},
            },
            5,
            id="path_and_cache",
        ),
        pytest.param(
            "/usr/go/bin:/usr/go/bin",
            {"usr": {"bin": {"go": ""}, "local": {"bin": {"go": ""}}}},
            2,
            id="deduplicate_paths",
        ),
        pytest.param(
            "/opt/go/bin:/usr/go/bin",
            {
                "opt": {"go": {"bin": {"go": ""}}},
                "usr": {"go": {"bin": {"go": Symlink("../../../opt/go/bin/go")}}},
            },
            1,
            id="filter_symlinked_path",
        ),
    ],
)
@mock.patch.dict(os.environ, {}, clear=False)
@mock.patch("hermeto.core.package_managers.gomod.get_cache_dir")
@mock.patch("hermeto.core.package_managers.gomod.Go", spec=Go)
def test_list_installed_toolchains(
    mock_go: mock.Mock,
    mock_get_cache_dir: mock.Mock,
    tmp_path: Path,
    PATH: str | None,
    file_tree: dict,
    binary_count: int,
) -> None:
    """Test various combinations of PATH, cache, and /usr/local Go installations."""
    mock_get_cache_dir.return_value = tmp_path
    mock_go.side_effect = mock_go_class
    write_file_tree(file_tree, tmp_path, exist_ok=True)

    if not PATH:
        os.environ.update({"PATH": ""})
    else:
        paths = PATH.split(":")
        prefixed_paths = [f"{tmp_path}/{path}" for path in paths]
        os.environ["PATH"] = ":".join(prefixed_paths)

    with mock.patch(
        "hermeto.core.package_managers.gomod.HERMETO_GO_INSTALL_DIR",
        new=Path(tmp_path, "usr/local"),
    ):
        result = _list_installed_toolchains()
    assert len(result) == binary_count
    assert mock_go.call_count == binary_count


@pytest.mark.parametrize(
    "gowork_output, expected",
    [
        pytest.param("", None, id="empty_gowork"),
        pytest.param(" off ", None, id="disabled_gowork"),
        pytest.param("./go.work", "./go.work", id="relative_path"),
        pytest.param("go.work\n", "go.work", id="path_with_trailing_newline"),
    ],
)
def test_get_go_work_path(
    rooted_tmp_path: RootedPath, gowork_output: str, expected: str | None
) -> None:
    mock_go = mock.Mock(spec=Go)
    mock_go.return_value = gowork_output

    result = _get_go_work_path(mock_go, rooted_tmp_path)

    if expected is None:
        assert result is None
    else:
        assert result is not None
        assert result == rooted_tmp_path.join_within_root(expected)

    mock_go.assert_called_once()
    assert mock_go.call_args[0][0] == ["env", "GOWORK"]
    assert mock_go.call_args[0][1] == {"cwd": rooted_tmp_path}


@pytest.mark.parametrize(
    "dir_path, files, expected",
    [
        pytest.param(
            "pkg/mod/cache/download/golang.org/toolchain/@v",
            ["v0.0.1-go1.21.5.linux-amd64.zip", "v0.0.1-go1.22.0.linux-amd64.zip"],
            ["v0.0.1-go1.21.5.linux-amd64.zip", "v0.0.1-go1.22.0.linux-amd64.zip"],
            id="toolchain_files",
        ),
        pytest.param(
            "pkg/mod/cache/download/sumdb/sum.golang.org/lookup",
            [
                "golang.org/toolchain@v0.0.1-go1.21.5.linux-amd64",
                "github.com/example/module@v1.0.0",
            ],
            ["golang.org/toolchain@v0.0.1-go1.21.5.linux-amd64"],
            id="mixed_sumdb_files",
        ),
        pytest.param(
            "pkg/mod/cache/download/golang.org/x/crypto/@v",
            ["v0.1.0.mod", "v0.1.0.zip"],
            [],
            id="golang_org_module_files",
        ),
        pytest.param(
            "pkg/mod/cache/download/github.com/example/module/@v",
            ["v1.0.0.zip", "v1.1.0.zip"],
            [],
            id="other_module_files",
        ),
        pytest.param(
            "foo/bar/golang.org/toolchain/@v",
            ["version.zip"],
            [],
            id="toolchain_under_strange_path",
        ),
        pytest.param(
            "pkg/mod/cache/download",
            [],
            [],
            id="empty_files_list",
        ),
    ],
)
def test_ignore_toolchain_files(dir_path: str, files: list[str], expected: list[str]) -> None:
    result = _list_toolchain_files(dir_path, files)
    assert result == expected


class TestGo:
    # Override the module-level autouse fixture — no test in this class needs it
    @pytest.fixture(autouse=True)
    def mock_go_release(self) -> Iterator[None]:
        yield

    @pytest.mark.parametrize(
        "goversion_output, expected",
        [
            pytest.param("go1.21.0\n", "go1.21.0", id="vanilla"),
            pytest.param("go1.25.7 X:nodwarf5\n", "go1.25.7", id="extra_build_flags"),
            pytest.param("go1.21.0-asdf\n", "go1.21.0-asdf", id="vendor_suffix"),
        ],
    )
    @mock.patch("hermeto.core.package_managers.gomod.run_cmd")
    def test_get_release(
        self,
        mock_run: mock.Mock,
        goversion_output: str,
        expected: str,
    ) -> None:
        mock_run.return_value = goversion_output
        go = Go()
        assert go._get_release() == expected

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({}, id="no_params"),
            pytest.param(
                {
                    "env": {"GOCACHE": "/foo", "GOTOOLCHAIN": "local"},
                    "cwd": "/foo/bar",
                    "text": True,
                },
                id="with_params",
            ),
        ],
    )
    @mock.patch("hermeto.core.package_managers.gomod.run_cmd")
    def test_run(
        self,
        mock_run: mock.Mock,
        params: dict,
    ) -> None:
        cmd = [GO_CMD_PATH, "mod", "download"]
        Go._run(cmd, **params)
        mock_run.assert_called_once_with(cmd, params)

    @pytest.mark.parametrize(
        "bin_, params, tries_needed",
        [
            pytest.param(None, {}, 1, id="bundled_go_1_try"),
            pytest.param("/usr/bin/go1.21", {}, 2, id="custom_go_2_tries"),
            pytest.param(
                None,
                {
                    "env": {"GOCACHE": "/foo", "GOTOOLCHAIN": "local"},
                    "cwd": "/foo/bar",
                    "text": True,
                },
                5,
                id="bundled_go_params_5_tries",
            ),
        ],
    )
    @mock.patch("hermeto.core.package_managers.gomod.get_config")
    @mock.patch("hermeto.core.package_managers.gomod.run_cmd")
    @mock.patch("time.sleep")
    def test_retry(
        self,
        mock_sleep: mock.Mock,
        mock_run: mock.Mock,
        mock_config: mock.Mock,
        bin_: str,
        params: dict,
        tries_needed: int,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_config.return_value.gomod.download_max_tries = 5

        # We don't want to mock subprocess.run here, because:
        # 1) the call chain looks like this: Go()._retry->run_go->self._run->run_cmd->subprocess.run
        # 2) we wouldn't be able to check if params are propagated correctly since run_cmd adds some too
        failure = subprocess.CalledProcessError(returncode=1, cmd="foo")
        success = 1
        mock_run.side_effect = [failure for _ in range(tries_needed - 1)] + [success]

        if bin_:
            go = Go(bin_)
        else:
            go = Go()

        cmd = [go.binary, "mod", "download"]
        go._retry(cmd, **params)
        mock_run.assert_called_with(cmd, params)
        assert mock_run.call_count == tries_needed
        assert mock_sleep.call_count == tries_needed - 1

    @mock.patch("hermeto.core.package_managers.gomod.get_config")
    @mock.patch("hermeto.core.package_managers.gomod.run_cmd")
    @mock.patch("time.sleep")
    def test_retry_failure(
        self, mock_sleep: Any, mock_run: Any, mock_config: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_config.return_value.gomod.download_max_tries = 5

        failure = subprocess.CalledProcessError(returncode=1, cmd="foo")
        mock_run.side_effect = [failure] * 5
        go = Go()

        error_msg = f"Go execution failed: {APP_NAME} re-tried running `{go.binary} mod download` command 5 times."

        with pytest.raises(PackageManagerError, match=error_msg):
            go._retry([go.binary, "mod", "download"])

        assert mock_run.call_count == 5
        assert mock_sleep.call_count == 4

    @pytest.mark.parametrize("release", ["go1.20", "go1.21.1"])
    @mock.patch.object(Go, "__post_init__", lambda self: None)
    @mock.patch("hermeto.core.package_managers.gomod.tempfile.TemporaryDirectory")
    @mock.patch("pathlib.Path.home")
    @mock.patch("hermeto.core.package_managers.gomod.Go._retry")
    @mock.patch("hermeto.core.package_managers.gomod.get_cache_dir")
    def test_from_missing_toolchain(
        self,
        mock_cache_dir: mock.Mock,
        mock_go_retry: mock.Mock,
        mock_path_home: mock.Mock,
        mock_temp_dir: mock.Mock,
        tmp_path: Path,
        release: str,
    ) -> None:
        """
        Test that given a release string we can download a Go SDK from the official sources and
        instantiate a new Go instance from the downloaded toolchain.

        NOTE: There is a module-level 'shutil.which' mock that applies to all tests and that would
        collide with what we're trying to test, so we need to override it and mock one level above:
        __post_init__.
        """
        dest_cache_dir = tmp_path / "cache"
        temp_dir = tmp_path / "tmpdir"
        env_vars = ["PATH", "GOPATH", "GOCACHE", "HOME"]

        # This is to simulate the filesystem operations the tested method performs
        temp_dir.mkdir()
        sdk_source_dir = temp_dir / f"sdk/{release}"
        sdk_bin_dir = sdk_source_dir / "bin"
        sdk_bin_dir.mkdir(parents=True)
        sdk_bin_dir.joinpath("go").touch()

        mock_cache_dir.return_value = dest_cache_dir
        mock_go_retry.return_value = 0
        mock_path_home.return_value = tmp_path
        mock_temp_dir.return_value.__enter__.return_value = str(temp_dir)
        mock_temp_dir.return_value.__exit__.return_value = None

        result_go = Go.from_missing_toolchain(release, GO_CMD_PATH)

        assert mock_go_retry.call_count == 2  # 'go install' && '<go-shim> download'
        assert mock_go_retry.call_args_list[0][0][0][0] == GO_CMD_PATH
        assert mock_go_retry.call_args_list[0][0][0][1] == "install"
        assert mock_go_retry.call_args_list[0][0][0][2] == f"golang.org/dl/{release}@latest"
        assert mock_go_retry.call_args_list[0][1].get("env") is not None
        assert set(mock_go_retry.call_args_list[0][1]["env"].keys()) == set(env_vars)
        assert mock_go_retry.call_args_list[1][0][0][1] == "download"

        target_binary = dest_cache_dir / f"go/{release}/bin/go"
        assert not sdk_source_dir.exists()
        assert target_binary.exists()
        assert result_go.binary == str(target_binary)

    @pytest.mark.parametrize(
        "release, retry",
        [
            pytest.param(None, False, id="bundled_go"),
            pytest.param("go1.20", True, id="custom_release_installed"),
            pytest.param("go1.21.0", True, id="custom_release_needs_installation"),
        ],
    )
    @mock.patch("hermeto.core.package_managers.gomod.get_config")
    @mock.patch("hermeto.core.package_managers.gomod.Go._run")
    def test_call(
        self,
        mock_run: mock.Mock,
        mock_get_config: mock.Mock,
        tmp_path: Path,
        release: str | None,
        retry: bool,
    ) -> None:
        env = {"env": {"GOTOOLCHAIN": "local", "GOCACHE": "foo", "GOPATH": "bar"}}
        opts = ["mod", "download"]
        go = Go()
        go(opts, retry=retry, params=env)

        cmd = [go.binary, *opts]
        if not retry:
            mock_run.assert_called_once_with(cmd, **env)
        else:
            mock_get_config.return_value.gomod.download_max_tries = 1
            mock_run.call_count = 1
            mock_run.assert_called_with(cmd, **env)

    @pytest.mark.parametrize("retry", [False, True])
    @mock.patch("hermeto.core.package_managers.gomod.get_config")
    @mock.patch("subprocess.run")
    def test_call_failure(
        self,
        mock_run: mock.Mock,
        mock_get_config: mock.Mock,
        retry: bool,
    ) -> None:
        tries = 1
        mock_get_config.return_value.gomod.download_max_tries = tries
        failure = proc_mock(returncode=1, stdout="")
        mock_run.side_effect = [failure]

        opts = ["mod", "download"]
        cmd = [GO_CMD_PATH, *opts]
        error_msg = "Go execution failed: "
        if retry:
            error_msg += f"{APP_NAME} re-tried running `{' '.join(cmd)}` command {tries} times."
        else:
            error_msg += f"`{' '.join(cmd)}` failed with rc=1"

        with pytest.raises(PackageManagerError, match=error_msg):
            go = Go()
            go(opts, retry=retry)

        assert mock_run.call_count == 1


class TestGoWork:
    def test_init(
        self,
        rooted_tmp_path: RootedPath,
        data_dir: Path,
    ) -> None:
        go_work_path = rooted_tmp_path.join_within_root("foo/bar/baz/go.work")
        go_work_data = ParsedGoWork.model_validate_json(
            get_mocked_data(data_dir, "workspaces/go_work.json")
        ).model_dump()
        go_work = GoWork(go_work_path, go_work_data)
        assert go_work.rooted_path == go_work_path
        assert go_work.path == go_work_path.path
        assert go_work.data == go_work_data

    @mock.patch("hermeto.core.package_managers.gomod.GoWork._get_go_work")
    def test_from_file(
        self,
        mock_get_go_work: mock.Mock,
        rooted_tmp_path: RootedPath,
        data_dir: Path,
    ) -> None:
        go_work_path = rooted_tmp_path.join_within_root("go.work")
        mock_get_go_work.return_value = get_mocked_data(data_dir, "workspaces/go_work.json")
        go_work_data = ParsedGoWork.model_validate_json(
            get_mocked_data(data_dir, "workspaces/go_work.json")
        ).model_dump()
        go_work = GoWork.from_file(go_work_path, mock.Mock(spec=Go))
        assert go_work.rooted_path == go_work_path
        assert go_work.path == go_work_path.path
        assert go_work.data == go_work_data

    @pytest.mark.parametrize(
        "go_work_data, expected",
        [
            pytest.param({}, False, id="empty"),
            pytest.param({"foo": "bar"}, True, id="with_data"),
        ],
    )
    def test_bool(self, rooted_tmp_path: RootedPath, go_work_data: dict, expected: bool) -> None:
        assert bool(GoWork(rooted_tmp_path, go_work_data)) is expected

    @pytest.mark.parametrize(
        "go_work_json, expected",
        [
            pytest.param('{"Go": "1.999.999"}', [], id="minimal_go_work"),
            pytest.param(
                """
                {
                    "Go": "1.999.999",
                    "Use": [
                        {"DiskPath": "."},
                        {"DiskPath": "./foo/bar"},
                        {"DiskPath": "./bar/baz"}
                    ]
                }
                """,
                [".", "./foo/bar", "./bar/baz"],
                id="complex_go_work",
            ),
        ],
    )
    @mock.patch("hermeto.core.package_managers.gomod.GoWork._get_go_work")
    def test_workspace_paths(
        self,
        mock_get_go_work: mock.Mock,
        rooted_tmp_path: RootedPath,
        go_work_json: str,
        expected: list,
    ) -> None:
        """Test our workspace path reporting as properly re-rooted RootedPath instances."""
        mock_get_go_work.return_value = go_work_json
        go_work_path = rooted_tmp_path.join_within_root("subdir/go.work")
        mock_get_go_work.return_value = go_work_json

        expected = [go_work_path.path.parent / p for p in expected]

        go_work = GoWork.from_file(go_work_path, mock.Mock(spec=Go))
        assert list(go_work.workspace_paths) == expected
        mock_get_go_work.assert_called_once()

    def test_get_go_work(self) -> None:
        mock_go = mock.Mock(spec=Go)
        mock_go.return_value = None

        GoWork._get_go_work(mock_go, {})

        mock_go.assert_called_once()
        assert mock_go.call_args[0][0] == ["work", "edit", "-json"]


_ENV_VARS_BASE_INIT = {v: "/some/path" for v in ("PATH", "HOME", "NETRC")}


@pytest.mark.parametrize(
    "env, extra_env, expected",
    [
        pytest.param(_ENV_VARS_BASE_INIT, None, _ENV_VARS_BASE_INIT, id="vars_inherited"),
        pytest.param(
            {},
            None,
            {"PATH": "", "HOME": "/mocked/home", "NETRC": ""},
            id="vars_defaults",
        ),
        pytest.param(
            _ENV_VARS_BASE_INIT,
            {"GOPATH": "/tmp/go"},
            _ENV_VARS_BASE_INIT | {"GOPATH": "/tmp/go"},
            id="with_extra_env",
        ),
    ],
)
@mock.patch("pathlib.Path.home", return_value=Path("/mocked/home"))
def test_go_exec_env(
    mock_home: mock.Mock,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    extra_env: dict[str, str] | None,
    expected: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", env)

    actual = _go_exec_env() if extra_env is None else _go_exec_env(**extra_env)
    assert actual == expected
