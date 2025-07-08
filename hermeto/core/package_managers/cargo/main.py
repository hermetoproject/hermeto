import logging
import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from itertools import chain
from pathlib import Path
from typing import Any, Optional

import tomlkit
from packageurl import PackageURL

from hermeto.core.errors import NotAGitRepo, PackageRejected
from hermeto.core.models.input import Mode, Request
from hermeto.core.models.output import Component, EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import get_repo_id
from hermeto.core.utils import run_cmd

log = logging.getLogger(__name__)


class PackageWithCorruptLockfileRejected(PackageRejected):
    """Package lock file does not match package config."""

    def __init__(self, package_path: str) -> None:
        """Initialize the error."""
        reason = (
            f"{package_path} contains a Cargo.lock that does not match"
            " the corresponding Cargo.toml"
        )
        super().__init__(reason, solution=self.default_solution, docs="")

    default_solution = (
        "Consider reaching out to maintainer of the dependency in question to address"
        " inconsistencies between Cargo.lock and Cargo.toml"
    )


@dataclass(frozen=True)
class CargoPackage:
    """CargoPackage."""

    name: str
    version: Optional[str] = None
    source: Optional[str] = None  # [git|registry]+https://github.com/<org>/<package>#[|<sha>]
    checksum: Optional[str] = None
    dependencies: Optional[list] = None
    vcs_url: Optional[str] = None

    @cached_property
    def purl(self) -> PackageURL:
        """Return corrsponding purl."""
        qualifiers = {}
        if self.source is not None:
            qualifiers["source"] = self.source
        # The condition below holds for either the main package or any packages
        # that originate from the filesystem (for example, workspace and patched source ones).
        if self.vcs_url is not None and self.source is None:
            qualifiers["vcs_url"] = self.vcs_url
        if self.checksum is not None:
            qualifiers["checksum"] = self.checksum
        return PackageURL(type="cargo", name=self.name, version=self.version, qualifiers=qualifiers)

    def to_component(self) -> Component:
        """Convert CargoPackage into SBOM component."""
        return Component(name=self.name, version=self.version, purl=self.purl.to_string())


def fetch_cargo_source(request: Request) -> RequestOutput:
    """Fetch the source code for all cargo packages specified in a request."""
    components: list[Component] = []
    environment_variables: list[EnvironmentVariable] = []
    project_files: list[ProjectFile] = []

    for package in request.cargo_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        _verify_lockfile_is_present_or_fail(package_dir)
        # cargo allows to specify configuration per-package
        # https://doc.rust-lang.org/cargo/reference/config.html#hierarchical-structure
        config_template = _fetch_dependencies(package_dir, request)
        project_files.append(_use_vendored_sources(package_dir, config_template))
        components.extend(_resolve_cargo_package(package_dir))

    return RequestOutput.from_obj_list(components, environment_variables, project_files)


def _fetch_dependencies(package_dir: RootedPath, request: Request) -> dict[str, Any]:
    vendor_dir = request.output_dir.join_within_root("deps/cargo")
    # --no-delete to keep everything already present. It does not matter for a fresh
    # single package, but it does matter when there is pip interaction.
    cmd = ["cargo", "vendor", "--locked", "--versioned-dirs", "--no-delete", str(vendor_dir)]
    log.info("Fetching cargo dependencies at %s", package_dir)
    with _hidden_cargo_config_file(package_dir):
        # stdout contains exact values to add to .cargo/config.toml for a build to become hermetic.
        config_template = _run_cmd_watching_out_for_lock_mismatch(
            cmd=cmd, params={"cwd": package_dir}, package_dir=package_dir, mode=request.mode
        )

    return _swap_sources_directory_for_subsitution_slot(config_template)


def _parse_toml_project_file(path: Path) -> dict[str, Any]:
    """Parse any Cargo related TOML file into a dictionary."""
    parsed_toml = tomlkit.parse(path.read_text())
    return parsed_toml.value


def _resolve_main_package(package_dir: RootedPath) -> tuple[str, Optional[str]]:
    """Resolve package name and version from Cargo.toml."""
    parsed_toml = _parse_toml_project_file(package_dir.path / "Cargo.toml")

    package_info = parsed_toml.get("package", {})
    workspace_info = parsed_toml.get("workspace", {})

    # use default values if the project is a virtual workspace without any package information
    name = package_info.get("name", package_dir.path.stem)
    version = package_info.get("version", None)

    # check for a workspace package version
    # https://doc.rust-lang.org/cargo/reference/workspaces.html#the-package-table
    if version is None:
        version = workspace_info.get("package", {}).get("version")

    return name, version


def _verify_lockfile_is_present_or_fail(package_dir: RootedPath) -> None:
    # Most packages will be locked, however metapackages (i.e. those, which
    # contain just a workspace and could even lack a name) could arrive without
    # a lock file. A user could try and fix this by explicitly locking the
    # package first.
    if not (package_dir.path / "Cargo.lock").exists():
        raise PackageRejected(
            f"{package_dir.path} is not locked",
            solution="Please lock it first by running 'cargo generate-lockfile",
        )


@contextmanager
def _hidden_cargo_config_file(package_dir: RootedPath) -> Generator[None, None, None]:
    """Hide the cargo config file if it exists.

    The file may contain various settings that could result in potential attack vectors.
    Therefore, it is better to "hide" it before running the `cargo vendor` command.
    """
    # There is a slim chance to find an old project with .cargo/config
    # instead of .cargo/config.toml. If found it has to be hidden too since it still
    # takes precedence over the now standard .cargo/config.toml
    # (https://doc.rust-lang.org/cargo/reference/config.html).
    # Note, that ordering matters here, since .cargo/config could be a symlink
    # to .cargo/config.toml for projects that are built with both old and new versions
    # of Cargo. Unlinking a symlink first is safe.
    all_possible_config_names = (".cargo/config", ".cargo/config.toml")
    configs_contents = []

    for cfgname in all_possible_config_names:
        config = package_dir.join_within_root(cfgname)
        data = config.path.read_text() if config.path.exists() else None
        configs_contents.append((config, data))
        if data is not None:
            config.path.unlink()

    try:
        yield
    finally:
        for config, data in configs_contents:
            if data is not None:
                config.path.write_text(data)


@contextmanager
def _temporary_cwd(path_to_new_cwd: Path) -> Generator[None, None, None]:
    oldcwd = os.getcwd()
    os.chdir(path_to_new_cwd)
    yield
    os.chdir(oldcwd)


def _run_cmd_watching_out_for_lock_mismatch(
    cmd: list, params: dict, package_dir: RootedPath, mode: Mode
) -> str:
    warn_about_imminent_update_to_cargo_lock = (
        f"A mismatch between Cargo.lock and Cargo.toml was detected in {package_dir}. "
        "Because of permissive mode Hermeto will now regenerate Cargo.lock "
        "to match expectation and will try to process the package again. This "
        f"is a violation of reproducibility and must be addressed by {package_dir.path.name} "
        "maintainers."
    )
    update_cargo_lock_cmd = ["cargo", "generate-lockfile"]
    try:
        return run_cmd(cmd=cmd, params=params, suppress_errors=(mode == Mode.PERMISSIVE))
    except subprocess.CalledProcessError as e:
        # Search for a very specific failure state to better report it.
        # This is not a robust solution in any way, however it seems to be the only one
        # readily available: cargo returns a generic 101 code on this failure and on multiple
        # others, thus the only way to check for this specific type of failure is to process
        # stderr. Two parts of a string are used to decrease the likelihood of false positives.
        lock_corruption_marker1 = "failed to sync"
        lock_corruption_marker2 = "needs to be updated but --locked was passed"
        if lock_corruption_marker1 in e.stderr and lock_corruption_marker2 in e.stderr:
            if mode == Mode.PERMISSIVE:
                log.warning(warn_about_imminent_update_to_cargo_lock)
                with _temporary_cwd(package_dir.path):
                    run_cmd(cmd=update_cargo_lock_cmd, params={})
                # If it fails here then something else is horribly broken.
                # No more attempts to salvage the situation will be made.
                return run_cmd(cmd=cmd, params=params)
            else:
                raise PackageWithCorruptLockfileRejected(f"{package_dir.path}")
        else:
            raise


def _resolve_cargo_package(package_dir: RootedPath) -> chain[Component]:
    """Resolve a single cargo package."""
    parsed_lockfile = _parse_toml_project_file(package_dir.path / "Cargo.lock")
    packages = parsed_lockfile.get("package", [])

    main_package_name, main_package_version = _resolve_main_package(package_dir)
    is_a_dep = lambda p: p["name"] != main_package_name
    try:
        vcs_url = get_repo_id(package_dir.root).as_vcs_url_qualifier()
    except NotAGitRepo:
        # Could become invalid when directories are swapped for nested package managers
        vcs_url = None
    deps_components = (
        CargoPackage(**p, vcs_url=vcs_url).to_component() for p in packages if is_a_dep(p)
    )
    main_component = CargoPackage(
        name=main_package_name, version=main_package_version, vcs_url=vcs_url
    ).to_component()

    components = chain((main_component,), deps_components)

    return components


def _swap_sources_directory_for_subsitution_slot(template: str) -> dict:
    toml_template = tomlkit.parse(template).value
    # Absolute path has to be replaced with relative path for sources relocation to work:
    toml_template["source"]["vendored-sources"]["directory"] = "${output_dir}/deps/cargo"
    # A correct output_dir value will be supplied by the application during a later stage.
    return toml_template


def _old_style_config_is_present_in(package_dir: RootedPath) -> bool:
    return (package_dir.path / ".cargo/config").exists()


def _use_vendored_sources(package_dir: RootedPath, config_template: dict) -> ProjectFile:
    """Make sure cargo will use the vendored sources when building the project."""
    # Cargo could be told to use vendored sources instead of a registry via .cargo/config.toml.
    # Prior to cargo v1.39.0 .cargo/config.toml was known as .cargo/config.
    # After v1.39.0 this name was considered obsolete, however .cargo/config would
    # take precedence on .cargo/config.toml if present and the latter one would be ignored.
    # The recommended practice for dealing with a situation when an older build system
    # has to build a more modern project is to symlink .cargo/config.toml to .cargo/config.
    # And vice versa: renaming .cargo/config to .cargo/config.toml would have no effect on
    # any post-2019 toolchain.
    # Refer to https://doc.rust-lang.org/cargo/reference/config.html for further details.
    # Since we could potentially end up building a somewhat stale Rust-based
    # Python extension it is better to check if there is an old-style config present and
    # process it if found.
    cfn = ".cargo/config" if _old_style_config_is_present_in(package_dir) else ".cargo/config.toml"
    config_path = package_dir.join_within_root(cfn).path

    original_content = _parse_toml_project_file(config_path) if config_path.exists() else {}
    original_content.update(config_template)
    return ProjectFile(abspath=config_path, template=tomlkit.dumps(config_template))
