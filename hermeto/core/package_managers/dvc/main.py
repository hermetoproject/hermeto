"""Main logic for fetching DVC dependencies."""

import logging
import os
import subprocess
from pathlib import Path

from hermeto import APP_NAME
from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import Mode, Request
from hermeto.core.models.output import EnvironmentVariable, RequestOutput
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.dvc.parser import (
    generate_sbom_components,
    load_dvc_lockfile,
    validate_checksums_present,
)
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE_NAME = "dvc.lock"
DEFAULT_CACHE_DIR = "deps/dvc/cache"


def fetch_dvc_source(request: Request) -> RequestOutput:
    """
    Resolve and fetch DVC dependencies for a given request.

    Uses `dvc fetch` to download all external dependencies declared in dvc.lock.
    Requires a git repository context.

    :param request: the request to process
    :return: Request output with fetched components and environment variables
    """
    components = []
    for package in request.dvc_packages:
        path = request.source_dir.join_within_root(package.path)
        lockfile_path = path.join_within_root(DEFAULT_LOCKFILE_NAME).path

        components.extend(_resolve_dvc_lockfile(lockfile_path, request))

    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=_generate_environment_variables(),
    )


def _resolve_dvc_lockfile(lockfile_path: Path, request: Request) -> list[Component]:
    """
    Resolve the DVC lockfile and fetch dependencies using dvc CLI.

    :param lockfile_path: Path to dvc.lock file
    :param request: The request object containing source and output directories
    :return: List of SBOM components
    """
    # Load and validate lockfile
    log.info(f"Reading DVC lockfile: {lockfile_path}")
    lockfile = load_dvc_lockfile(lockfile_path)

    # Validate checksums are present (strict mode by default)
    strict_mode = request.mode == Mode.STRICT
    validate_checksums_present(lockfile, strict=strict_mode)

    # Setup cache directory
    cache_dir = request.output_dir.join_within_root(DEFAULT_CACHE_DIR)
    cache_dir.path.mkdir(parents=True, exist_ok=True)

    # Run dvc fetch
    _run_dvc_fetch(request.source_dir, cache_dir)

    # Generate SBOM from lockfile
    components = generate_sbom_components(lockfile)

    return components


def _run_dvc_fetch(source_dir: RootedPath, cache_dir: RootedPath) -> None:
    """
    Execute dvc fetch command to download all dependencies.

    :param source_dir: Source directory containing dvc.lock
    :param cache_dir: Cache directory where DVC should store downloaded files
    :raises PackageRejected: If dvc fetch fails
    """
    log.info(f"Running dvc fetch with cache at {cache_dir.path}")

    # Set DVC_CACHE_DIR to our output location
    env = os.environ.copy()
    env["DVC_CACHE_DIR"] = str(cache_dir.path)

    try:
        result = subprocess.run(
            ["dvc", "fetch"],
            cwd=source_dir.path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise PackageRejected(
            "dvc command not found",
            solution=(
                "Make sure DVC is installed and available in PATH. "
                "You can install it with: pip install dvc"
            ),
        ) from e

    if result.returncode != 0:
        error_msg = result.stderr.strip() if result.stderr else result.stdout.strip()
        raise PackageRejected(
            f"dvc fetch failed: {error_msg}",
            solution=(
                "Check that dvc.lock is valid and all dependencies are accessible. "
                "Make sure you're in a git repository and DVC is properly configured."
            ),
        )

    log.info("dvc fetch completed successfully")


def _generate_environment_variables() -> list[EnvironmentVariable]:
    """
    Generate environment variables for building with DVC dependencies.

    The hermetic build should set DVC_CACHE_DIR and then run `dvc pull` to
    checkout files from the pre-populated cache.
    """
    return [
        EnvironmentVariable(
            name="DVC_CACHE_DIR",
            value=f"${{output_dir}}/{DEFAULT_CACHE_DIR}",
        )
    ]
