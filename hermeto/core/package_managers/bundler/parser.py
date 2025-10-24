import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Optional, Union

from hermeto.core.binary_filters import BinaryPackageFilter
from hermeto.core.errors import PackageManagerError, PackageRejected
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


BundlerDependency = Union[
    GemDependency, GemPlatformSpecificDependency, GitDependency, PathDependency
]
ParseResult = list[BundlerDependency]


def parse_lockfile(
    package_dir: RootedPath, binary_filters: Optional[BundlerBinaryFilters] = None
) -> ParseResult:
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
                            "Will download binary dependency %s because 'allow_binary' is set to True",
                            full_name,
                        )
                        result.append(GemPlatformSpecificDependency(platform=platform, **dep))
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


class GemsFilter(BinaryPackageFilter):
    """Filter gems based on the filter constraints."""

    def __init__(self, filters: BundlerBinaryFilters) -> None:
        """Initialize the filter."""
        self.packages = self._parse_filter_spec(filters.packages)
        self.platform = self._parse_filter_spec(filters.platform)

    def __contains__(self, item: Any) -> bool:
        return NotImplemented

    def _prefer_binary(self, gem: dict[str, Any]) -> None:
        if "ruby" in gem["platforms"] and len(gem["platforms"]) > 1:
            gem["platforms"].remove("ruby")

    def apply_platform_filters(self, gems: list[dict[str, Any]]) -> None:
        """Update platforms for each gem based on the filter constraints."""
        for gem in gems:
            # all packages | all platforms
            if self.packages is None and self.platform is None:
                self._prefer_binary(gem)

            # all packages | specific platforms
            elif self.packages is None and self.platform is not None:
                gem["platforms"] = list(self.platform)

            # specific packages | all platforms
            elif self.packages is not None and self.platform is None:
                if gem["name"] in self.packages:
                    self._prefer_binary(gem)
                else:
                    gem["platforms"] = ["ruby"]

            # specific packages | specific platforms
            elif self.packages is not None and self.platform is not None:
                if gem["name"] in self.packages:
                    gem["platforms"] = list(self.platform)
                else:
                    gem["platforms"] = ["ruby"]
