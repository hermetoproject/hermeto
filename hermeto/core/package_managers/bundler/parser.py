import json
import logging
import subprocess
from pathlib import Path
from typing import Union

from hermeto.core.errors import PackageManagerError, PackageRejected
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


BundlerDependency = Union[
    GemDependency, GemPlatformSpecificDependency, GitDependency, PathDependency
]
ParseResult = list[BundlerDependency]


def parse_lockfile(package_dir: RootedPath, allow_binary: bool = False) -> ParseResult:
    """Parse a Gemfile.lock file and return a list of dependencies."""
    lockfile_path = package_dir.join_within_root(GEMFILE_LOCK)
    gemfile_path = package_dir.join_within_root(GEMFILE)
    if not lockfile_path.path.exists() or not gemfile_path.path.exists():
        reason = "Gemfile and Gemfile.lock must be present in the package directory"
        solution = (
            "Run `bundle init` to generate the Gemfile.\n"
            "Run `bundle lock` to generate the Gemfile.lock."
        )
        raise PackageRejected(reason=reason, solution=solution)

    scripts_dir = Path(__file__).parent / "scripts"
    lockfile_parser = scripts_dir / "lockfile_parser.rb"
    try:
        output = run_cmd(cmd=[str(lockfile_parser)], params={"cwd": package_dir.path})
    except subprocess.CalledProcessError:
        raise PackageManagerError(f"Failed to parse {lockfile_path}")

    json_output = json.loads(output)

    bundler_version: str = json_output["bundler_version"]
    log.info("Package %s is bundled with version %s", package_dir.path.name, bundler_version)
    dependencies: list[dict[str, str]] = json_output["dependencies"]

    result: ParseResult = []
    for dep in dependencies:
        if dep["type"] == "rubygems":
            if dep["platform"] == "ruby":
                result.append(GemDependency(**dep))
            else:
                full_name = "-".join([dep["name"], dep["version"], dep["platform"]])
                log.info("Found a binary dependency %s", full_name)
                if allow_binary:
                    log.warning(
                        "Will download binary dependency %s because 'allow_binary' is set to True",
                        full_name,
                    )
                    result.append(GemPlatformSpecificDependency(**dep))
                else:
                    # No need to force a platform if we skip the packages.
                    log.warning(
                        "Skipping binary dependency %s because 'allow_binary' is set to False."
                        " This will likely result in an unbuildable package.",
                        full_name,
                    )
        elif dep["type"] == "git":
            result.append(GitDependency(**dep))
        elif dep["type"] == "path":
            result.append(PathDependency(**dep, root=package_dir))

    return result
