# SPDX-License-Identifier: GPL-3.0-only
import logging
import os
import subprocess
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
from hermeto.core.models.input import Request
from hermeto.core.models.output import Annotation, Component, ProjectFile, RequestOutput
from hermeto.core.models.sbom import create_backend_annotation, spdx_now
from hermeto.core.package_managers.cargo.config import (
    _hide_original_cargo_config_from_cargo,
    _inject_proxy_configuration_into_config_if_needed,
    _sanitized_cargo_config_file,
    _swap_sources_directory_for_subsitution_slot,
    _use_vendored_sources,
)
from hermeto.core.package_managers.cargo.resolver import _generate_sbom_components
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import run_cmd

log = logging.getLogger(__name__)


class CargoVendorResult(NamedTuple):
    """
    Vendoring result from running the `cargo vendor` command.
    """

    config_template: str
    lockfile_was_generated: bool


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

        vendor_result = _fetch_dependencies(package_dir, request)
        # cargo allows to specify configuration per-package
        # https://doc.rust-lang.org/cargo/reference/config.html#hierarchical-structure
        if vendor_result.config_template:
            config_template = _swap_sources_directory_for_subsitution_slot(
                vendor_result.config_template
            )
            project_files.append(_use_vendored_sources(package_dir, config_template))
        package_components = _generate_sbom_components(package_dir, request, invoked_through_pip)

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


def _fetch_dependencies(package_dir: RootedPath, request: Request) -> CargoVendorResult:
    """Fetch cargo dependencies and return a config template for hermetic build."""
    vendor_dir = request.output_dir.join_within_root("deps/cargo")
    # --locked           Assert that `Cargo.lock` will remain unchanged.
    # --versioned-dirs   Always include version in subdir name.
    # --no-delete        Don't delete older crates in the vendor directory.
    #                    It is necessary to make Cargo keep dependencies that are already
    #                    present in the vendored directory. This flag has no effect on standalone
    #                    cargo operations however is crucial when it is invoked from pip.
    # --respect-source-config tells cargo to respect config in .cargo/config.toml in the repository.
    #                         Is necessary when working through a proxy or when custom registries
    #                         must be used.
    cmd = [
        "cargo",
        "vendor",
        "--locked",
        "--versioned-dirs",
        "--no-delete",
        "--respect-source-config",
        str(vendor_dir),
    ]
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
        # The necessary configuration to use the vendored sources will be printed to STDOUT.
        # https://doc.rust-lang.org/cargo/commands/cargo-vendor.html#description
        return _run_cmd_watching_out_for_lock_mismatch(
            cmd=cmd,
            params={"cwd": package_dir, "env": env},
            package_dir=package_dir.path,
        )


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
