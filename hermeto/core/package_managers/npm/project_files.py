# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from hermeto.core.models.output import ProjectFile
from hermeto.core.rooted_path import RootedPath

if TYPE_CHECKING:
    from hermeto.core.package_managers.npm.package_lock import NormalizedUrl, PackageLock


def _should_replace_dependency(dependency_version: str) -> bool:
    """Check if dependency must be updated in package(-lock).json.

    package(-lock).json files require to replace dependency URLs for
    empty string in git and https dependencies.
    """
    url = urlparse(dependency_version)
    if url.scheme == "file" or url.scheme == "npm":
        return False
    return url.scheme != "" or "/" in dependency_version


def _update_package_lock_with_local_paths(
    download_paths: dict[NormalizedUrl, RootedPath],
    package_lock: PackageLock,
) -> None:
    """Replace packages resolved URLs with local paths.

    Update package-lock.json file in a way it can be used in isolated environment (container)
    without internet connection. All package resolved URLs will be replaced for
    local paths to downloaded dependencies.

    :param download_paths:
    :param package_lock: PackageLock instance which holds package-lock.json content
    """
    from hermeto.core.package_managers.npm.package_lock import (
        DEPENDENCY_TYPES,
        _classify_resolved_url,
        _normalize_resolved_url,
    )

    for package in package_lock.packages + [package_lock.main_package]:
        for dep_type in DEPENDENCY_TYPES:
            if package.package_dict.get(dep_type):
                for dependency, dependency_version in package.package_dict[dep_type].items():
                    if _should_replace_dependency(dependency_version):
                        package.package_dict[dep_type].update({dependency: ""})

        if package.path and package.resolved_url:
            url = _normalize_resolved_url(str(package.resolved_url))
        else:
            continue

        # Remove integrity for git sources, their integrity checksum will change when
        # constructing tar archive from cloned repository
        if _classify_resolved_url(url) == "git":
            if package.integrity:
                package.integrity = ""

        # Replace the resolved_url of all packages, unless it's already a file url:
        if _classify_resolved_url(url) != "file":
            templated_abspath = Path("${output_dir}", download_paths[url].subpath_from_root)
            package.resolved_url = f"file://{templated_abspath}"


def _update_package_json_files(
    workspaces: list[str],
    pkg_dir: RootedPath,
) -> list[ProjectFile]:
    """Set dependencies to empty string in package.json files.

    :param workspaces: list of workspaces paths
    :param pkg_dir: Package subdirectory
    """
    from hermeto.core.package_managers.npm.package_lock import DEPENDENCY_TYPES

    package_json_paths = []
    for workspace in workspaces:
        package_json_paths.append(pkg_dir.join_within_root(workspace, "package.json"))
    package_json_paths.append(pkg_dir.join_within_root("package.json"))

    package_json_projectfiles = []
    for package_json_path in package_json_paths:
        with open(package_json_path) as f:
            package_json_content = json.load(f)

        for dep_type in DEPENDENCY_TYPES:
            if package_json_content.get(dep_type):
                for dependency, url in package_json_content[dep_type].items():
                    if _should_replace_dependency(url):
                        package_json_content[dep_type].update({dependency: ""})

        package_json_projectfiles.append(
            ProjectFile(
                abspath=package_json_path.path,
                template=json.dumps(package_json_content, indent=2) + "\n",
            )
        )
    return package_json_projectfiles
