from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hermeto import APP_NAME
from hermeto.core.models.input import PackageManagerType, Request
from hermeto.core.models.output import RequestOutput
from hermeto.core.package_managers import (
    bundler,
    cargo,
    generic,
    gomod,
    huggingface,
    metayarn,
    npm,
    pip,
    rpm,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import copy_directory

Handler = Callable[[Request], RequestOutput]

_package_managers: dict[PackageManagerType, Handler] = {
    "bundler": bundler.fetch_bundler_source,
    "cargo": cargo.fetch_cargo_source,
    "gomod": gomod.fetch_gomod_source,
    "npm": npm.fetch_npm_source,
    "pip": pip.fetch_pip_source,
    "yarn": metayarn.fetch_yarn_source,
    "generic": generic.fetch_generic_source,
    "rpm": rpm.fetch_rpm_source,
    "x-huggingface": huggingface.fetch_huggingface_source,
}


# This is *only* used to provide a list for `hermeto --version`
supported_package_managers = list(_package_managers)


def resolve_packages(request: Request) -> RequestOutput:
    """
    Resolve all packages specified in a request.

    This function performs the operations in a working copy of the source directory in case
    a package manager that can make unwanted modifications will be used.
    """
    original_source_dir = request.source_dir

    with TemporaryDirectory(f".{APP_NAME}-source-copy", dir=".") as temp_dir:
        source_backup = copy_directory(original_source_dir.path, Path(temp_dir).resolve())

        request.source_dir = RootedPath(source_backup)
        output = _resolve_packages(request)
        request.source_dir = original_source_dir

        # Update all project file paths that end up directly in the source repository
        for project_file in output.build_config.project_files:
            try:
                subpath = project_file.abspath.relative_to(source_backup)
                project_file.abspath = original_source_dir / subpath
            except ValueError:
                # '<project_file.abspath> is not in the subpath of <source_backup>', i.e the file
                # is referenced directly from the output directory and doesn't need replacing
                continue

        return output


def _resolve_packages(request: Request) -> RequestOutput:
    """Run all requested package managers, return their combined output."""
    _supported_package_managers = _package_managers
    requested_types = set(pkg.type for pkg in request.packages)
    pkg_managers = [_supported_package_managers[type_] for type_ in sorted(requested_types)]
    return sum([pkg_manager(request) for pkg_manager in pkg_managers], RequestOutput.empty())


def inject_files_post(from_output_dir: Path, for_output_dir: Path, **kwargs: Any) -> None:
    """Do extra steps for package manager."""
    # if there is a callback method defined within the particular package manager, run it
    if hasattr(rpm, "inject_files_post"):
        callback_method = getattr(rpm, "inject_files_post")
        callback_method(from_output_dir, for_output_dir, **kwargs)
