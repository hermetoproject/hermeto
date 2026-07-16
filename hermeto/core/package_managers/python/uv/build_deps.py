# SPDX-License-Identifier: GPL-3.0-only
import logging

from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import Component
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


def to_component(package: PipPackage) -> Component:
    """Report a build dependency downloaded through pip's machinery as a uv component.

    Reusing pip's downloader is plumbing the user never asked for: everything
    that gets here was declared in the uv project's own requirements-build.txt,
    so the SBOM has to attribute it to uv. Nothing else is fetched this way,
    which is why every component built here is a build dependency.
    """
    return package.to_sbom_component(
        PropertySet(
            missing_hash_in_file=package.missing_hash_in_file,
            uv_package_binary=(package.package_type == "wheel"),
            uv_build_dependency=True,
        )
    )
