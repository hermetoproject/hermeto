import asyncio
import errno
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from functools import cache
from itertools import filterfalse, tee
from os import PathLike
from pathlib import Path
from typing import Any, Callable, Optional, Union

import httpx
from requests import RequestException, Session
from requests.adapters import HTTPAdapter, Retry
from requests.auth import AuthBase

from hermeto import APP_NAME
from hermeto.core.config import get_config
from hermeto.core.errors import BaseError, FetchError

log = logging.getLogger(__name__)


def get_requests_session() -> Session:
    """Create a requests session with various retry options."""
    session = Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            connect=5,
            total=5,
            read=5,
            allowed_methods=["GET", "HEAD", "OPTIONS", "TRACE"],
            status_forcelist=(500, 502, 503, 504),
            backoff_factor=1.3,
        )
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


pkg_requests_session = get_requests_session()


def download_binary_file(
    url: str,
    download_path: Union[str, PathLike[str]],
    auth: Optional[AuthBase] = None,
    insecure: bool = False,
    chunk_size: int = 8192,
) -> None:
    """
    Download a binary file (such as a TAR archive) from a URL.

    :param str url: URL for file download
    :param (str | PathLike) download_path: Path to download file to
    :param requests.auth.AuthBase auth: Authentication for the URL
    :param bool insecure: Do not verify SSL for the URL
    :param int chunk_size: Chunk size param for Response.iter_content()
    :raise FetchError: If download failed
    """
    timeout = get_config().requests_timeout
    try:
        resp = pkg_requests_session.get(
            url, stream=True, verify=not insecure, auth=auth, timeout=timeout
        )
        resp.raise_for_status()
    except RequestException as e:
        raise FetchError(f"Could not download {url}: {e}")

    with open(download_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            f.write(chunk)


async def _async_download_binary_file(
    client: httpx.AsyncClient,
    url: str,
    download_path: Union[str, PathLike[str]],
) -> None:
    """Download a file from the given URL and save it."""
    timeout = get_config().requests_timeout

    try:
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        Path(download_path).write_bytes(response.content)

    except Exception as e:
        log.error(f"Unsuccessful download: {url}")
        raise FetchError(str(e)) from None

    log.debug(f"Download completed - {url}")


async def async_download_files(
    files_to_download: dict[str, Union[str, PathLike[str]]],
    ssl_context: Optional[ssl.SSLContext] = None,
) -> None:
    """Download multiple files asynchronously."""
    tasks: set[asyncio.Task] = set()

    verify = ssl_context if ssl_context is not None else True
    # check if .netrc file exists for authentication
    try:
        auth = httpx.NetRCAuth()
    except Exception:
        log.debug("No .netrc file found in the home directory.")
        auth = None

    async with httpx.AsyncClient(auth=auth, trust_env=True, verify=verify) as client:
        for url, download_path in files_to_download.items():
            tasks.add(asyncio.create_task(_async_download_binary_file(client, url, download_path)))

        await asyncio.gather(*tasks)


class _FastCopyFailedFallback(Exception):
    """Signals a fall back from fast-in kernel copying to regular copy."""


def run_cmd(cmd: Sequence[str], params: dict, suppress_errors: bool = False) -> str:
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
        if not suppress_errors:
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
            while nbytes := os.copy_file_range(srcfd, destfd, count=_get_blocksize(srcfd)):  # type: ignore
                total += nbytes

        # `os` module deos not have attribute `copy_file_range` on some platforms (see type ignore above)
        except AttributeError:
            raise _FastCopyFailedFallback()

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
