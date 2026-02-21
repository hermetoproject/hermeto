# SPDX-License-Identifier: GPL-3.0-only
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from hermeto.core.errors import LockfileNotFound, PackageManagerError
from hermeto.core.models.input import BundlerBinaryFilters
from hermeto.core.package_managers.bundler.gem_models import (
    GemDependency,
    GemPlatformSpecificDependency,
    GitDependency,
    PathDependency,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import run_cmd

log = logging.getLogger(__name__)

GEMFILE = "Gemfile"
GEMFILE_LOCK = "Gemfile.lock"


BundlerDependency = GemDependency | GemPlatformSpecificDependency | GitDependency | PathDependency
ParseResult = list[BundlerDependency]


def _ensure_bundler_files_exist(package_dir: RootedPath) -> None:
    lockfile_path = package_dir.join_within_root(GEMFILE_LOCK)
    gemfile_path = package_dir.join_within_root(GEMFILE)
    if not lockfile_path.path.exists() or not gemfile_path.path.exists():
        raise LockfileNotFound(
            files=lockfile_path.path if not lockfile_path.path.exists() else gemfile_path.path,
        )


def parse_lockfile(
    package_dir: RootedPath, binary_filters: BundlerBinaryFilters | None = None
) -> ParseResult:
    """Parse a Gemfile.lock file and return a list of dependencies."""
    _ensure_bundler_files_exist(package_dir)

    scripts_dir = Path(__file__).parent / "scripts"
    lockfile_parser = scripts_dir / "lockfile_parser.rb"
    try:
        output = run_cmd(cmd=[str(lockfile_parser)], params={"cwd": package_dir.path})
    except subprocess.CalledProcessError as e:
        raise PackageManagerError("Failed to parse Gemfile.lock") from e

    json_output = json.loads(output)

    bundler_version: str = json_output["bundler_version"]
    log.info("Package %s is bundled with version %s", package_dir.path.name, bundler_version)
    dependencies: list[dict[str, Any]] = json_output["dependencies"]

    result: ParseResult = []
    for dep in dependencies:
        if dep["type"] == "rubygems":
            for platform in dep["platforms"]:
                if platform == "ruby":
                    result.append(GemDependency(**dep))
                else:
                    full_name = "-".join([dep["name"], dep["version"], platform])
                    log.info("Found a binary dependency %s", full_name)
                    if binary_filters is not None:
                        log.warning(
                            "Will download binary dependency %s because 'binary' field is set",
                            full_name,
                        )
                        result.append(GemPlatformSpecificDependency(platform=platform, **dep))
                    else:
                        # No need to force a platform if we skip the packages.
                        log.warning(
                            "Skipping binary dependency %s because 'binary' field is not set."
                            " This will likely result in an unbuildable package.",
                            full_name,
                        )
        elif dep["type"] == "git":
            result.append(GitDependency(**dep))
        elif dep["type"] == "path":
            result.append(PathDependency(**dep, root=package_dir))

    return result
