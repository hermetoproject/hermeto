import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections import UserDict
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from functools import cached_property
from itertools import chain
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, NoReturn, Optional, Union

import git
import pydantic
import semver
from packageurl import PackageURL
from packaging import version
from pydantic.alias_generators import to_pascal
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from hermeto import APP_NAME

if TYPE_CHECKING:
    from typing_extensions import Self

from hermeto.core.config import get_config
from hermeto.core.errors import FetchError, PackageManagerError, PackageRejected, UnexpectedFormat
from hermeto.core.models.input import Mode, Request
from hermeto.core.models.output import EnvironmentVariable, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import Component
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import get_repo_for_path, get_repo_id
from hermeto.core.utils import GIT_PRISTINE_ENV, get_cache_dir, load_json_stream, run_cmd
from hermeto.interface.logging import EnforcingModeLoggerAdapter

# NOTE: the 'extra' dict is unused right now, but it's a positional argument for the adapter class
log = EnforcingModeLoggerAdapter(logging.getLogger(__name__), {"enforcing_mode": Mode.STRICT})


GOMOD_DOC = "https://github.com/hermetoproject/hermeto/blob/main/docs/gomod.md"
GOMOD_INPUT_DOC = f"{GOMOD_DOC}#specifying-modules-to-process"
VENDORING_DOC = f"{GOMOD_DOC}#vendoring"

ModuleDict = dict[str, Any]


class _ParsedModel(pydantic.BaseModel):
    """Attributes automatically get PascalCase aliases to make parsing Golang JSON easier.

    >>> class SomeModel(_GolangModel):
            some_attribute: str

    >>> SomeModel.model_validate({"SomeAttribute": "hello"})
    SomeModel(some_attribute="hello")

    >>> SomeModel(some_attribute="hello")
    SomeModel(some_attribute="hello")
    """

    model_config = pydantic.ConfigDict(alias_generator=to_pascal, populate_by_name=True)


class ParsedModule(_ParsedModel):
    """A Go module as returned by the -json option of various commands (relevant fields only).

    See:
        go help mod download    (Module struct)
        go help list            (Module struct)
    """

    path: str
    version: Optional[str] = None
    main: bool = False
    replace: Optional["ParsedModule"] = None


class ParsedPackage(_ParsedModel):
    """A Go package as returned by the -json option of go list (relevant fields only).

    See:
        go help list    (Package struct)
    """

    import_path: str
    standard: bool = False
    module: Optional[ParsedModule] = None


class _GoWorkUseStruct(_ParsedModel):
    disk_path: str


class ParsedGoWork(_ParsedModel):
    """Repr of the go.work file returned by 'go work edit -json' (relevant fields only).

    See: go work help edit
    """

    go: Optional[str] = None
    toolchain: Optional[str] = None
    use: list[_GoWorkUseStruct] = []


class ResolvedGoModule(NamedTuple):
    """Contains the data for a resolved main module (a module in the user's repo)."""

    parsed_main_module: ParsedModule
    parsed_modules: Iterable[ParsedModule]
    parsed_packages: Iterable[ParsedPackage]
    modules_in_go_sum: frozenset["ModuleID"]


class Module(NamedTuple):
    """A Go module with relevant data for the SBOM generation.

    name: the resolved name for this module
    original_name: module's name as written in go.mod, before any replacement
    real_path: real path to locate the package on the Internet, which might differ from its name
    version: the resolved version for this module
    main: if this is the main module in the repository subpath that is being processed
    missing_hash_in_file: path (relative to repository root) to the go.sum file which should have
        had a checksum for this module but didn't
    """

    name: str
    original_name: str
    real_path: str
    version: str
    main: bool = False
    missing_hash_in_file: Optional[Path] = None

    @property
    def purl(self) -> str:
        """Get the purl for this module."""
        purl = PackageURL(
            type="golang",
            name=self.real_path,
            version=self.version,
            qualifiers={"type": "module"},
        )
        return purl.to_string()

    def to_component(self) -> Component:
        """Create a SBOM component for this module."""
        if self.missing_hash_in_file:
            missing_hash_in_file = frozenset([str(self.missing_hash_in_file)])
        else:
            missing_hash_in_file = frozenset()

        return Component(
            name=self.name,
            version=self.version,
            purl=self.purl,
            properties=PropertySet(missing_hash_in_file=missing_hash_in_file).to_properties(),
        )


class Package(NamedTuple):
    """A Go package with relevant data for the SBOM generation.

    relative_path: the package path relative to its parent module's name
    module: parent module for this package
    """

    relative_path: Optional[str]
    module: Module

    @property
    def name(self) -> str:
        """Get the name for this package based on the parent module's name."""
        if self.relative_path:
            return f"{self.module.name}/{self.relative_path}"

        return self.module.name

    @property
    def real_path(self) -> str:
        """Get the real path to locate this package on the Internet."""
        if self.relative_path:
            return f"{self.module.real_path}/{self.relative_path}"

        return self.module.real_path

    @property
    def purl(self) -> str:
        """Get the purl for this package."""
        purl = PackageURL(
            type="golang",
            name=self.real_path,
            version=self.module.version,
            qualifiers={"type": "package"},
        )
        return purl.to_string()

    def to_component(self) -> Component:
        """Create a SBOM component for this package."""
        return Component(name=self.name, version=self.module.version, purl=self.purl)


class StandardPackage(NamedTuple):
    """A package from Go standard lib used in the SBOM generation.

    Standard lib packages lack a parent module and, consequentially, a version.
    """

    name: str

    @property
    def purl(self) -> str:
        """Get the purl for this package."""
        purl = PackageURL(type="golang", name=self.name, qualifiers={"type": "package"})
        return purl.to_string()

    def to_component(self) -> Component:
        """Create a SBOM component for this package."""
        return Component(name=self.name, purl=self.purl)


# NOTE: Skim the class once we don't need to work with multiple versions of Go
class Go:
    """High level wrapper over the 'go' CLI command.

    Provides convenient methods to download project dependencies, alternative toolchains,
    parses various Go files, etc.
    """

    def __init__(
        self,
        binary: Union[str, os.PathLike[str]] = "go",
        release: Optional[str] = None,
    ) -> None:
        """Initialize the Go toolchain wrapper.

        :param binary: path-like string to the Go binary or direct command (in PATH)
        :param release: Go release version string, e.g. go1.20, go1.21.10
        :returns: a callable instance
        """
        # run_cmd will take care of checking any bogus passed in 'binary'
        self._bin = str(binary)
        self._release = release

        self._version: Optional[version.Version] = None
        self._install_toolchain: bool = False

        if self._release:
            if bin_ := self._locate_toolchain(self._release):
                self._bin = bin_
            else:
                log.debug(f"Desired toolchain '{self._release}' not found, will download it lazily")
                self._install_toolchain = True

    def __call__(self, cmd: list[str], params: Optional[dict] = None, retry: bool = False) -> str:
        """Run a Go command using the underlying toolchain, same as running GoToolchain()().

        :param cmd: Go CLI options
        :param params: additional subprocess arguments, e.g. 'env'
        :param retry: whether the command should be retried on failure (e.g. network actions)
        :returns: Go command's output
        """
        if params is None:
            params = {}

        # we check both values to silence the type checker complaining self._release might be None
        if self._install_toolchain and self._release:
            self._bin = self._install(self._release)
            self._install_toolchain = False

        cmd = [self._bin] + cmd
        if retry:
            return self._retry(cmd, **params)

        return self._run(cmd, **params)

    @property
    def version(self) -> version.Version:
        """Version of the Go toolchain as a packaging.version.Version object."""
        if not self._version:
            self._version = version.Version(self.release[2:])
        return self._version

    @property
    def release(self) -> str:
        """Release name of the Go Toolchain, e.g. go1.20 ."""
        # lazy evaluation: defer running 'go'
        if not self._release:
            output = self(["version"])
            log.debug(f"Go release: {output}")
            release_pattern = f"go{version.VERSION_PATTERN}"

            # packaging.version requires passing the re.VERBOSE|re.IGNORECASE flags [1]
            # [1] https://packaging.pypa.io/en/latest/version.html#packaging.version.VERSION_PATTERN
            if match := re.search(release_pattern, output, re.VERBOSE | re.IGNORECASE):
                self._release = match.group(0)
            else:
                # This should not happen, otherwise we must figure out a more reliable way of
                # extracting Go version
                raise PackageManagerError(
                    f"Could not extract Go toolchain version from Go's output: '{output}'",
                    solution=f"This is a fatal error, please open a bug report against {APP_NAME}",
                )
        return self._release

    @staticmethod
    def _locate_toolchain(release: str) -> Optional[str]:
        """Given a release locate an alternative Go toolchain.

        Locate an alternative Go toolchain under the one of the following locations:
            - /usr/local/go/                    for container environments (pre-installed)
            - $XDG_CACHE_HOME/hermeto/go         for local environments (download & cache)
        """
        local_cache = get_cache_dir()
        go_path_stub = f"go/{release}/bin/go"
        for p in [Path("/usr/local/", go_path_stub), Path(local_cache, go_path_stub)]:
            status = "SUCCESS" if p.exists() else "FAIL"

            log.debug(f"Trying to locate Go toolchain at '{p}': {status}")
            if p.exists():
                return str(p)

        return None

    def _install(self, release: str) -> str:
        """Fetch and install an alternative version of main Go toolchain.

        This method should only ever be needed with local installs, but not in container
        environment installs where we pre-install multiple Go versions.
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

        :param release: Go release version string, e.g. go1.20, go1.21.10
        :param env: params to use with the underlying subprocess and 'go' execution
        :returns: path-like string to the newly installed toolchain binary
        """
        base_url = "golang.org/dl/"
        url = f"{base_url}{release}@latest"

        # Download the go<release> shim to a temporary directory and wipe it after we're done
        # Go would download the shim to $HOME too, but unlike 'go download' we can at least adjust
        # 'go install' to point elsewhere using $GOPATH
        with tempfile.TemporaryDirectory(prefix=f"{APP_NAME}", suffix="go-download") as td:
            log.debug(f"Installing Go {release} toolchain shim from '{url}'")
            env = {
                "PATH": os.environ.get("PATH", ""),
                "GOPATH": td,
                "GOCACHE": str(Path(td, "cache")),
            }
            self._retry([self._bin, "install", url], env=env)

            log.debug(f"Downloading Go {release} SDK")
            self._retry([f"{td}/bin/{release}", "download"], env=env)

            # move the newly downloaded SDK from $HOME/sdk to $HOME/.cache/hermeto/go
            sdk_download_dir = Path.home() / f"sdk/{release}"
            go_dest_dir = get_cache_dir() / "go" / release
            shutil.move(sdk_download_dir, go_dest_dir)

        log.debug(f"Go {release} toolchain installed at: {go_dest_dir}")
        return str(go_dest_dir / "bin/go")

    def _retry(self, cmd: list[str], **kwargs: Any) -> str:
        """Run gomod command in a networking context.

        Commands that involve networking, such as dependency downloads, may fail due to network
        errors (go is bad at retrying), so the entire operation will be retried a configurable
        number of times.

        The same cache directory will be use between retries, so Go will not have to download the
        same artifact (e.g. dependency) twice. The backoff is exponential, we will wait 1s ->
        2s -> 4s -> ... before retrying.
        """
        n_tries = get_config().gomod_download_max_tries

        @retry(
            stop=stop_after_attempt(n_tries),
            wait=wait_exponential(),
            retry=retry_if_exception_type(PackageManagerError),
            reraise=True,
        )
        def run_go(_cmd: list[str], **kwargs: Any) -> str:
            return self._run(_cmd, **kwargs)

        try:
            return run_go(cmd, **kwargs)
        except PackageManagerError:
            err_msg = (
                f"Go execution failed: {APP_NAME} re-tried running `{' '.join(cmd)}` command "
                f"{n_tries} times."
            )
            raise PackageManagerError(err_msg) from None

    def _run(self, cmd: list[str], **kwargs: Any) -> str:
        try:
            log.debug(f"Running '{cmd}'")
            return run_cmd(cmd, kwargs)
        except subprocess.CalledProcessError as e:
            rc = e.returncode
            raise PackageManagerError(
                f"Go execution failed: `{' '.join(cmd)}` failed with {rc=}"
            ) from e


class GoWork(UserDict):
    """Representation of Go's go.work file."""

    def __init__(self, app_dir: RootedPath) -> None:
        """Initialize GoWork dict."""
        super().__init__()
        self._path = None
        self._app_dir = app_dir

        # workspaces may not be enabled -> empty instance
        if (rooted_path := self._get_go_work_path(app_dir)) is None:
            return

        self._path = rooted_path

    def __bool__(self) -> bool:
        return self._path is not None

    @staticmethod
    def _get_go_work(go: Go, run_params: dict[str, Any]) -> str:
        return go(["work", "edit", "-json"], run_params)

    @staticmethod
    def _get_go_work_path(app_dir: RootedPath) -> Optional[RootedPath]:
        go_work_file = Go()(["env", "GOWORK"], {"cwd": app_dir}).rstrip()

        # workspaces can be disabled explicitly with GOWORK=off
        if not go_work_file or go_work_file == "off":
            return None

        # make sure that the path to go.work is within the request's root
        return app_dir.join_within_root(go_work_file)

    @property
    def path(self) -> Optional[RootedPath]:
        """Return the go.work file path."""
        return self._path

    @cached_property
    def dir(self) -> Optional[RootedPath]:
        """Return the base directory for the go.work file."""
        if self._path is None:
            return None

        return RootedPath(self._app_dir.root).join_within_root(self._path.subpath_from_root.parent)

    def _parse(self, go: Go, run_params: dict[str, Any] = {}) -> "Self":
        """Actually parse the go.work file and fill in the instance with returned data."""
        # NOTE: This is only a temporary solution. This method is to be merged to __init__. We
        # can't do that just yet because this is being called from fetch_gomod_source which is
        # before we set up the correct Go toolchains. We don't need toolchains to query the GOWORK
        # env variable, but we need correct toolchain for everything else, otherwise go might
        # complain about not meeting the required versions, so make this effectively a "lazy"
        # evaluation driven by the caller.
        if self.data or self._path is None:
            return self

        go_work_json = self._get_go_work(go, run_params)
        self.data = ParsedGoWork.model_validate_json(go_work_json).model_dump()
        return self

    def workspace_paths(self, go: Go, run_params: dict[str, Any] = {}) -> Iterable[RootedPath]:
        """Get a list of paths to all workspace modules.

        :return:RootedPath instance iterable where root is go.work's containing directory
        """
        if not self.data:
            self._parse(go, run_params)

        if self._path is None or self.get("use", []) == []:
            return []

        # This re-root is going to be useful when constructing workspace ParsedModule.
        # mypy doesn't see that self.dir is directly connected to self._path which we checked
        go_work_dir_reroot = RootedPath(self.dir.path)  # type: ignore
        return (go_work_dir_reroot.join_within_root(p["disk_path"]) for p in self["use"])


ModuleID = tuple[str, str]


def _get_module_id(module: ParsedModule) -> ModuleID:
    """Identify a ParsedModule by its name and version/filepath.

    The main module, which doesn't have a version in its ParsedModule representation,
    gets the "." filepath.

    Note: if two IDs (include a filepath and) differ only by filepath, they may in fact identify
    the same module - different relative paths but the same absolute path. IDs that include
    a filepath are not universally unique, only locally unique within the dependencies of a main
    module.
    """
    if not (replace := module.replace):
        name = module.path
        version_or_path = module.version or "."
    elif replace.version:
        # module/name v1.0.0 => replace/name v1.2.3
        name = replace.path
        version_or_path = replace.version
    else:
        # module/name v1.0.0 => ./local/path
        name = module.path
        version_or_path = replace.path

    return name, version_or_path


def _create_modules_from_parsed_data(
    main_module: Module,
    main_module_dir: RootedPath,
    parsed_modules: Iterable[ParsedModule],
    modules_in_go_sum: frozenset[ModuleID],
    version_resolver: "ModuleVersionResolver",
    go_work: GoWork,
) -> list[Module]:
    def _create_module(module: ParsedModule) -> Module:
        mod_id = _get_module_id(module)
        name, version_or_path = mod_id
        original_name = module.path
        missing_hash_in_file = None

        if not version_or_path.startswith("."):
            version = version_or_path
            real_path = name

            if mod_id not in modules_in_go_sum:
                if go_work:
                    # __bool__ checks go_work.dir, so it can't be None
                    missing_hash_in_file = go_work.dir.subpath_from_root / "go.work.sum"  # type: ignore
                else:
                    missing_hash_in_file = main_module_dir.subpath_from_root / "go.sum"

                log.warning("checksum not found in %s: %s@%s", missing_hash_in_file, name, version)
        else:
            # module/name v1.0.0 => ./local/path
            resolved_replacement_path = main_module_dir.join_within_root(version_or_path)
            version = version_resolver.get_golang_version(module.path, resolved_replacement_path)
            real_path = _resolve_path_for_local_replacement(module)

        return Module(
            name=name,
            version=version,
            original_name=original_name,
            real_path=real_path,
            missing_hash_in_file=missing_hash_in_file,
        )

    def _resolve_path_for_local_replacement(module: ParsedModule) -> str:
        """Resolve all instances of "." and ".." for a local replacement."""
        if not module.replace:
            # Should not happen, this function will only be called for replaced modules
            raise RuntimeError("Can't resolve path for a module that was not replaced")

        path = f"{main_module.real_path}/{module.replace.path}"

        platform_specific_path = os.path.normpath(path)
        return Path(platform_specific_path).as_posix()

    return [_create_module(module) for module in parsed_modules]


def _create_packages_from_parsed_data(
    modules: list[Module], parsed_packages: Iterable[ParsedPackage]
) -> list[Union[Package, StandardPackage]]:
    # in case of replacements, the packages still refer to their parent module by its original name
    indexed_modules = {module.original_name: module for module in modules}

    def _create_package(package: ParsedPackage) -> Union[Package, StandardPackage]:
        if package.standard:
            return StandardPackage(name=package.import_path)

        if package.module is None:
            module = _find_parent_module_by_name(package)
        else:
            module = indexed_modules[package.module.path]

        relative_path = _resolve_package_relative_path(package, module)

        return Package(relative_path=str(relative_path), module=module)

    def _find_parent_module_by_name(package: ParsedPackage) -> Module:
        """Return the longest module name that is contained in package's import_path."""
        path = Path(package.import_path)

        matched_name = max(
            filter(path.is_relative_to, indexed_modules.keys()),
            key=len,  # type: ignore
            default=None,
        )

        if not matched_name:
            # This should be impossible
            raise RuntimeError("Package parent module was not found")

        return indexed_modules[matched_name]

    def _resolve_package_relative_path(package: ParsedPackage, module: Module) -> str:
        """Return the path for a package relative to its parent module original name."""
        relative_path = Path(package.import_path).relative_to(module.original_name)
        return str(relative_path).removeprefix(".")

    return [_create_package(package) for package in parsed_packages]


def fetch_gomod_source(request: Request) -> RequestOutput:
    """
    Resolve and fetch gomod dependencies for a given request.

    :param request: the request to process
    :raises PackageRejected: if a file is not present for the gomod package manager
    :raises PackageManagerError: if failed to fetch gomod dependencies
    """
    config = get_config()
    subpaths = [str(package.path) for package in request.gomod_packages]

    if not subpaths:
        return RequestOutput.empty()

    invalid_gomod_files = _find_missing_gomod_files(request.source_dir, subpaths)

    if invalid_gomod_files:
        invalid_files_print = "; ".join(str(file.parent) for file in invalid_gomod_files)

        raise PackageRejected(
            f"The go.mod file must be present for the Go module(s) at: {invalid_files_print}",
            solution="Please double-check that you have specified correct paths to your Go modules",
            docs=GOMOD_INPUT_DOC,
        )

    env_vars = {
        "GOCACHE": "${output_dir}/deps/gomod",
        "GOPATH": "${output_dir}/deps/gomod",
        "GOMODCACHE": "${output_dir}/deps/gomod/pkg/mod",
        "GOPROXY": "file://${GOMODCACHE}/cache/download",
    }
    env_vars.update(config.default_environment_variables.get("gomod", {}))

    components: list[Component] = []

    repo_name = _get_repository_name(request.source_dir)
    version_resolver = ModuleVersionResolver.from_repo_path(request.source_dir)

    with GoCacheTemporaryDirectory(prefix=f"{APP_NAME}-") as tmp_dir:
        gomod_download_dir = request.output_dir.join_within_root(
            "deps/gomod/pkg/mod/cache/download"
        )
        gomod_download_dir.path.mkdir(exist_ok=True, parents=True)
        for subpath in subpaths:
            log.info("Fetching the gomod dependencies at subpath %s", subpath)

            main_module_dir = request.source_dir.join_within_root(subpath)
            go_work = GoWork(main_module_dir)

            try:
                resolve_result = _resolve_gomod(
                    main_module_dir, request, Path(tmp_dir), version_resolver, go_work
                )
            except PackageManagerError:
                log.error("Failed to fetch gomod dependencies")
                raise

            main_module = _create_main_module_from_parsed_data(
                main_module_dir, repo_name, resolve_result.parsed_main_module
            )

            modules = [main_module]
            modules.extend(
                _create_modules_from_parsed_data(
                    main_module,
                    main_module_dir,
                    resolve_result.parsed_modules,
                    resolve_result.modules_in_go_sum,
                    version_resolver,
                    go_work,
                )
            )

            packages = _create_packages_from_parsed_data(modules, resolve_result.parsed_packages)

            components.extend(module.to_component() for module in modules)
            components.extend(package.to_component() for package in packages)

        tmp_download_cache_dir = Path(tmp_dir).joinpath("pkg/mod/cache/download")
        if tmp_download_cache_dir.exists():
            log.debug(
                "Adding dependencies from %s to %s",
                tmp_download_cache_dir,
                gomod_download_dir,
            )
            shutil.copytree(
                tmp_download_cache_dir,
                str(gomod_download_dir),
                dirs_exist_ok=True,
            )

    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=[
            EnvironmentVariable(name=key, value=value) for key, value in env_vars.items()
        ],
        project_files=[],
    )


def _create_main_module_from_parsed_data(
    main_module_dir: RootedPath, repo_name: str, parsed_main_module: ParsedModule
) -> Module:
    resolved_subpath = main_module_dir.subpath_from_root

    if str(resolved_subpath) == ".":
        resolved_path = repo_name
    else:
        resolved_path = f"{repo_name}/{resolved_subpath}"

    if not parsed_main_module.version:
        # Should not happen, since the version is always resolved from the Git repo
        raise RuntimeError(f"Version was not identified for main module at {resolved_subpath}")

    return Module(
        name=parsed_main_module.path,
        original_name=parsed_main_module.path,
        version=parsed_main_module.version,
        real_path=resolved_path,
    )


def _get_repository_name(source_dir: RootedPath) -> str:
    """Return the name resolved from the Git origin URL.

    The name is a treated form of the URL, after stripping the scheme, user and .git extension.
    """
    url = get_repo_id(source_dir).parsed_origin_url
    return f"{url.hostname}{url.path.rstrip('/').removesuffix('.git')}"


# NOTE: get rid of this go.mod parser once we can assume Go > 1.21 (1.20 can't parse micro release)
def _get_gomod_version(go_mod_file: RootedPath) -> tuple[Optional[str], Optional[str]]:
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


def _protect_against_symlinks(app_dir: RootedPath) -> None:
    """Try to prevent go subcommands from following suspicious symlinks.

    The go command doesn't particularly care if the files it reads are subpaths of the directory
    where it is executed. Check some of the common paths that the subcommands may read.

    :raises PathOutsideRoot: if go.mod, go.sum, vendor/modules.txt or any **/*.go file is a symlink
        that leads outside the source directory
    """

    def check_potential_symlink(relative_path: Union[str, Path]) -> None:
        app_dir.join_within_root(relative_path)

    # we purposefully skip checking go.work here because it is being checked elsewhere

    go_control_files = ["go.mod", "go.sum", "vendor/modules.txt"]
    go_sources_paths = [fp.relative_to(app_dir) for fp in app_dir.path.rglob("*.go")]

    # mypy doesn't see the object type from chain can only be a str or a Path and reports an error
    for p in chain(go_control_files, go_sources_paths):
        check_potential_symlink(p)  # type: ignore


def _find_missing_gomod_files(source_path: RootedPath, subpaths: list[str]) -> list[Path]:
    """
    Find all go modules with missing gomod files.

    These files will need to be present in order for the package manager to proceed with
    fetching the package sources.

    :param RequestBundleDir bundle_dir: the ``RequestBundleDir`` object for the request
    :param list subpaths: a list of subpaths in the source repository of gomod packages
    :return: a list containing all non-existing go.mod files across subpaths
    :rtype: list
    """
    invalid_gomod_files = []
    for subpath in subpaths:
        package_gomod_path = source_path.join_within_root(subpath, "go.mod").path
        log.debug(f"Testing for go mod file in {package_gomod_path}")
        if not package_gomod_path.exists():
            invalid_gomod_files.append(package_gomod_path)

    return invalid_gomod_files


def _setup_go_toolchain(go_mod_file: RootedPath) -> Go:
    GO_121 = version.Version("1.21")
    go = Go()
    target_version = None
    go_max_version = version.Version("1.25")
    go_base_version = go.version
    go_mod_version_msg = "go.mod reported versions: '%s'[go], '%s'[toolchain]"

    go_version_str, toolchain_version_str = _get_gomod_version(go_mod_file)
    log.info(
        go_mod_version_msg,
        go_version_str if go_version_str else "-",
        toolchain_version_str if toolchain_version_str else "-",
    )

    if not go_version_str:
        # Go added the 'go' directive to go.mod in 1.12 [1]. If missing, 1.16 is assumed [2].
        # For our version comparison purposes we set the version explicitly to 1.20 if missing.
        # [1] https://go.dev/doc/go1.12#modules
        # [2] https://go.dev/ref/mod#go-mod-file-go
        go_version_str = "1.20"
        log.debug("Could not parse Go version from go.mod, using %s as fallback", go_version_str)

    if not toolchain_version_str:
        toolchain_version_str = go_version_str

    go_mod_version = version.Version(go_version_str)
    go_mod_toolchain_version = version.Version(toolchain_version_str)

    if go_mod_version >= go_mod_toolchain_version:
        target_version = go_mod_version
    else:
        target_version = go_mod_toolchain_version

    if target_version.major > go_max_version.major or target_version.minor > go_max_version.minor:
        raise PackageManagerError(
            f"Required/recommended Go toolchain version '{target_version}' is not supported yet.",
            solution=(
                "Please lower your required/recommended Go version and retry the request. "
                "You may also want to open a feature request on adding support for this version."
            ),
        )

    if target_version >= GO_121:
        # Project makes use of Go >=1.21:
        # - always use the 'X.Y.0' toolchain to make sure GOTOOLCHAIN=auto fetches anything newer
        # - container environments need to have it pre-installed
        # - local environments will always install 1.21.0 SDK and then pull any newer toolchain
        go = Go(release="go1.21.0")
    elif go_base_version >= GO_121:
        # Starting with Go 1.21, Go doesn't try to be semantically backwards compatible in that the
        # 'go X.Y' line now denotes the minimum required version of Go, no a "suggested" version.
        # What it means in practice is that a Go toolchain >= 1.21 enforces the biggest common
        # toolchain denominator across all dependencies and so if the input project specifies e.g.
        # 'go 1.19' and **any** of its dependencies specify 'go 1.21' (or higher), then the default
        # 1.21 toolchain will bump the input project's go.mod file to make sure the minimum
        # required Go version is met across all dependencies. That is a problem, because it'll lead
        # to fatal build failures forcing everyone to update their build recipes. Note that at some
        # point they'll have to do that anyway, but until majority of projects in the ecosystem
        # adopt 1.21, we need a fallback to an older toolchain version.
        go = Go(release="go1.20")
    return go


def _disable_telemetry(go: Go, run_params: dict[str, Any]) -> None:
    telemetry = go(["env", "GOTELEMETRY"], run_params).rstrip()
    if telemetry and telemetry != "off":
        log.debug("Disabling Go telemetry")
        go(["telemetry", "off"], run_params)


def _go_list_deps(
    go: Go, pattern: Literal["./...", "all"], run_params: Optional[dict[str, Any]] = None
) -> Iterator[ParsedPackage]:
    """Run go list -deps -json and return the parsed list of packages.

    The "./..." pattern returns the list of packages compiled into the final binary.

    The "all" pattern includes dependencies needed only for tests. Use it to get a more
    complete module list (roughly matching the list of downloaded modules).
    """
    cmd = ["list", "-e", "-deps", "-json=ImportPath,Module,Standard,Deps", pattern]
    return map(
        ParsedPackage.model_validate,
        load_json_stream(go(cmd, run_params)),
    )


def _parse_packages(go_work: GoWork, go: Go, run_params: dict[str, Any]) -> Iterator[ParsedPackage]:
    """Return all Go packages for the project.

    Query the packages from the root of the project. If the project uses Go workspaces (1.18+) we
    additionally need to execute the query from every workspace module because 'go list' command
    isn't workspace aware and doesn't return all results if run just from the project root.

    :param go_work: GoWork instance wrapping the go.work file
    :param go: Go executable wrapper instance
    :param run_params: Additional run cmd params
    :return: ParsedPackage iterator
    """
    all_packages: Iterable[ParsedPackage] = []

    if not go_work:
        log.debug("Querying for list of packages")
        all_packages = _go_list_deps(go, "./...", run_params)
    else:
        # If there are workspace modules we need to run 'list -e ./...' under every local module
        # path because 'go list' command isn't fully properly workspace context aware
        for wsp in go_work.workspace_paths(go, run_params):
            log.debug(f"Querying workspace module '{wsp.path}' for list of packages")

            packages = list(_go_list_deps(go, "./...", run_params | {"cwd": wsp.path}))
            log.debug(packages)
            all_packages = chain(all_packages, packages)
    return iter(all_packages)


def _resolve_gomod(
    app_dir: RootedPath,
    request: Request,
    tmp_dir: Path,
    version_resolver: "ModuleVersionResolver",
    go_work: GoWork,
) -> ResolvedGoModule:
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param app_dir: the full path to the application source code
    :param request: app request this is for
    :param tmp_dir: one temporary directory for all go modules
    :return: a dict containing the Go module itself ("module" key), the list of dictionaries
        representing the dependencies ("module_deps" key), the top package level dependency
        ("pkg" key), and a list of dictionaries representing the package level dependencies
        ("pkg_deps" key)
    :raises PackageManagerError: if fetching dependencies fails
    """
    _protect_against_symlinks(app_dir)

    config = get_config()

    should_vendor = app_dir.join_within_root("vendor").path.is_dir()

    if should_vendor:
        # Even though we do not perform a "go mod download" when vendoring is detected, some
        # go commands still download dependencies as a side effect. Since we don't want those
        # copied to the output dir, we need to set the GOMODCACHE to a different directory.
        gomod_cache = f"{tmp_dir}/vendor-cache"
    else:
        gomod_cache = f"{tmp_dir}/pkg/mod"

    env = {
        "GOPATH": tmp_dir,
        "GO111MODULE": "on",
        "GOCACHE": tmp_dir,
        "PATH": os.environ.get("PATH", ""),
        "GOMODCACHE": gomod_cache,
        "GOSUMDB": "sum.golang.org",
        "GOTOOLCHAIN": "auto",
    }

    if config.goproxy_url:
        env["GOPROXY"] = config.goproxy_url

    if "cgo-disable" in request.flags:
        env["CGO_ENABLED"] = "0"

    go = _setup_go_toolchain(app_dir.join_within_root("go.mod"))
    log.info(f"Using Go release: {go.release}")

    run_params = {"env": env, "cwd": app_dir}

    # Explicitly disable toolchain telemetry for go >= 1.23
    _disable_telemetry(go, run_params)

    if go_work:
        modules_in_go_sum = _parse_go_sum_from_workspaces(go_work, go, run_params)
    else:
        modules_in_go_sum = _parse_go_sum(app_dir.join_within_root("go.sum"))

    # Vendor dependencies if the gomod-vendor flag is set
    if should_vendor:
        downloaded_modules = _vendor_deps(go, app_dir, bool(go_work), request.mode, run_params)
    else:
        log.info("Downloading the gomod dependencies")
        downloaded_modules = (
            ParsedModule.model_validate(obj)
            for obj in load_json_stream(go(["mod", "download", "-json"], run_params, retry=True))
        )

    main_module, workspace_modules = _parse_local_modules(
        go_work, go, run_params, app_dir, version_resolver
    )

    deps = _go_list_deps(go, "all", run_params)
    package_modules = [pkg.module for pkg in deps if pkg.module and not pkg.module.main]
    package_modules.extend(workspace_modules)
    all_modules = _deduplicate_resolved_modules(package_modules, downloaded_modules)
    _validate_local_replacements(all_modules, app_dir)

    log.info("Retrieving the list of packages")
    all_packages = _parse_packages(go_work, go, run_params)

    return ResolvedGoModule(main_module, all_modules, all_packages, modules_in_go_sum)


def _parse_local_modules(
    go_work: GoWork,
    go: Go,
    run_params: dict[str, Any],
    app_dir: RootedPath,
    version_resolver: "ModuleVersionResolver",
) -> tuple[ParsedModule, list[ParsedModule]]:
    """
    Identify and parse the main module and all workspace modules, if they exist.

    :return: A tuple containing the main module and a list of workspaces
    """
    modules_json_stream = go(["list", "-e", "-m", "-json"], run_params).rstrip()
    main_module_dict, workspace_dict_list = _process_modules_json_stream(
        app_dir, modules_json_stream
    )

    main_module_path = main_module_dict["Path"]
    main_module_version = version_resolver.get_golang_version(main_module_path, app_dir)

    main_module = ParsedModule(
        path=main_module_path,
        version=main_module_version,
        main=True,
    )

    workspace_modules = [
        _parse_workspace_module(go_work, ws, go, run_params) for ws in workspace_dict_list
    ]
    return main_module, workspace_modules


def _process_modules_json_stream(
    app_dir: RootedPath, modules_json_stream: str
) -> tuple[ModuleDict, list[ModuleDict]]:
    """Process the json stream returned by "go list -m -json".

    The stream will contain the module currently being processed, or a list of all workspaces in
    case a go.work file is present in the repository.

    :param app_dir: the path to the module directory
    :param modules_json_stream: the json stream returned by "go list -m -json"
    :return: A tuple containing the main module and a list of workspaces
    """
    module_list = []
    main_module = None

    for module in load_json_stream(modules_json_stream):
        if module["Dir"] == str(app_dir):
            main_module = module
        else:
            module_list.append(module)

    # should never happen, since the main module will always be a part of the json stream
    if not main_module:
        raise RuntimeError('Failed to find the main module info in the "go list -m -json" output.')

    return main_module, module_list


def _parse_workspace_module(
    go_work: GoWork, module: ModuleDict, go: Go, run_params: dict[str, Any] = {}
) -> ParsedModule:
    """Create a ParsedModule from a listed workspace.

    The replacement info returned will always be relative to the go.work file path.
    """
    # there's only ever going to be a single match
    ws_rootedpath = None
    for wsp_rooted in go_work.workspace_paths(go, run_params):
        if str(wsp_rooted.path) == module["Dir"]:
            ws_rootedpath = wsp_rooted
            break
    else:
        # This should be impossible
        raise RuntimeError(f"Failed to match a module based on '{module['Dir']}'")

    return ParsedModule(
        path=module["Path"],
        replace=ParsedModule(path=f"./{ws_rootedpath.subpath_from_root}"),
    )


def _parse_go_sum_from_workspaces(
    go_work: GoWork,
    go: Go,
    run_params: dict[str, Any],
) -> frozenset[ModuleID]:
    """Return the set of modules present in all go.sum files across the existing workspaces."""
    go_sum_files = _get_go_sum_files(go_work, go, run_params)

    modules: frozenset[ModuleID] = frozenset()

    for go_sum_file in go_sum_files:
        modules = modules | _parse_go_sum(go_sum_file)

    return modules


def _get_go_sum_files(
    go_work: GoWork,
    go: Go,
    run_params: dict[str, Any],
) -> list[RootedPath]:
    """Find all go.sum files present in the related workspaces."""
    workspace_paths = go_work.workspace_paths(go, run_params)

    # mypy doesn't see that go_work is true here and true means .path and .dir are set
    go_sums = [go_work.dir.join_within_root(wp.path / "go.sum") for wp in workspace_paths]  # type: ignore
    go_sums.append(go_work.dir.join_within_root("go.work.sum"))  # type: ignore

    return go_sums


def _parse_go_sum(go_sum: RootedPath) -> frozenset[ModuleID]:
    """Return the set of modules present in the specified go.sum file.

    A module is considered present if the checksum for its .zip file is present. The go.mod file
    checksums are not relevant for our purposes.
    """
    if not go_sum.path.exists():
        return frozenset()

    modules: list[ModuleID] = []

    # https://github.com/golang/go/blob/d5c5808534f0ad97333b1fd5fff81998f44986fe/src/cmd/go/internal/modfetch/fetch.go#L507-L534
    lines = go_sum.path.read_text().splitlines()
    for i, go_sum_line in enumerate(lines):
        parts = go_sum_line.split()
        if not parts:
            continue
        if len(parts) != 3:
            # https://github.com/golang/go/issues/62345
            # replicate the bug here, because it means that go only uses the non-broken part
            #   of go.sum for checksum verification
            log.warning(
                "%s:%d: malformed line, skipping the rest of the file: %r",
                go_sum.subpath_from_root,
                i + 1,
                go_sum_line,
            )
            break

        name, version, _ = parts
        if Path(version).name == "go.mod":
            continue

        modules.append((name, version))

    return frozenset(modules)


def _deduplicate_resolved_modules(
    package_modules: Iterable[ParsedModule],
    downloaded_modules: Iterable[ParsedModule],
) -> Iterable[ParsedModule]:
    modules_by_name_and_version: dict[ModuleID, ParsedModule] = {}

    # package_modules have the replace data, so they should take precedence in the deduplication
    for module in chain(package_modules, downloaded_modules):
        # get the module for this name+version or create a new one
        modules_by_name_and_version.setdefault(_get_module_id(module), module)

    return modules_by_name_and_version.values()


class GoCacheTemporaryDirectory(tempfile.TemporaryDirectory[str]):
    """
    A wrapper around the TemporaryDirectory context manager to also run `go clean -modcache`.

    The files in the Go cache are read-only by default and cause the default clean up behavior of
    tempfile.TemporaryDirectory to fail with a permission error. A way around this is to run
    `go clean -modcache` before the default clean up behavior is run.
    """

    def __exit__(
        self,
        exc: Optional[type[BaseException]],
        value: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        """Clean up the temporary directory by first cleaning up the Go cache."""
        try:
            Go()(["clean", "-modcache"], {"env": {"GOPATH": self.name, "GOCACHE": self.name}})
        finally:
            super().__exit__(exc, value, tb)


class ModuleVersionResolver:
    """Resolves the versions of Go modules in a git repository."""

    def __init__(self, repo: git.Repo, commit: git.objects.commit.Commit):
        """Initialize a ModuleVersionResolver for the provided Repo."""
        self._repo = repo
        self._commit = commit

    @classmethod
    def from_repo_path(cls, repo_path: RootedPath) -> "Self":
        """Fetch tags from a git Repo and return a ModuleVersionResolver."""
        repo = git.Repo(repo_path)
        commit = repo.commit(repo.rev_parse("HEAD").hexsha)
        try:
            repo.remote().fetch(force=True, tags=True)
        except Exception as ex:
            raise FetchError(
                f"Failed to fetch the tags on the Git repository ({type(ex).__name__}) "
                f"for {repo.working_tree_dir}: "
                f"{str(ex)}"
            )

        return cls(repo, commit)

    @cached_property
    def _commit_tags(self) -> list[str]:
        """Return the git tags pointing to the current commit."""
        return self._get_commit_tags()

    @cached_property
    def _all_tags(self) -> list[str]:
        """Return all of the git tags pointing to the current and preceding commits."""
        return self._get_commit_tags(all_reachable=True)

    def _get_commit_tags(self, all_reachable: bool = False) -> list[str]:
        """
        Return all of the tags associated with the current commit.

        :param all_reachable: True to get all tags on the current commit and all commits preceding
                              it. False to get the tags on the current commit only.
        :return: a list of tag names
        :raises GitCommandError: if failed to fetch the tags on the Git repository
        """
        try:
            if all_reachable:
                # Get all the tags on the input commit and all that precede it.
                # This is based on:
                # https://github.com/golang/go/blob/0ac8739ad5394c3fe0420cf53232954fefb2418f/src/cmd/go/internal/modfetch/codehost/git.go#L659-L695
                cmd = [
                    "git",
                    "for-each-ref",
                    "--format",
                    "%(refname:lstrip=2)",
                    "refs/tags",
                    "--merged",
                    self._commit.hexsha,
                ]
            else:
                # Get the tags that point to this commit
                cmd = ["git", "tag", "--points-at", self._commit.hexsha]

            tag_names = self._repo.git.execute(
                cmd,
                # these args are the defaults, but are required to let mypy know which override to match
                # (the one that returns a string)
                with_extended_output=False,
                as_process=False,
                stdout_as_string=True,
            ).splitlines()
        except git.GitCommandError:
            msg = f"Failed to get the tags associated with the reference {self._commit.hexsha}"
            log.error(msg)
            raise

        return tag_names

    def get_golang_version(
        self,
        module_name: str,
        app_dir: RootedPath,
    ) -> str:
        """
        Get the version of the Go module in the input Git repository in the same format as `go list`.

        If commit doesn't point to a commit with a semantically versioned tag, a pseudo-version
        will be returned.

        :param module_name: the Go module's name
        :param app_dir: the path to the module directory
        :return: a version as `go list` would provide
        """
        # If the module is version v2 or higher, the major version of the module is included as /vN at
        # the end of the module path. If the module is version v0 or v1, the major version is omitted
        # from the module path.
        match = re.match(r"(?:.+/v)(?P<major_version>\d+)$", module_name)
        module_major_version = int(match.group("major_version")) if match else None

        # If no match, prefer v1.x.x tags but fallback to v0.x.x tags if both are present
        major_versions_to_try = (module_major_version,) if module_major_version else (1, 0)

        if app_dir.path == app_dir.root:
            subpath = None
        else:
            subpath = app_dir.path.relative_to(app_dir.root).as_posix()

        tag_on_commit = self._get_highest_semver_tag_on_current_commit(
            major_versions_to_try, subpath
        )
        if tag_on_commit:
            return tag_on_commit

        log.debug("No semantic version tag was found on the commit %s", self._commit.hexsha)
        pseudo_version = self._get_highest_reachable_semver_tag(major_versions_to_try, subpath)
        if pseudo_version:
            return pseudo_version

        log.debug("No valid semantic version tag was found")
        # Fall-back to a vX.0.0-yyyymmddhhmmss-abcdefabcdef pseudo-version
        return self._get_golang_pseudo_version(
            module_major_version=module_major_version, subpath=subpath
        )

    def _get_highest_semver_tag_on_current_commit(
        self, major_versions_to_try: tuple[int, ...], subpath: Optional[str]
    ) -> Optional[str]:
        """Return the highest semver tag on the current commit."""
        for major_version in major_versions_to_try:
            # Get the highest semantic version tag on the commit with a matching major version
            tag_on_commit = self._get_highest_semver_tag(major_version, subpath=subpath)
            if not tag_on_commit:
                continue

            log.debug(
                "Using the semantic version tag of %s for commit %s",
                tag_on_commit.name,
                self._commit.hexsha,
            )

            # We want to preserve the version in the "v0.0.0" format, so the subpath is not needed
            return (
                tag_on_commit.name if not subpath else tag_on_commit.name.replace(f"{subpath}/", "")
            )

        return None

    def _get_highest_reachable_semver_tag(
        self, major_versions_to_try: tuple[int, ...], subpath: Optional[str]
    ) -> Optional[str]:
        """Return the pseudo-version using the highest reachable semver tag as a base."""
        # This logic is based on:
        # https://github.com/golang/go/blob/a23f9afd9899160b525dbc10d01045d9a3f072a0/src/cmd/go/internal/modfetch/coderepo.go#L511-L521
        for major_version in major_versions_to_try:
            # Get the highest semantic version tag before the commit with a matching major version
            pseudo_base_tag = self._get_highest_semver_tag(
                major_version, all_reachable=True, subpath=subpath
            )
            if not pseudo_base_tag:
                continue

            log.debug(
                "Using the semantic version tag of %s as the pseudo-base for the commit %s",
                pseudo_base_tag.name,
                self._commit.hexsha,
            )
            pseudo_version = self._get_golang_pseudo_version(
                pseudo_base_tag, major_version, subpath=subpath
            )
            log.debug(
                "Using the pseudo-version %s for the commit %s", pseudo_version, self._commit.hexsha
            )
            return pseudo_version

        return None

    def _get_highest_semver_tag(
        self,
        major_version: int,
        all_reachable: bool = False,
        subpath: Optional[str] = None,
    ) -> Optional[git.Tag]:
        """
        Get the highest semantic version tag related to the input commit.

        :param major_version: the major version of the Go module as in the go.mod file to use as a
            filter for major version tags
        :param all_reachable: if False, the search is constrained to the input commit. If True,
            then the search is constrained to the input commit and preceding commits.
        :param subpath: path to the module, relative to the root repository folder
        :return: the highest semantic version tag if one is found
        """
        tag_names = self._all_tags if all_reachable else self._commit_tags

        # Keep only semantic version tags related to the path being processed
        prefix = f"{subpath}/v" if subpath else "v"
        filtered_tags = [tag_name for tag_name in tag_names if tag_name.startswith(prefix)]

        not_semver_tag_msg = "%s is not a semantic version tag"
        highest: Optional[dict[str, Any]] = None

        for tag_name in filtered_tags:
            try:
                semantic_version = self._get_semantic_version_from_tag(tag_name, subpath)
            except ValueError:
                log.debug(not_semver_tag_msg, tag_name)
                continue

            # If the major version of the semantic version tag doesn't match the Go module's major
            # version, then ignore it
            if semantic_version.major != major_version:
                continue

            if highest is None or semantic_version > highest["semver"]:
                highest = {"tag": tag_name, "semver": semantic_version}

        if highest:
            return self._repo.tags[highest["tag"]]

        return None

    def _get_golang_pseudo_version(
        self,
        tag: Optional[git.Tag] = None,
        module_major_version: Optional[int] = None,
        subpath: Optional[str] = None,
    ) -> str:
        """
        Get the Go module's pseudo-version when a non-version commit is used.

        For a description of the algorithm, see https://tip.golang.org/cmd/go/#hdr-Pseudo_versions.

        :param tag: the highest semantic version tag with a matching major version before the
            input commit. If this isn't specified, it is assumed there was no previous valid tag.
        :param module_major_version: the Go module's major version as stated in its go.mod file. If
            this and "tag" are not provided, 0 is assumed.
        :param subpath: path to the module, relative to the root repository folder
        :return: the Go module's pseudo-version as returned by `go list`
        :rtype: str
        """
        # Use this instead of commit.committed_datetime so that the datetime object is UTC
        committed_dt = datetime.fromtimestamp(self._commit.committed_date, timezone.utc)
        commit_timestamp = committed_dt.strftime(r"%Y%m%d%H%M%S")
        commit_hash = self._commit.hexsha[0:12]

        # vX.0.0-yyyymmddhhmmss-abcdefabcdef is used when there is no earlier versioned commit with an
        # appropriate major version before the target commit
        if tag is None:
            # If the major version isn't in the import path and there is not a versioned commit with the
            # version of 1, the major version defaults to 0.
            return f'v{module_major_version or "0"}.0.0-{commit_timestamp}-{commit_hash}'

        tag_semantic_version = self._get_semantic_version_from_tag(tag.name, subpath)

        # An example of a semantic version with a prerelease is v2.2.0-alpha
        if tag_semantic_version.prerelease:
            # vX.Y.Z-pre.0.yyyymmddhhmmss-abcdefabcdef is used when the most recent versioned commit
            # before the target commit is vX.Y.Z-pre
            version_seperator = "."
            pseudo_semantic_version = tag_semantic_version
        else:
            # vX.Y.(Z+1)-0.yyyymmddhhmmss-abcdefabcdef is used when the most recent versioned commit
            # before the target commit is vX.Y.Z
            version_seperator = "-"
            pseudo_semantic_version = tag_semantic_version.bump_patch()

        return f"v{pseudo_semantic_version}{version_seperator}0.{commit_timestamp}-{commit_hash}"

    @staticmethod
    def _get_semantic_version_from_tag(
        tag_name: str, subpath: Optional[str] = None
    ) -> semver.version.Version:
        """
        Parse a version tag to a semantic version.

        A Go version follows the format "v0.0.0", but it needs to have the "v" removed in
        order to be properly parsed by the semver library.

        In case `subpath` is defined, it will be removed from the tag_name, e.g. `subpath/v0.1.0`
        will be parsed as `0.1.0`.

        :param tag_name: tag to be converted into a semver object
        :param subpath: path to the module, relative to the root repository folder
        """
        if subpath:
            semantic_version = tag_name.replace(f"{subpath}/v", "")
        else:
            semantic_version = tag_name[1:]

        return semver.version.Version.parse(semantic_version)


def _validate_local_replacements(modules: Iterable[ParsedModule], app_path: RootedPath) -> None:
    replaced_paths = [
        (module.path, module.replace.path)
        for module in modules
        if module.replace and module.replace.path.startswith(".")
    ]

    for _, path in replaced_paths:
        app_path.join_within_root(path)


def _parse_vendor(context_dir: RootedPath) -> Iterable[ParsedModule]:
    """Parse modules from vendor/modules.txt."""
    modules_txt = context_dir.join_within_root("vendor", "modules.txt")
    if not modules_txt.path.exists():
        return []

    def fail_for_unexpected_format(msg: str) -> NoReturn:
        solution = (
            "Does `go mod vendor` make any changes to modules.txt?\n"
            f"If not, please let the maintainers know that {APP_NAME} fails to parse valid modules.txt"
        )
        raise UnexpectedFormat(f"vendor/modules.txt: {msg}", solution=solution)

    def parse_module_line(line: str) -> ParsedModule:
        parts = line.removeprefix("# ").split()
        # name version
        if len(parts) == 2:
            name, version = parts
            return ParsedModule(path=name, version=version)
        # name => path
        if len(parts) == 3 and parts[1] == "=>":
            name, _, path = parts
            return ParsedModule(path=name, replace=ParsedModule(path=path))
        # name => new_name new_version
        if len(parts) == 4 and parts[1] == "=>":
            name, _, new_name, new_version = parts
            return ParsedModule(path=name, replace=ParsedModule(path=new_name, version=new_version))
        # name version => path
        if len(parts) == 4 and parts[2] == "=>":
            name, version, _, path = parts
            return ParsedModule(path=name, version=version, replace=ParsedModule(path=path))
        # name version => new_name new_version
        if len(parts) == 5 and parts[2] == "=>":
            name, version, _, new_name, new_version = parts
            return ParsedModule(
                path=name,
                version=version,
                replace=ParsedModule(path=new_name, version=new_version),
            )
        fail_for_unexpected_format(f"unexpected module line format: {line!r}")

    modules: list[ParsedModule] = []
    module_has_packages: list[bool] = []

    for line in modules_txt.path.read_text().splitlines():
        if line.startswith("# "):  # module line
            modules.append(parse_module_line(line))
            module_has_packages.append(False)
        elif not line.startswith("#"):  # package line
            if not modules:
                fail_for_unexpected_format(f"package has no parent module: {line}")
            module_has_packages[-1] = True
        elif not line.startswith("##"):  # marker line
            fail_for_unexpected_format(f"unexpected format: {line!r}")

    return (module for module, has_packages in zip(modules, module_has_packages) if has_packages)


def _vendor_deps(
    go: Go,
    context_dir: RootedPath,
    has_workspace: bool,
    enforcing_mode: Mode,
    run_params: dict[str, Any],
) -> Iterable[ParsedModule]:
    """
    Vendor golang dependencies.

    Application checks the vendor directory for updated content, failing if Go'd be to make any
    changes.

    :param app_dir: path to the module directory
    :param run_params: common params for the subprocess calls to `go`
    :param has_workspace: whether we detected Go workspaces in the repo (affects @context_dir)
    :return: the list of Go modules parsed from vendor/modules.txt
    :raise PackageRejected: if vendor directory changed
    :raise UnexpectedFormat: if application fails to parse vendor/modules.txt
    """
    log.info("Vendoring the gomod dependencies")

    cmdscope = "work" if has_workspace else "mod"
    go([cmdscope, "vendor"], run_params)
    if _vendor_changed(context_dir, enforcing_mode):
        if enforcing_mode == Mode.STRICT:
            raise PackageRejected(
                reason=(
                    "The content of the vendor directory is not consistent with go.mod. "
                    "Please check the logs for more details."
                ),
                solution=(
                    "Please try running `go mod vendor` and committing the changes.\n"
                    "Note that you may need to `git add --force` ignored files in the vendor/ dir."
                ),
                docs=VENDORING_DOC,
            )
    return _parse_vendor(context_dir)


def _vendor_changed(context_dir: RootedPath, enforcing_mode: Mode) -> bool:
    """Check for changes in the vendor directory.

    :param context_dir: main module dir OR workspace context (directory containing go.work)
    """
    repo_root = context_dir.root

    # Get the correct repo context (main or submodule)
    repo, context_relative_path = get_repo_for_path(repo_root, context_dir.path)

    # Calculate vendor paths relative to the active repo
    vendor = context_relative_path / "vendor"
    modules_txt = vendor / "modules.txt"

    # Add untracked files but do not stage them
    repo.git.add("--intent-to-add", "--force", "--", context_relative_path)

    try:
        # Diffing modules.txt should catch most issues and produce relatively useful output
        modules_txt_diff = repo.git.diff("--", str(modules_txt), env=GIT_PRISTINE_ENV)
        if modules_txt_diff:
            log.error_or_warn(
                "%s changed after vendoring:\n%s",
                modules_txt,
                modules_txt_diff,
                enforcing_mode=enforcing_mode,
            )
            return True

        # Show only if files were added/deleted/modified, not the full diff
        vendor_diff = repo.git.diff("--name-status", "--", str(vendor), env=GIT_PRISTINE_ENV)
        if vendor_diff:
            log.error_or_warn(
                "%s directory changed after vendoring:\n%s",
                vendor,
                vendor_diff,
                enforcing_mode=enforcing_mode,
            )
            return True
    finally:
        repo.git.reset("--", context_relative_path)

    return False
