import logging
from dataclasses import dataclass
from functools import cached_property
from itertools import chain
from pathlib import Path
from typing import Optional

import tomlkit
from packageurl import PackageURL
from tomlkit.toml_file import TOMLFile

from cachi2.core.errors import PackageRejected
from cachi2.core.models.input import Request
from cachi2.core.models.output import Component, EnvironmentVariable, ProjectFile, RequestOutput
from cachi2.core.rooted_path import RootedPath
from cachi2.core.scm import get_repo_id
from cachi2.core.utils import run_cmd

log = logging.getLogger(__name__)


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
    return tomlkit.parse(path_to_toml.read_text()).value["package"]


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


def _resolve_cargo_package(
    package_dir: RootedPath,
    output_dir: RootedPath,
) -> tuple[chain[Component], dict]:
    """Resolve a single cargo package."""
    _verify_lockfile_is_present_or_fail(package_dir)
    vendor_dir = output_dir.join_within_root("deps/cargo")
    cmd = ["cargo", "vendor", "--locked", str(vendor_dir)]
    log.info("Fetching cargo dependencies at %s", package_dir)
    # stdout contains exact values to add to .cargo/config.toml for a build to become hermetic.
    config_template = run_cmd(cmd=cmd, params={"cwd": package_dir})

    packages = _extract_package_info(package_dir.path / "Cargo.lock")
    main_package = _resolve_main_package(package_dir)
    vcs_url = get_repo_id(package_dir.root).as_vcs_url_qualifier()
    is_a_dep = lambda p: p["name"] != main_package["name"]
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


def _use_vendored_sources(package_dir: RootedPath, config_template: dict) -> ProjectFile:
    """Make sure cargo will use the vendored sources when building the project."""
    cargo_config = package_dir.join_within_root(".cargo/config.toml")

    toml_file = TOMLFile(cargo_config)
    original_content = toml_file.read() if cargo_config.path.exists() else {}
    original_content.update(config_template)

    return ProjectFile(abspath=cargo_config.path, template=tomlkit.dumps(config_template))
