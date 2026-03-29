# SPDX-License-Identifier: GPL-3.0-only
from typing import Any

import tomlkit
import tomlkit.exceptions

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound
from hermeto.core.rooted_path import RootedPath


def parse_uv_lockfile(package_dir: RootedPath) -> dict[str, Any]:
    """Load and validate a uv.lock file from package_dir."""
    lockfile = package_dir.join_within_root("uv.lock")

    if not lockfile.path.exists():
        raise LockfileNotFound(
            files=lockfile.path,
            solution=(
                f"No uv.lock found in {package_dir}; run `uv lock` and commit uv.lock to the repository."
            ),
        )

    try:
        parsed = tomlkit.parse(lockfile.path.read_text()).value
    except tomlkit.exceptions.TOMLKitError as e:
        raise InvalidLockfileFormat(lockfile.path, str(e)) from e

    if not isinstance(parsed, dict):
        raise InvalidLockfileFormat(lockfile.path, "expected a TOML table at document root")

    version = parsed.get("version")
    if not isinstance(version, int):
        raise InvalidLockfileFormat(lockfile.path, "missing or invalid top-level 'version' field")

    return parsed
