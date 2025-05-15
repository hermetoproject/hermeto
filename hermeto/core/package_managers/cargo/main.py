import logging
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from itertools import chain
from pathlib import Path
from typing import Generator, Optional

import tomlkit
from packageurl import PackageURL
from tomlkit.toml_file import TOMLFile

from hermeto.core.errors import NotAGitRepo, PackageRejected
from hermeto.core.models.input import Request
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
    version: str
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
        # cargo allows to specify configuration per-package
        # https://doc.rust-lang.org/cargo/reference/config.html#hierarchical-structure
        fetched_components, cfg_template = _resolve_cargo_package(package_dir, request.output_dir)
        components.extend(fetched_components)
        project_files.append(_use_vendored_sources(package_dir, cfg_template))

    return RequestOutput.from_obj_list(components, environment_variables, project_files)


def _extract_package_info(path_to_toml: Path) -> dict:
    # 'value' unwraps the underlying dict and that makes mypy happy (it complains about
    # mismatching type otherwise despite parsed document having the necessary interface).
    parsed_toml = tomlkit.parse(path_to_toml.read_text())
    package_info = parsed_toml.value["package"]
    if isinstance(package_info, dict) and package_info.get("version") == {"workspace": True}:
        # support for workspace package table https://doc.rust-lang.org/cargo/reference/workspaces.html#the-package-table
        # real world example: https://github.com/pyca/cryptography/blob/43.0.0/src/rust/Cargo.toml
        # we condition this to when package_info is a dict because this same function has usages where package_info is a list
        # (Cargo.lock) and this info is only available when it is a dict (Cargo.toml)
        package_info["version"] = parsed_toml.value["workspace"]["package"]["version"]
    return package_info


def _resolve_main_package(package_dir: RootedPath) -> dict:
    try:
        return _extract_package_info(package_dir.path / "Cargo.toml")
    # We'll get here in the case of virtual workspaces. A real-world example is
    # https://github.com/rwf2/Rocket/tree/master
    # In this case there is no package name per se, but there still is a metapackage.
    # We'll use directory name as a fallback in this case.
    # Version won't make much sense here: this is a meta-package, the state of a
    # repository will be captured in VCS_URL, and individual components will be
    # versioned.
    except KeyError:
        return {
            "name": package_dir.path.stem,
            "version": None,
        }


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


def _resolve_cargo_package(
    package_dir: RootedPath,
    output_dir: RootedPath,
) -> tuple[chain[Component], dict]:
    """Resolve a single cargo package."""
    _verify_lockfile_is_present_or_fail(package_dir)
    vendor_dir = output_dir.join_within_root("deps/cargo")
    # --no-delete to keep everything already present. It does not matter for a fresh
    # single package, but it does matter when there is pip interaction.
    cmd = ["cargo", "vendor", "--locked", "--versioned-dirs", "--no-delete", str(vendor_dir)]
    log.info("Fetching cargo dependencies at %s", package_dir)
    with _hidden_cargo_config_file(package_dir):
        # stdout contains exact values to add to .cargo/config.toml for a build to become hermetic.
        try:
            config_template = run_cmd(cmd=cmd, params={"cwd": package_dir})
        except subprocess.CalledProcessError as e:
            # Search for a very specific failure state to better report it.
            # This is not a robust solution in any way, however it seems to be the only one
            # readily available: cargo returns a generic 101 code on this failure and on multiple
            # others, thus the only way to check for this specific type of failure is to process
            # stderr. Two parts of a string are used to decrease the likelihood of false positives.
            lock_corruption_marker1 = "failed to sync"
            lock_corruption_marker2 = "needs to be updated but --locked was passed"
            if lock_corruption_marker1 in e.stderr and lock_corruption_marker2 in e.stderr:
                raise PackageWithCorruptLockfileRejected(f"{package_dir.path}")
            else:
                raise

    packages = _extract_package_info(package_dir.path / "Cargo.lock")
    main_package = _resolve_main_package(package_dir)
    is_a_dep = lambda p: p["name"] != main_package["name"]
    try:
        vcs_url = get_repo_id(package_dir.root).as_vcs_url_qualifier()
    except NotAGitRepo:
        # Could become invalid when directories are swapped for nested package managers
        vcs_url = None
    deps_components = (
        CargoPackage(**p, vcs_url=vcs_url).to_component() for p in packages if is_a_dep(p)
    )
    main_component = CargoPackage(
        name=main_package["name"], version=main_package["version"], vcs_url=vcs_url
    ).to_component()

    components = chain((main_component,), deps_components)

    return components, _swap_sources_directory_for_subsitution_slot(config_template)


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
    cargo_config = package_dir.join_within_root(cfn)

    toml_file = TOMLFile(cargo_config)
    original_content = toml_file.read() if cargo_config.path.exists() else {}
    original_content.update(config_template)

    return ProjectFile(abspath=cargo_config.path, template=tomlkit.dumps(config_template))
