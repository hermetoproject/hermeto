# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from hermeto import APP_NAME
from hermeto.core.errors import (
    PackageManagerError,
)
from hermeto.core.package_managers.gomod.go import (
    Go,
    GoVersion,
    GoWork,
    ParsedGoWork,
    _get_go_work_path,
    _get_gomod_version,
    _list_installed_toolchains,
    _list_toolchain_files,
    _select_toolchain,
)
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import Symlink, write_file_tree
from tests.unit.package_managers.gomod.helpers import get_mocked_data, proc_mock

GO_CMD_PATH = "/usr/bin/go"


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


@pytest.fixture
def go_mod_file(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    output_file = tmp_path / "go.mod"

    with open(output_file, "w") as f:
        f.write(request.param)


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
    @mock.patch("hermeto.core.package_managers.gomod.go.run_cmd")
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
    @mock.patch("hermeto.core.package_managers.gomod.go.run_cmd")
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
    @mock.patch("hermeto.core.package_managers.gomod.go.get_config")
    @mock.patch("hermeto.core.package_managers.gomod.go.run_cmd")
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

    @mock.patch("hermeto.core.package_managers.gomod.go.get_config")
    @mock.patch("hermeto.core.package_managers.gomod.go.run_cmd")
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
    @mock.patch("hermeto.core.package_managers.gomod.go.tempfile.TemporaryDirectory")
    @mock.patch("pathlib.Path.home")
    @mock.patch("hermeto.core.package_managers.gomod.go.Go._retry")
    @mock.patch("hermeto.core.package_managers.gomod.go.get_cache_dir")
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
    @mock.patch("hermeto.core.package_managers.gomod.go.get_config")
    @mock.patch("hermeto.core.package_managers.gomod.go.Go._run")
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
    @mock.patch("hermeto.core.package_managers.gomod.go.get_config")
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

    @mock.patch("hermeto.core.package_managers.gomod.go.GoWork._get_go_work")
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
    @mock.patch("hermeto.core.package_managers.gomod.go.GoWork._get_go_work")
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
@mock.patch("hermeto.core.package_managers.gomod.go.Go.from_missing_toolchain")
@mock.patch("hermeto.core.package_managers.gomod.go.Go._get_release")
@mock.patch("hermeto.core.package_managers.gomod.go._get_gomod_version")
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
@mock.patch("hermeto.core.package_managers.gomod.go.get_cache_dir")
@mock.patch("hermeto.core.package_managers.gomod.go.Go", spec=Go)
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
        "hermeto.core.package_managers.gomod.go.HERMETO_GO_INSTALL_DIR",
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


def mock_go_class(binary: str) -> mock.Mock:
    """Create a mock Go instance with a specific binary path."""
    mock_go = mock.Mock(spec=Go)
    mock_go.binary = binary
    return mock_go


@mock.patch.dict(os.environ, {"PATH": ""})
@mock.patch("shutil.which")
@mock.patch("hermeto.core.package_managers.gomod.go.Go._get_release")
def test_multi_toolchain_detection(
    mock_get_release: mock.Mock,
    mock_which: mock.Mock,
    tmp_path: Path,
) -> None:
    """
    Test that Hermeto can successfully detect a multi-toolchain environment
    by pointing the search paths to a temporary directory with fake binaries.
    """
    expected_go_releases = ["go1.20.1", "go1.21.0-custom.build.info"]
    system_go_dir = tmp_path / "usr" / "local" / "go"
    cache_go_dir = tmp_path / ".cache" / "hermeto" / "go" / "go1.21"

    for go_dir in (system_go_dir, cache_go_dir):
        bin_dir = go_dir / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "go").touch()
        (bin_dir / "go").chmod(0o755)

    mock_which.side_effect = lambda cmd, *args, **kwargs: cmd if Path(cmd).is_absolute() else None

    with (
        mock.patch("hermeto.core.package_managers.gomod.go.HERMETO_GO_INSTALL_DIR", system_go_dir),
        mock.patch(
            "hermeto.core.package_managers.gomod.go.get_cache_dir",
            return_value=tmp_path / ".cache" / "hermeto",
        ),
    ):
        mock_get_release.side_effect = expected_go_releases
        installed_toolchains = _list_installed_toolchains()

    expected = {GoVersion(release) for release in expected_go_releases}
    actual = {go.version for go in installed_toolchains}

    assert expected == actual
    assert mock_get_release.call_count == len(expected_go_releases)
