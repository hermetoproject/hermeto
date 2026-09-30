# SPDX-License-Identifier: GPL-3.0-only
import json
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar, NamedTuple

from hermeto import APP_NAME
from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import (
    ExitError,
    LockfileNotFound,
    PackageManagerError,
    PackageRejected,
)
from hermeto.core.models.input import CargoPackageInput, CargoPackageSelection, Request
from hermeto.core.models.output import Annotation, Component, ProjectFile, RequestOutput
from hermeto.core.models.sbom import create_backend_annotation, spdx_now
from hermeto.core.package_managers.cargo.config import (
    _hide_original_cargo_config_from_cargo,
    _inject_proxy_configuration_into_config_if_needed,
    _sanitized_cargo_config_file,
    _swap_sources_directory_for_subsitution_slot,
    _use_vendored_sources,
)
from hermeto.core.package_managers.cargo.resolver import (
    _generate_sbom_components,
    _parse_toml_project_file,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import run_cmd

log = logging.getLogger(__name__)


CARGO_VENDOR_FILTERER = "cargo-vendor-filterer"


class CargoVendorResult(NamedTuple):
    """
    Vendoring result from running the `cargo vendor` command.
    """

    config_template: str
    lockfile_was_generated: bool
    # (name, version) of the crates the selected packages can reach, None when unfiltered
    reachable_crates: frozenset[tuple[str, str]] | None = None


class PackageWithCorruptLockfileRejected(PackageRejected):
    """Package lock file does not match package config."""

    _exit_error: ClassVar[ExitError] = ExitError.ERR_PACKAGE_WITH_CORRUPT_LOCKFILE_REJECTED

    def __init__(self, package_path: str) -> None:
        """Initialize the error."""
        reason = (
            f"{package_path} contains a Cargo.lock that does not match the corresponding Cargo.toml"
        )
        super().__init__(reason, solution=self.default_solution)

    default_solution = (
        "Consider reaching out to maintainer of the dependency in question to address"
        " inconsistencies between Cargo.lock and Cargo.toml"
    )


def fetch_cargo_source(request: Request, invoked_through_pip: bool = False) -> RequestOutput:
    """Fetch the source code for all cargo packages specified in a request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []
    annotations: list[Annotation] = []

    for package in request.cargo_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        _verify_lockfile_is_present(package_dir)

        vendor_result = _fetch_dependencies(package_dir, request, package)
        # cargo allows to specify configuration per-package
        # https://doc.rust-lang.org/cargo/reference/config.html#hierarchical-structure
        if vendor_result.config_template:
            config_template = _swap_sources_directory_for_subsitution_slot(
                vendor_result.config_template
            )
            project_files.append(_use_vendored_sources(package_dir, config_template))
        package_components = _generate_sbom_components(
            package_dir, request, invoked_through_pip, vendor_result.reachable_crates
        )

        if vendor_result.lockfile_was_generated:
            _update_permissive_mode_annotation(annotations, package_components)

        components.extend(package_components)

    if backend_annotation := create_backend_annotation(components, "cargo"):
        annotations.append(backend_annotation)
    return RequestOutput.from_obj_list(
        components=components,
        project_files=project_files,
        annotations=annotations,
    )


def _verify_lockfile_is_present(package_dir: RootedPath) -> None:
    """Verify that the Cargo.lock file is present in the package directory."""
    mode = get_config().mode
    lockfile = package_dir.path / "Cargo.lock"
    if lockfile.exists():
        return

    if mode == Mode.PERMISSIVE:
        log.warning("Cargo.lock not found in %s, continuing due to permissive mode", package_dir)
    else:
        raise LockfileNotFound(
            lockfile,
            solution=f"Cargo.lock not found in {package_dir}, run `cargo generate-lockfile` or use permissive mode",
        )


def _fetch_dependencies(
    package_dir: RootedPath, request: Request, package: CargoPackageInput | None = None
) -> CargoVendorResult:
    """Fetch cargo dependencies and return a config template for hermetic build."""
    vendor_dir = request.output_dir.join_within_root("deps/cargo")
    selection = package.packages if package is not None else None

    # A crate stubbed out for one input must neither replace nor shadow the real crate another
    # input needs, and `cargo vendor` on its own cannot express that: without --no-delete it
    # drops crates other inputs vendored, and with it a stub left in place shadows a real crate.
    # Every run therefore vendors into a staging directory and merges. The staging directory is
    # a sibling of the vendor directory, so merging renames rather than copies.
    vendor_dir.path.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".cargo-staging-", dir=vendor_dir.path.parent))
    # cargo-vendor-filterer refuses to write into an existing directory
    staging_dir = staging_root / "vendor"
    report_path = staging_root / "filter-report.json"
    try:
        if selection is None:
            cmd = [
                "cargo",
                "vendor",
                "--locked",
                "--versioned-dirs",
                "--respect-source-config",
                str(staging_dir),
            ]
        else:
            cmd = _cargo_vendor_filterer_cmd(selection, package, staging_dir, report_path)
        result = _run_vendor_command(cmd, package_dir)
        reachable: frozenset[tuple[str, str]] | None = None
        stubbed: frozenset[str] = frozenset()
        if selection is not None:
            reachable, stubbed = _read_filter_report(report_path, staging_dir)
        _merge_vendored_crates(staging_dir, vendor_dir.path, stubbed)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    return result._replace(reachable_crates=reachable)


def _cargo_vendor_filterer_cmd(
    selection: list[CargoPackageSelection],
    package: CargoPackageInput | None,
    output_dir: Path,
    report_path: Path,
) -> list[str]:
    """Build the cargo-vendor-filterer command that keeps only what the selection can reach."""
    if len({p.no_default_features for p in selection}) > 1 or (
        len({p.all_features for p in selection}) > 1
    ):
        raise PackageRejected(
            "Packages selected from one cargo input must agree on 'no_default_features' and "
            "'all_features': cargo-vendor-filterer applies these flags to every selected package",
            solution=(
                "Use the same 'no_default_features' and 'all_features' values for every entry in "
                "'packages', and enable the remaining differences through 'features'"
            ),
        )
    # --keep-dep-kinds no-dev  dev-dependencies are never part of a shipped build, and keeping
    #                          them pulls back crates (e.g. TLS backends) the build cannot reach.
    # --json                   names the crates replaced with stubs, so neither the merge nor
    #                          the SBOM has to guess which vendored directories are stubs.
    cmd = [
        CARGO_VENDOR_FILTERER,
        "--locked",
        "--versioned-dirs",
        "--respect-source-config",
        "--keep-dep-kinds",
        "no-dev",
        "--json",
        str(report_path),
    ]
    for selected in selection:
        cmd += ["--package", selected.name]
    # member/feature enables a feature on that selected member only
    features = [f"{p.name}/{feature}" for p in selection for feature in p.features]
    if features:
        cmd += ["--features", ",".join(features)]
    if selection[0].no_default_features:
        cmd.append("--no-default-features")
    if selection[0].all_features:
        cmd.append("--all-features")
    for platform in (package.platforms if package is not None else None) or []:
        cmd += ["--platform", platform]
    cmd.append(str(output_dir))
    return cmd


def _run_vendor_command(cmd: list[str], package_dir: RootedPath) -> CargoVendorResult:
    log.info("Fetching cargo dependencies at %s", package_dir)
    if (proxy_url := get_config().cargo.proxy_url) is not None:
        log.info("Using registry proxy %s for registry dependencies", proxy_url)
    # NOTE: ordering is important here, a config must be sanitized first, extended to use
    # a proxy after that, otherwise proxy data will be scrubbed by the sanitizer.
    with (
        _sanitized_cargo_config_file(package_dir),
        _inject_proxy_configuration_into_config_if_needed(package_dir),
        _hide_original_cargo_config_from_cargo(package_dir),
    ):
        # Prevent Cargo from invoking rustc
        env = {"CARGO_RESOLVER_INCOMPATIBLE_RUST_VERSIONS": "allow"}
        if cmd[0] == CARGO_VENDOR_FILTERER:
            # it runs cargo and rustc by name, which fails outside /usr/bin without PATH
            env["PATH"] = os.environ.get("PATH", os.defpath)
        # The necessary configuration to use the vendored sources will be printed to STDOUT.
        # https://doc.rust-lang.org/cargo/commands/cargo-vendor.html#description
        return _run_cmd_watching_out_for_lock_mismatch(
            cmd=cmd,
            params={"cwd": package_dir, "env": env},
            package_dir=package_dir.path,
        )


def _read_filter_report(
    report_path: Path, vendor_dir: Path
) -> tuple[frozenset[tuple[str, str]], frozenset[str]]:
    """Read which vendored crates cargo-vendor-filterer replaced with stubs.

    Returns the real crates as (name, version), and the stubs as the directory names
    `cargo vendor --versioned-dirs` gives them. The report names only the stubs; every other
    directory in vendor_dir is a real crate. Those are identified by their own manifest, since a
    directory name cannot be split into name and version unambiguously (both may contain '-').
    Asking the tool beats inspecting the directories: a stub is only recognisable by its shape
    or its contents, and both misread real crates (memchr and anyhow carry `compile_error!` in
    their own sources).
    """
    solution = (
        f"Check that the {CARGO_VENDOR_FILTERER} on PATH supports --json; "
        "the version Hermeto's container image ships does."
    )
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError) as e:
        raise PackageRejected(
            f"{CARGO_VENDOR_FILTERER} did not write a readable filter report: {e}",
            solution=solution,
        ) from e

    stubs = report.get("stubs") if isinstance(report, dict) else None
    if not isinstance(stubs, list) or not all(isinstance(stub, str) for stub in stubs):
        raise PackageRejected(
            f"{CARGO_VENDOR_FILTERER} wrote a filter report without a list of stubs",
            solution=solution,
        )

    stubbed = frozenset(stubs)
    kept = frozenset(
        _vendored_crate_identity(crate)
        for crate in vendor_dir.iterdir()
        if crate.is_dir() and crate.name not in stubbed
    )
    return kept, stubbed


def _vendored_crate_identity(crate_dir: Path) -> tuple[str, str]:
    """Return the (name, version) a vendored crate's manifest declares."""
    package = _parse_toml_project_file(crate_dir / "Cargo.toml").get("package", {})
    # https://doc.rust-lang.org/cargo/reference/manifest.html#the-version-field
    return package["name"], package.get("version", "0.0.0")


def _merge_vendored_crates(source_dir: Path, vendor_dir: Path, stubbed: frozenset[str]) -> None:
    """Move vendored crates into the shared vendor directory, a real crate always winning.

    A stub is only moved where nothing is present yet, so it can never shadow a crate another
    input vendored for real; everything else replaces whatever is there.
    """
    vendor_dir.mkdir(parents=True, exist_ok=True)
    for crate in source_dir.iterdir():
        target = vendor_dir / crate.name
        if target.exists():
            if crate.name in stubbed:
                continue
            shutil.rmtree(target)
        shutil.move(crate, target)


def _run_cmd_watching_out_for_lock_mismatch(
    cmd: list, params: dict, package_dir: Path
) -> CargoVendorResult:
    warn_about_imminent_update_to_cargo_lock = (
        f"A mismatch between Cargo.lock and Cargo.toml was detected in {package_dir}. "
        "Because of permissive mode Hermeto will now regenerate Cargo.lock "
        "to match expectation and will try to process the package again. This "
        f"is a violation of reproducibility and must be addressed by {package_dir.name} "
        "maintainers."
    )
    mode = get_config().mode
    update_cargo_lock_cmd = ["cargo", "generate-lockfile"]
    try:
        stdout = run_cmd(cmd=cmd, params=params, suppress_errors=(mode == Mode.PERMISSIVE))
        return CargoVendorResult(config_template=stdout, lockfile_was_generated=False)
    except subprocess.CalledProcessError as e:
        # Search for a very specific failure state to better report it.
        # This is not a robust solution in any way, however it seems to be the only one
        # readily available: cargo returns a generic 101 code on this failure and on multiple
        # others, thus the only way to check for this specific type of failure is to process
        # stderr. Two parts of a string are used to decrease the likelihood of false positives.
        generic_vendor_error = "failed to sync"
        # Since Cargo version 1.93.0, the error message for an unsynchronized lockfile has changed.
        specific_vendor_error_variants = (
            "needs to be updated but --locked was passed",
            "because --locked was passed to prevent this",
        )

        if generic_vendor_error in e.stderr and any(
            error in e.stderr for error in specific_vendor_error_variants
        ):
            if mode == Mode.PERMISSIVE:
                log.warning(warn_about_imminent_update_to_cargo_lock)
                with _temporary_cwd(package_dir):
                    # Extract env from params if present to pass to cargo generate-lockfile
                    update_cmd_params = {"env": params.get("env", {})}
                    run_cmd(cmd=update_cargo_lock_cmd, params=update_cmd_params)
                # If it fails here then something else is horribly broken.
                # No more attempts to salvage the situation will be made.
                stdout = run_cmd(cmd=cmd, params=params)
                return CargoVendorResult(config_template=stdout, lockfile_was_generated=True)
            else:
                raise PackageWithCorruptLockfileRejected(str(package_dir))
        else:
            raise PackageManagerError(
                f"Cargo execution failed: `{' '.join(cmd)}` failed with rc={e.returncode}",
                stderr=e.stderr,
            ) from e


def _update_permissive_mode_annotation(
    annotations: list[Annotation],
    components: list[Component],
) -> None:
    """Update permissive mode SBOM annotation with subjects from the provided components."""
    text = f"{APP_NAME}:permissive-mode:cargo:generated-lockfile"
    subjects = set(c.bom_ref for c in components)
    for annotation in annotations:
        if annotation.text == text:
            annotation.subjects.update(subjects)
            return

    annotations.append(
        Annotation(
            subjects=subjects,
            annotator={"organization": {"name": "red hat"}},
            timestamp=spdx_now(),
            text=text,
        )
    )


@contextmanager
def _temporary_cwd(path_to_new_cwd: Path) -> Generator[None, None, None]:
    oldcwd = os.getcwd()
    os.chdir(path_to_new_cwd)
    try:
        yield
    finally:
        os.chdir(oldcwd)
