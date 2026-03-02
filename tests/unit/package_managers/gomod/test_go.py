# SPDX-License-Identifier: GPL-3.0-or-later
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
from hermeto.core.package_managers.gomod.main import (
    Go,
)
from tests.unit.package_managers.gomod.test_main import proc_mock

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
