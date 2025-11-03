import errno
import io
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from hermeto import APP_NAME
from hermeto.core.errors import BaseError
from hermeto.core.utils import (
    _fast_copy,
    _FastCopyFailedFallback,
    copy_directory,
    get_cache_dir,
    run_cmd,
)


@mock.patch("subprocess.run")
@mock.patch("shutil.which")
@pytest.mark.parametrize(
    "stdout, stderr, expect_logs",
    [
        # when stderr is empty, log that fact and log the stdout
        ("failed", None, ["STDERR: <empty>", "STDOUT:\nfailed"]),
        ("failed", "", ["STDERR: <empty>", "STDOUT:\nfailed"]),
        # when stderr is not empty, don't log stdout no matter what
        (None, "failed", ["STDERR:\nfailed"]),
        ("", "failed", ["STDERR:\nfailed"]),
        ("some info", "failed", ["STDERR:\nfailed"]),
        # when both are empty, log that fact and be sad :(
        ("", "", ["STDERR: <empty>", "STDOUT: <empty>"]),
        # test that newlines are stripped
        ("", "failed\n", ["STDERR:\nfailed"]),
        ("failed", "", ["STDERR: <empty>", "STDOUT:\nfailed"]),
    ],
)
def test_run_cmd_logs_stdouterr_on_failure(
    mock_shutil_which: mock.Mock,
    mock_subprocess_run: mock.Mock,
    stdout: str | None,
    stderr: str | None,
    expect_logs: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_shutil_which.return_value = "/usr/local/bin/some"
    mock_subprocess_run.return_value = subprocess.CompletedProcess(
        ["some", "command"], returncode=1, stdout=stdout, stderr=stderr
    )

    with pytest.raises(subprocess.CalledProcessError):
        run_cmd(["some", "command"], {})

    assert caplog.messages == ['The command "some command" failed', *expect_logs]


@mock.patch("subprocess.run")
@mock.patch("shutil.which")
@pytest.mark.parametrize("extra_args", [True, False])
def test_run_cmd_execute_by_abspath(
    mock_shutil_which: mock.Mock,
    mock_subprocess_run: mock.Mock,
    extra_args: bool,
) -> None:
    mock_shutil_which.return_value = "/usr/local/bin/yarn"
    mock_subprocess_run.return_value = subprocess.CompletedProcess([], returncode=0)

    if extra_args:
        cmd = ["yarn", "--version"]
        expect_cmd = ["/usr/local/bin/yarn", "--version"]
    else:
        cmd = ["yarn"]
        expect_cmd = ["/usr/local/bin/yarn"]

    run_cmd(cmd, params={})

    mock_subprocess_run.assert_called_once()
    call = mock_subprocess_run.call_args_list[0]
    assert call.args[0] == expect_cmd


@mock.patch("shutil.which")
def test_run_cmd_executable_not_found(
    mock_shutil_which: mock.Mock,
) -> None:
    mock_shutil_which.return_value = None

    with pytest.raises(BaseError, match="'foo' executable not found in PATH"):
        run_cmd(["foo"], params={})


@pytest.mark.skipif(sys.platform != "linux", reason="os.copy_file_range is only available on Linux")
@mock.patch("hermeto.core.utils._get_blocksize")
def test_fast_copy(mock_blocksize: mock.Mock, tmp_path: Path) -> None:
    mock_blocksize.return_value = 4
    src = tmp_path / "src/test"
    dest = tmp_path / "dest/test"
    for dir_ in [src.parent, dest.parent]:
        dir_.mkdir()
    test_str = "hello world"

    with open(src, "w") as fd:
        fd.write(test_str)

    nbytes = _fast_copy(src, dest)
    assert nbytes == len(test_str)


@pytest.mark.skipif(sys.platform != "linux", reason="os.copy_file_range is only available on Linux")
@pytest.mark.parametrize(
    "errno_",
    [
        pytest.param(errno.ENOSYS, id="ENOSYS"),
        pytest.param(errno.EXDEV, id="EXDEV"),
        pytest.param(errno.EAGAIN, id="unexpected_errno"),
    ],
)
@mock.patch("os.copy_file_range")
def test_fast_copy_fail_errno(mock_copy_range: mock.Mock, tmp_path: Path, errno_: int) -> None:
    src = tmp_path / "src/test"
    dest = tmp_path / "dest/test"
    for dir_ in [src.parent, dest.parent]:
        dir_.mkdir()

    mock_copy_range.side_effect = OSError(errno_)

    with pytest.raises(Exception) as ex:
        _fast_copy(src, dest)

        if errno_ == errno.EAGAIN:
            assert isinstance(ex, OSError)
        else:
            assert isinstance(ex, _FastCopyFailedFallback)


@pytest.mark.skipif(sys.platform != "linux", reason="os.copy_file_range is only available on Linux")
@mock.patch("hermeto.core.utils.open")
def test_fast_copy_fail_io_fileno(mock_open: mock.MagicMock, tmp_path: Path) -> None:
    """Test that we correctly signal a fallback to regular copy with a irregular files."""
    # inherits OSError
    mock_file = mock.Mock()
    mock_file.fileno.side_effect = io.UnsupportedOperation
    mock_open.return_value.__enter__.return_value = mock_file

    with pytest.raises(_FastCopyFailedFallback):
        _fast_copy(tmp_path / "src/foo", tmp_path / "dest/foo")


@pytest.mark.skipif(sys.platform != "linux", reason="os.copy_file_range is only available on Linux")
@mock.patch("os.copy_file_range")
@mock.patch("hermeto.core.utils.open")
def test_fast_copy_fail_no_data_copied(
    mock_open: mock.MagicMock, mock_copy_range: mock.Mock, tmp_path: Path
) -> None:
    """Test that we correctly signal a fallback to regular copy on fast copy IO errors."""
    mock_file = mock.Mock()
    mock_file.tell.return_value = 0
    mock_open.return_value.__enter__.return_value = mock_file
    mock_copy_range.return_value = 0

    with pytest.raises(_FastCopyFailedFallback):
        _fast_copy(tmp_path / "src/foo", tmp_path / "dest/foo")


@pytest.mark.skipif(sys.platform != "linux", reason="os.copy_file_range is only available on Linux")
@mock.patch("shutil.copy2")
@mock.patch("os.copy_file_range")
def test_copy_directory(
    mock_copy_range: mock.Mock,
    mock_shutil_copy2: mock.Mock,
    tmp_path: Path,
) -> None:
    mock_copy_range.side_effect = _FastCopyFailedFallback
    mock_shutil_copy2.return_value = None

    # prepare dummy copy data
    destination = tmp_path.joinpath("dst")
    origin = tmp_path.joinpath("src")
    origin.mkdir()
    origin.joinpath("foo").touch()

    copy_directory(origin, destination)

    # check we called both copy_functions (_fast_copy, copy2)
    mock_copy_range.assert_called_once()
    mock_shutil_copy2.assert_called_once()


@pytest.mark.parametrize("environ", [{"XDG_CACHE_HOME": "/tmp/xdg_home/"}, {}])
@mock.patch("pathlib.Path.home")
@mock.patch("os.environ")
def test_get_cache_dir(
    mock_environ: mock.MagicMock,
    mock_home_path: mock.Mock,
    environ: dict,
    tmp_path: Path,
) -> None:
    mock_environ.__getitem__.side_effect = environ.__getitem__
    mock_home_path.return_value = tmp_path

    if environ:
        expected = Path(environ["XDG_CACHE_HOME"], f"{APP_NAME}")
    else:
        expected = Path(tmp_path, f".cache/{APP_NAME}")

    assert get_cache_dir() == expected
