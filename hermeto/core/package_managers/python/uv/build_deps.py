# SPDX-License-Identifier: GPL-3.0-only
import logging

from hermeto.core.package_managers.python.pip.main import (
    DEFAULT_BUILD_REQUIREMENTS_FILE,
    download_from_requirement_files,
)
from hermeto.core.package_managers.python.pip.packages import PipPackage
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


def download_build_dependencies(
    package_dir: RootedPath, output_dir: RootedPath
) -> list[PipPackage]:
    """Download build-time dependencies listed in requirements-build.txt, if present.

    Artifacts land in {output_dir}/deps/uv next to the runtime dependencies,
    where UV_FIND_LINKS already exposes them to the build.
    """
    req_file = package_dir.join_within_root(DEFAULT_BUILD_REQUIREMENTS_FILE)
    if not req_file.path.is_file():
        log.info("No %s found, no build dependencies will be fetched", req_file.subpath_from_root)
        return []

    log.info("Using build requirements file: %s", req_file.subpath_from_root)
    return download_from_requirement_files(output_dir, [req_file], deps_dir_name="uv")
