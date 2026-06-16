# SPDX-License-Identifier: GPL-3.0-only
import logging
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Any

from hermeto.core.package_managers.javascript.package_json import PackageJson
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


@dataclass
class Workspace:
    """
    Workspace model.

    Attributes:
        path: Path to workspace directory.
        package_json: Content of package.json file.
    """

    path: Path
    package_json: PackageJson

    def __post_init__(self) -> None:
        if self.package_json.data.get("name") is None:
            raise ValueError("Workspaces must contain 'name' field.")


def ensure_no_path_leads_out(
    paths: Iterable[Path],
    source_dir: RootedPath,
) -> None:
    """Ensure no path leads out of source directory.

    Raises an exception when any path is not relative to source directory.
    Does nothing when path does not exist in the file system.
    """
    for path in paths:
        source_dir.join_within_root(path)


def get_workspace_paths(workspaces_globs: list[str], source_dir: RootedPath) -> list[Path]:
    """Resolve globs within source directory."""

    def all_paths_matching(glob: str) -> Generator[Path, None, None]:
        return (path.resolve() for path in source_dir.path.glob(glob) if path.is_dir())

    return list(chain.from_iterable(map(all_paths_matching, workspaces_globs)))


def _extract_workspaces_globs(package: dict[str, Any]) -> list[str]:
    """Extract globs from workspaces entry in package dict.

    The 'workspaces' entry can either be:
    - an array of strings
      (e.g., "workspaces": ["workspace-a", "workspace-b"])
    - an object with a 'packages' key containing an array of strings
      (e.g., "workspaces": {"packages": ["workspace-a", "workspace-b"]})

    See:
    https://classic.yarnpkg.com/en/docs/workspaces/#toc-how-to-use-it
    https://classic.yarnpkg.com/blog/2018/02/15/nohoist/#how-to-use-it
    """
    workspaces_globs = package.get("workspaces", [])
    if isinstance(workspaces_globs, dict):
        workspaces_globs = workspaces_globs.get("packages", [])
    return workspaces_globs


def extract_workspace_metadata(package_path: RootedPath) -> list[Workspace]:
    """Extract workspace metadata from a package."""
    package_json = PackageJson.from_dir(package_path.path)
    workspaces_globs = _extract_workspaces_globs(package_json.data)
    workspaces_paths = get_workspace_paths(workspaces_globs, package_path)
    ensure_no_path_leads_out(workspaces_paths, package_path)

    parsed_workspaces = []
    for wp in workspaces_paths:
        package_json_path = package_path.join_within_root(wp, "package.json")

        # Ignore "workspaces" with missing package.json
        # https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/config.js#L833
        if not package_json_path.path.exists():
            log.warning(
                (
                    "The Yarn workspace located at %s does not contain a "
                    "package.json and will be ignored."
                ),
                wp,
            )
            continue

        parsed_workspaces.append(
            Workspace(path=wp, package_json=PackageJson.from_file(package_json_path.path))
        )

    return parsed_workspaces
