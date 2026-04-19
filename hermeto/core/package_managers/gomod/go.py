# SPDX-License-Identifier: GPL-3.0-only
import dataclasses
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections import UserDict
from collections.abc import Iterable
from functools import cache, cached_property, total_ordering
from pathlib import Path
from typing import Any, Sequence

import pydantic
from packaging import version
from pydantic.alias_generators import to_pascal
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from hermeto import APP_NAME
from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import PackageManagerError
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import get_cache_dir, run_cmd
from hermeto.interface.logging import EnforcingModeLoggerAdapter

log = EnforcingModeLoggerAdapter(logging.getLogger(__name__), {"enforcing_mode": Mode.STRICT})

HERMETO_GO_INSTALL_DIR = Path("/usr/local/go")


class _ParsedModel(pydantic.BaseModel):
    """Attributes automatically get PascalCase aliases to make parsing Golang JSON easier.

    >>> class SomeModel(_ParsedModel):
    ...     some_attribute: str

    >>> SomeModel.model_validate({"SomeAttribute": "hello"})
    SomeModel(some_attribute='hello')

    >>> SomeModel(some_attribute="hello")
    SomeModel(some_attribute='hello')
    """

    model_config = pydantic.ConfigDict(
        alias_generator=to_pascal, populate_by_name=True, frozen=True
    )


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
    MAX_VERSION: str = "1.26"

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


class GoWork(UserDict):
    """Representation of Go's go.work file."""

    def __init__(self, go_work_path: RootedPath, go_work_data: dict) -> None:
        """Initialize GoWork dict from a parsed go.work file."""
        super().__init__(**go_work_data)
        self._path = go_work_path

    @classmethod
    def from_file(cls, go_work_path: RootedPath, go: Go) -> "GoWork":
        """Instantiate GoWork from an absolute path to the go.work file."""
        go_work_json = cls._get_go_work(go, {"env": {"GOWORK": go_work_path.path}})
        data = ParsedGoWork.model_validate_json(go_work_json).model_dump()
        return cls(go_work_path, data)

    def __bool__(self) -> bool:
        return bool(self.data)

    @staticmethod
    def _get_go_work(go: Go, run_params: dict[str, Any]) -> str:
        return go(["work", "edit", "-json"], run_params)

    @property
    def rooted_path(self) -> RootedPath:
        """Return the go.work file path as rooted."""
        return self._path

    @cached_property
    def path(self) -> Path:
        """Return the go.work file path."""
        return self._path.path

    @cached_property
    def workspace_paths(self) -> list[Path]:
        """Get a list of absolute paths to all workspace modules."""
        _dir = RootedPath(self._path.root).join_within_root(self.path.parent)
        wp_paths = [p["disk_path"] for p in self["use"]]

        # Make sure the workspace paths don't point outside our rooted path
        return [(self.path.parent / wp).resolve() for wp in wp_paths if _dir.join_within_root(wp)]


class _GoWorkUseStruct(_ParsedModel):
    disk_path: str


class ParsedGoWork(_ParsedModel):
    """Repr of the go.work file returned by 'go work edit -json' (relevant fields only).

    See: go work help edit
    """

    go: str | None = None
    toolchain: str | None = None
    use: list[_GoWorkUseStruct] = []


def _list_toolchain_files(dir_path: str, files: list[str]) -> list[str]:
    def is_a_toolchain_path(path: str | os.PathLike[str]) -> bool:
        # Go automatically downloads toolchains to paths like:
        #   - pkg/mod/cache/download/golang.org/toolchain/@v/v0.0.1-go1.21.5.*
        #   - pkg/mod/cache/download/sumdb/sum.golang.org/lookup/golang.org/toolchain@v0.0.1-go1.21.5.*
        return "golang.org/toolchain" in str(path) and "pkg/mod/cache" in str(path)

    return [file for file in files if is_a_toolchain_path(Path(dir_path) / file)]


# NOTE: get rid of this go.mod parser once we can assume Go > 1.21 (1.20 can't parse micro release)
def _get_gomod_version(go_mod_file: RootedPath) -> tuple[str | None, str | None]:
    """Return the required/recommended version of Go from go.mod.

    We need to extract the desired version of Go ourselves as older versions of Go might fail
    due to e.g. unknown keywords or unexpected format of the version (yes, Go always performs
    validation of go.mod).

    If we cannot extract a version from the 'go' line, we return None, leaving it up to the caller
    to decide what to do next.
    """
    go_version = None
    toolchain_version = None

    # this needs to be able to handle arbitrary pre-release version identifiers and commentaries
    # as well, since Go itself can parse it
    # - 'go 1.21.0'
    # - '   go 1.21.0rc4'
    # - 'go 1.21beta1//commentary'
    version_str_regex = r"(?P<ver>\d+\.\d+(:?\.\d+)?(?:[a-zA-Z]+\d+)?)"
    post_version_chars_regex = r"\s*(?:\/\/.*)?"
    go_version_regex = rf"^\s*go\s+{version_str_regex}{post_version_chars_regex}$"
    toolchain_version_regex = rf"^\s*toolchain\s+go{version_str_regex}{post_version_chars_regex}$"

    go_pattern = re.compile(go_version_regex)
    toolchain_pattern = re.compile(toolchain_version_regex)

    with open(go_mod_file) as f:
        for i, line in enumerate(f):
            if not go_version and (match := re.match(go_pattern, line)):
                go_version = match.group("ver")
                log.debug("Matched Go version %s on go.mod line %d: '%s'", go_version, i, line)
                continue

            if not toolchain_version and (match := re.match(toolchain_pattern, line)):
                toolchain_version = match.group("ver")
                log.debug(
                    "Matched toolchain %s on go.mod line %d: '%s'", toolchain_version, i, line
                )
                continue

    return (go_version, toolchain_version)


def _select_toolchain(go_mod_file: RootedPath, installed_toolchains: Iterable[Go]) -> Go | None:
    """
    Pick the closest matching installed toolchain give a go.mod file.

    :param go_mod_file: path to an application go.mod file as RootedPath
    :param installed_toolchains: an iterable of Go instances pointing to actual Go binaries
    :return: a Go instance which matches the go.mod version constraints or None if we could not find
             a satisfying toolchain version installed
    """
    go_max_version = GoVersion.max()
    go_version_str, toolchain_version_str = _get_gomod_version(go_mod_file)

    if go_version_str:
        log.debug("go.mod file reports: 'go %s'", go_version_str)
    else:
        log.debug("No 'go' directive found in the go.mod file")

    if toolchain_version_str:
        log.debug("go.mod file reports: 'toolchain %s'", toolchain_version_str)
    else:
        log.debug("No 'toolchain' directive found in the go.mod file")

    if not go_version_str:
        # Go added the 'go' directive to go.mod in 1.12 [1]. If missing, 1.16 is assumed [2].
        # For our version comparison purposes we set the version explicitly to 1.20 if missing.
        # [1] https://go.dev/doc/go1.12#modules
        # [2] https://go.dev/ref/mod#go-mod-file-go
        go_version_str = "1.20"
        log.debug("Could not parse Go version from go.mod, using %s as fallback", go_version_str)

    if not toolchain_version_str:
        toolchain_version_str = go_version_str

    go_mod_version = GoVersion(go_version_str)
    go_mod_toolchain_version = GoVersion(toolchain_version_str)

    if go_mod_version >= go_mod_toolchain_version:
        target_version = go_mod_version
    else:
        target_version = go_mod_toolchain_version

    if target_version.to_language_version() > go_max_version.to_language_version():
        raise PackageManagerError(
            f"Required/recommended Go toolchain version '{target_version}' is not supported yet.",
            solution=(
                "Please lower your required/recommended Go version and retry the request. "
                "You may also want to open a feature request on adding support for this version."
            ),
        )

    # If we cannot find a matching toolchain, we'll try to fallback to a 1.21 one
    matching_toolchains = filter(lambda t: t.version >= target_version, installed_toolchains)
    try:
        # pick the closest matching toolchain version for best compatibility
        go = min(matching_toolchains)
        log.debug("Using Go toolchain version '%s'", go.version)
    except ValueError:
        try:
            # No installed toolchain satisfied the exact version spec, relax the condition
            go = max(filter(lambda t: t.version >= GoVersion("1.21"), installed_toolchains))
            log.debug("Best matching Go toolchain version: '%s'", go.version)
            log.debug("Will use Go toolchain version '%s' [via GOTOOLCHAIN=auto]", target_version)
        except ValueError:
            # This is a long shot - we couldn't find a matching toolchain, nor have a toolchain
            # that can do GOTOOLCHAIN=auto, so we pick any installed toolchain (we know we have
            # some) and use it to download a new full-blown SDK for the target version
            log.debug("Installing Go toolchain version '%s'", target_version)
            release_str = f"go{str(target_version)}"
            try:
                work_toolchain = next(iter(installed_toolchains))
                go = Go.from_missing_toolchain(release_str, work_toolchain.binary)
                log.debug("Using Go toolchain version '%s'", go.version)
            except Exception as ex:
                log.error("Failed to download a Go toolchain version '%s': '%s'", release_str, ex)
                return None
    return go


def _list_installed_toolchains() -> set[Go]:
    """List all Go SDK installations we recognize.

    We look at:
        - /usr/local/go/                    container environments (Go pre-installed by us)
        - $XDG_CACHE_HOME/<APP_NAME>/go     local environments (Go downloaded & cached by us)
        - $PATH/go                          default system-wide Go installation

    :returns: A set of Go instances corresponding to the installations found
    """
    ret: set[Go] = set()
    paths: set[Path] = set()

    if pathvar := os.environ.get("PATH"):
        paths = {Path(p).resolve() for p in pathvar.split(":")}

    # we historically installed toolchains under (/usr/local|<our_cache_dir>)/go/go<version>/
    for path in (HERMETO_GO_INSTALL_DIR, get_cache_dir()):
        paths |= {p.resolve().parent for p in Path(path).rglob("bin/go")}

    # Resolve paths for deduplication only; preserve original symlink names for the binary.
    # Tools like snap rely on argv[0] for dispatch, resolving /snap/bin/go into /usr/bin/snap
    # which breaks Go execution.
    go_binary_paths: dict[Path, Path] = {}
    for p in paths:
        bin_path = Path(p, "go")
        if bin_path.exists():
            go_binary_paths.setdefault(bin_path.resolve(), bin_path)

    for bin_path in go_binary_paths.values():
        try:
            log.debug("Probing %s toolchain...", bin_path)
            ret.add(Go(binary=bin_path.as_posix()))
        except Exception as e:
            # Logging toolchain probing failures due to [1].
            # [1] https://bandit.readthedocs.io/en/1.8.3/plugins/b112_try_except_continue.html
            log.debug("Toolchain %s failed probing: %s, skipping...", bin_path, e)

    log.debug(
        "Found installed Go releases: %s\n", "\n".join(["\t- " + str(go.binary) for go in ret])
    )
    return ret


def _get_go_work_path(go: Go, app_dir: RootedPath) -> RootedPath | None:
    go_work_file = go(["env", "GOWORK"], {"cwd": app_dir}).strip()

    # workspaces can be disabled explicitly with GOWORK=off
    if not go_work_file or go_work_file == "off":
        return None

    # make sure that the path to go.work is within the request's root
    return app_dir.join_within_root(go_work_file)
