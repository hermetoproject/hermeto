import errno
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from functools import cache
from itertools import filterfalse, tee
from pathlib import Path
from typing import Any, Callable, Optional

from hermeto import APP_NAME
from hermeto.core.config import get_config
from hermeto.core.errors import BaseError

log = logging.getLogger(__name__)


class _FastCopyFailedFallback(Exception):
    """Signals a fall back from fast-in kernel copying to regular copy."""


def run_cmd(cmd: Sequence[str], params: dict) -> str:
    """
    Run the given command with provided parameters.

    :param iter cmd: iterable representing command to be executed
    :param dict params: keyword parameters for command execution
    :returns: the command output
    :rtype: str
    :raises CalledProcessError: if the command fails
    """
    params.setdefault("capture_output", True)
    params.setdefault("universal_newlines", True)
    params.setdefault("encoding", "utf-8")

    conf = get_config()
    params.setdefault("timeout", conf.subprocess_timeout)

    executable, *args = cmd
    executable_path = shutil.which(executable)
    if executable_path is None:
        raise BaseError(
            f"{executable!r} executable not found in PATH",
            solution=(
                f"Please make sure that the {executable!r} executable is installed in your PATH.\n"
                f"If you are using {APP_NAME} via its container image, this should not happen - please report this bug."
            ),
        )

    response = subprocess.run([executable_path, *args], **params)

    try:
        response.check_returncode()
    except subprocess.CalledProcessError:
        log.error('The command "%s" failed', " ".join(cmd))
        _log_error_output("STDERR", response.stderr)
        if not response.stderr:
            _log_error_output("STDOUT", response.stdout)
        raise

    return response.stdout


def _log_error_output(out_or_err: str, output: Optional[str]) -> None:
    if output:
        log.error("%s:\n%s", out_or_err, output.rstrip())
    else:
        log.error("%s: <empty>", out_or_err)


def load_json_stream(s: str) -> Iterator:
    """
    Load all JSON objects from input string.

    The objects can be separated by one or more whitespace characters. The return value is
    a generator that will yield the parsed objects one by one.
    """
    decoder = json.JSONDecoder()
    non_whitespace = re.compile(r"\S")
    i = 0

    while match := non_whitespace.search(s, i):
        obj, i = decoder.raw_decode(s, match.start())
        yield obj


@cache
def _get_blocksize(fd: int) -> int:
    """Determine blocksize for fastcopying on Linux.

    Hopefully the whole file will be copied in a single call.
    The copying itself should be performed in a loop 'till EOF is
    reached (0 return) so a blocksize smaller or bigger than the actual
    file size should not make any difference, also in case the file
    content changes while being copied.
    """
    BLK_8MiB = 2**23
    BLK_128MiB = 2**27
    BLK_1GiB = 2**30
    try:
        blocksize = max(os.fstat(fd).st_size, BLK_8MiB)
    except Exception:
        blocksize = BLK_128MiB

    # On 32-bit architectures truncate to 1 GiB to avoid OverflowError
    if sys.maxsize < 2**32:
        blocksize = min(blocksize, BLK_1GiB)

    return blocksize


def _fast_copy(src: Path, dest: Path, *, follow_symlinks: bool = True) -> int:
    """Perform a fast in-kernel copy using os.copy_file_range syscall.

    Copy data from source path to destination path using a high-performance copy_file_range(2)
    syscall. The syscall allows file systems to employ further optimizations like reflinks.

    This should work on Linux >= 4.5 only.

    :param src: source path
    :param dest: destination path
    :returns: number of bytes copied
    """
    total: int = 0
    with open(src, "rb") as fsrc, open(dest, "wb") as fdest:
        try:
            srcfd = fsrc.fileno()
            destfd = fdest.fileno()
        except OSError:
            # invalid stream or not a regular file (doesn't use a file descriptor)
            raise _FastCopyFailedFallback()

        try:
            while nbytes := os.copy_file_range(srcfd, destfd, count=_get_blocksize(srcfd)):
                total += nbytes

        except OSError as ex:
            # ...in oder to have a more informative exception.
            ex.filename = fsrc.name
            ex.filename2 = fdest.name

            if ex.errno == errno.ENOSYS or ex.errno == errno.EXDEV:
                raise _FastCopyFailedFallback

            raise ex from None

        # no data copied, copying from a pseudofilesystem? Not supported [1]
        # [1] https://docs.python.org/3/library/os.html#os.copy_file_range
        #
        # this should be very rare:
        # 1) copy within a pseudofilesystem requires elevated privileges which we
        #    normally don't have
        # 2) copy across filesystems raises EXDEV (handled above) on most kernel versions
        if total == 0 and fdest.tell() == 0:
            raise _FastCopyFailedFallback()
    return total


def copy_directory(origin: Path, destination: Path) -> Path:
    """
    Recursively copy directory to another path.

    Use fast in-kernel copying (including reflink file system optimization) and fall back to
    regular copy if the former fails for some reason.

    :raise FileExistsError: if the destination path already exists.
    :raise FileNotFoundError: if the origin directory does not exist.
    """

    def _copy_using(copy_function: Callable) -> None:
        shutil.copytree(
            origin,
            destination,
            copy_function=copy_function,
            dirs_exist_ok=True,
            symlinks=True,
            ignore=shutil.ignore_patterns(destination.name),
        )

    try:
        log.debug("Copying %s to %s using fast in-kernel copy.", origin, destination)
        _copy_using(_fast_copy)
    except _FastCopyFailedFallback:
        log.debug("Fast copying failed, falling back to standard copy.")
        shutil.rmtree(destination)
        _copy_using(shutil.copy2)

    return destination


def get_cache_dir() -> Path:
    """Return our application's global cache directory, useful for storing reusable data."""
    try:
        cache_dir = Path(os.environ["XDG_CACHE_HOME"])
    except KeyError:
        cache_dir = Path.home().joinpath(".cache")
    return cache_dir.joinpath(f"{APP_NAME}")


def first_for(predicate: Callable, iterable: Iterable, fallback: Any) -> Any:
    """Return the first match of predicate in iterable or fallback value."""
    return next((x for x in iterable if predicate(x)), fallback)


def partition_by(predicate: Callable, iterable: Iterable) -> tuple[Iterable, Iterable]:
    """Partition iterable in two by predicate."""
    i1, i2 = tee(iterable)
    return filterfalse(predicate, i1), filter(predicate, i2)
