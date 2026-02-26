# SPDX-License-Identifier: GPL-3.0-only
import dataclasses
import logging
import os
import shutil
import subprocess
import tempfile
from functools import cache, cached_property, total_ordering
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from packaging import version
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from hermeto import APP_NAME
from hermeto.core.config import get_config
from hermeto.core.errors import PackageManagerError
from hermeto.core.models.input import Mode
from hermeto.core.utils import get_cache_dir, run_cmd
from hermeto.interface.logging import EnforcingModeLoggerAdapter

if TYPE_CHECKING:
    pass


log = EnforcingModeLoggerAdapter(logging.getLogger(__name__), {"enforcing_mode": Mode.STRICT})


class GoVersion(version.Version):
    """packaging.version.Version wrapper handling Go version/release reporting aspects.

    >>> v = GoVersion("1.21")
    >>> v.major, v.minor, v.micro
    (1, 21, 0)

    >>> v = GoVersion("go1.21.4")
    >>> v.major, v.minor, v.micro
    (1, 21, 4)

    >>> v = GoVersion("go1.25.7-asdf-xyz")
    >>> v.major, v.minor, v.micro
    (1, 25, 7)

    >>> v = GoVersion("1.21")
    >>> str(v.to_language_version())
    '1.21'

    >>> v = GoVersion("go1.22.1")
    >>> str(v.to_language_version())
    '1.22'

    >>> GoVersion("1.21") < GoVersion("1.22")
    True
    >>> GoVersion("1.21.4") > GoVersion("1.21.0")
    True
    >>> GoVersion("go1.21") == GoVersion("1.21")
    True
    >>> GoVersion("go1.21-asdf") == GoVersion("1.21")
    True
    """

    # NOTE: It might not be obvious at first glance why we need this wrapper to represent a Go
    # language/toolchain version string instead of semver - semver requires all parts to be
    # specified, i.e. 'major.minor.patch' which golang historically didn't use to represent
    # language versions, only toolchains, e.g. 1.22 is still an acceptable way of specifying a
    # required Go version in one's go.mod file.

    # !THIS IS WHERE THE SUPPORTED GO VERSION BY HERMETO NEEDS TO BE BUMPED!
    MAX_VERSION: str = "1.25"

    def __init__(self, version_str: str) -> None:
        """Initialize the GoVersion instance.

        :param version_str: version string in the form of X.Y(.Z)?(-[a-zA-Z0-9-]+)?
                            Note we also accept standard Go release strings prefixed with 'go'
        """
        ver = version_str if not version_str.startswith("go") else version_str[2:]
        # Strip vendor-specific suffixes introduced by a dash, e.g. "1.21.0-asdf"
        ver = ver.split("-", 1)[0]
        super().__init__(ver)

    @classmethod
    def max(cls) -> "GoVersion":
        """Instantiate and return a GoVersion object with the maximum supported version of Go."""
        return cls(cls.MAX_VERSION)

    @cache
    def to_language_version(self) -> version.Version:
        """
        Language version for the given Go version.

        Go differentiates between Go language versions (major, minor) and toolchain versions (major,
        minor, micro).
        """
        return version.Version(f"{self.major}.{self.minor}")


@total_ordering
@dataclasses.dataclass(frozen=True, init=True, eq=True)
class Go:
    """High level wrapper over the 'go' CLI command.

    Provides convenient methods to download project dependencies, alternative toolchains,
    parses various Go files, etc.
    """

    binary: str = dataclasses.field(default="go", hash=True)

    def __post_init__(self) -> None:
        """Initialize the Go toolchain wrapper.

        Validate binary existence as part of the process.

        :return: a callable instance
        :raises PackageManagerError: if Go toolchain is not found or invalid
        """
        resolved = shutil.which(self.binary)

        if resolved is None:
            raise PackageManagerError(
                f"Invalid Go binary path: {self.binary}",
                solution=(
                    "Please ensure Go is installed in $PATH or provide a valid path to the Go binary"
                ),
            )

        object.__setattr__(self, "binary", resolved)

    def __call__(self, cmd: list[str], params: dict | None = None, retry: bool = False) -> str:
        """Run a Go command using the underlying toolchain, same as running GoToolchain()().

        :param cmd: Go CLI options
        :param params: additional subprocess arguments, e.g. 'env'
        :param retry: whether the command should be retried on failure (e.g. network actions)
        :returns: Go command's output
        """
        if params is None:
            params = {}

        cmd = [self.binary] + cmd
        if retry:
            return self._retry(cmd, **params)

        return self._run(cmd, **params)

    def __lt__(self, other: "Go") -> bool:
        return self.version < other.version

    @classmethod
    def from_missing_toolchain(cls, release: str, binary: str = "go") -> "Go":
        """Fetch and install an alternative version of main Go toolchain.

        This method should only ever be needed with local installs, but not in container
        environment installs where we pre-install the latest Go toolchain available.
        Because Go can't really be told where the toolchain should be installed to, the process is
        as follows:
            1) we use the base Go toolchain to fetch a versioned toolchain shim to a temporary
               directory as we're going to dispose of the shim later
            2) we use the downloaded shim to actually fetch the whole SDK for the desired version
               of Go toolchain
            3) we move the installed SDK to our cache directory
               (i.e. $HOME/.cache/hermeto/go/<version>) to reuse the toolchains in subsequent runs
            4) we delete the downloaded shim as we're not going to execute the toolchain through
               that any longer
            5) we delete any build artifacts go created as part of downloading the SDK as those
               can occupy >~70MB of storage

        :param release: target Go release, e.g. go1.20, go1.21.10
        :param binary: path to Go binary to use to download/install 'release' versioned toolchain
        :param tmp_dir: global tmp dir where the SDK should be downloaded to
        :returns: path-like string to the newly installed toolchain binary
        """
        base_url = "golang.org/dl/"
        url = f"{base_url}{release}@latest"

        # Download the go<release> shim to a temporary directory and wipe it after we're done
        # Go would download the shim to $HOME too, but unlike 'go download' we can at least adjust
        # 'go install' to point elsewhere using $GOPATH. This is a known pitfall of Go, see the
        # references below:
        # [1] https://github.com/golang/go/issues/26520
        # [2] https://golang.org/cl/34385
        with tempfile.TemporaryDirectory(prefix=f"{APP_NAME}", suffix="go-download") as td:
            log.debug("Installing Go %s toolchain shim from '%s'", release, url)
            env = {
                "PATH": os.environ.get("PATH", ""),
                "GOPATH": td,
                "GOCACHE": str(Path(td, "cache")),
                "HOME": Path.home().as_posix(),
            }
            cls._retry([binary, "install", url], env=env)

            log.debug("Downloading Go %s SDK", release)
            env["HOME"] = td
            cls._retry([f"{td}/bin/{release}", "download"], env=env)

            # move the newly downloaded SDK to $HOME/.cache/hermeto/go
            sdk_download_dir = Path(td, f"sdk/{release}")
            go_dest_dir = get_cache_dir() / "go" / release
            if go_dest_dir.exists():
                if go_dest_dir.is_dir():
                    shutil.rmtree(go_dest_dir, ignore_errors=True)
                else:
                    go_dest_dir.unlink()
            shutil.move(sdk_download_dir, go_dest_dir)

        log.debug(f"Go {release} toolchain installed at: {go_dest_dir}")
        return cls((go_dest_dir / "bin/go").as_posix())

    @cached_property
    def version(self) -> GoVersion:
        """Version of the Go toolchain as a GoVersion object."""
        return GoVersion(self._get_release())

    def _get_release(self) -> str:
        output = self(["env", "GOVERSION"], params={"env": {"GOTOOLCHAIN": "local"}})
        log.debug(f"Go release: {output.strip()}")

        # Non-vanilla Go builds may report extra data in the version string, e.g. "go1.25.7 X:nodwarf5"
        return output.split()[0]

    @staticmethod
    def _retry(cmd: list[str], **kwargs: Any) -> str:
        """Run gomod command in a networking context.

        Commands that involve networking, such as dependency downloads, may fail due to network
        errors (go is bad at retrying), so the entire operation will be retried a configurable
        number of times.

        The same cache directory will be use between retries, so Go will not have to download the
        same artifact (e.g. dependency) twice. The backoff is exponential, we will wait 1s ->
        2s -> 4s -> ... before retrying.
        """
        n_tries = get_config().gomod.download_max_tries

        @retry(
            stop=stop_after_attempt(n_tries),
            wait=wait_exponential(),
            retry=retry_if_exception_type(PackageManagerError),
            reraise=True,
        )
        def run_go(_cmd: list[str], **kwargs: Any) -> str:
            return Go._run(_cmd, **kwargs)

        try:
            return run_go(cmd, **kwargs)
        except PackageManagerError:
            err_msg = (
                f"Go execution failed: {APP_NAME} re-tried running `{' '.join(cmd)}` command "
                f"{n_tries} times."
            )
            raise PackageManagerError(err_msg) from None

    @staticmethod
    def _run(cmd: Sequence[str], **params: Any) -> str:
        try:
            log.debug("Running `%s`", " ".join(cmd))
            return run_cmd(cmd, params)
        except subprocess.CalledProcessError as e:
            rc = e.returncode
            raise PackageManagerError(
                f"Go execution failed: `{' '.join(cmd)}` failed with {rc=}"
            ) from e
