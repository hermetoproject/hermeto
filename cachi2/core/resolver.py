from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from cachi2.core.errors import UnsupportedFeature
from cachi2.core.models.input import PackageManagerType, Request
from cachi2.core.models.output import RequestOutput
from cachi2.core.package_managers import bundler, cargo, generic, gomod, metayarn, npm, pip, rpm
from cachi2.core.package_managers.utils import merge_outputs
from cachi2.core.rooted_path import RootedPath
from cachi2.core.utils import copy_directory

Handler = Callable[[Request], RequestOutput]

_package_managers: dict[PackageManagerType, Handler] = {
    "bundler": bundler.fetch_bundler_source,
    "cargo": cargo.fetch_cargo_source,
    "gomod": gomod.fetch_gomod_source,
    "npm": npm.fetch_npm_source,
    "pip": pip.fetch_pip_source,
    "yarn": metayarn.fetch_yarn_source,
    "generic": generic.fetch_generic_source,
}

# This is where we put package managers currently under development in order to
# invoke them via CLI
_dev_package_managers: dict[PackageManagerType, Handler] = {
    "rpm": rpm.fetch_rpm_source,
}

# This is *only* used to provide a list for `cachi2 --version`
supported_package_managers = list(_package_managers)


def resolve_packages(request: Request) -> RequestOutput:
    """
    Resolve all packages specified in a request.

    This function performs the operations in a working copy of the source directory in case
    a package manager that can make unwanted modifications will be used.
    """
    if request.yarn_packages:
        original_source_dir = request.source_dir

        with TemporaryDirectory(".cachi2-source-copy", dir=".") as temp_dir:
            source_backup = copy_directory(original_source_dir.path, Path(temp_dir).resolve())

            request.source_dir = RootedPath(source_backup)
            output = _resolve_packages(request)
            request.source_dir = original_source_dir

            # Temporary solution to project files paths that are pointing to the work copy.
            # Should be replaced once we extend the work copy solution to other package managers.
            # https://github.com/containerbuildsystem/cachi2/issues/712
            for project_file in output.build_config.project_files:
                subpath = project_file.abspath.relative_to(source_backup)
                project_file.abspath = original_source_dir / subpath

            return output
    else:
        return _resolve_packages(request)


def _resolve_packages(request: Request) -> RequestOutput:
    """Run all requested package managers, return their combined output."""
    _supported_package_managers = _package_managers
    requested_types = set(pkg.type for pkg in request.packages)
    if "dev-package-managers" in request.flags:
        _supported_package_managers = _package_managers | _dev_package_managers
    unsupported_types = requested_types - _supported_package_managers.keys()
    if unsupported_types:
        raise UnsupportedFeature(
            f"Package manager(s) not yet supported: {', '.join(sorted(unsupported_types))}",
            # unknown package managers shouldn't get past input validation
            solution="But the good news is that we're already working on it!",
        )
    pkg_managers = [_supported_package_managers[type_] for type_ in sorted(requested_types)]
    return merge_outputs(pkg_manager(request) for pkg_manager in pkg_managers)


def inject_files_post(from_output_dir: Path, for_output_dir: Path, **kwargs: Any) -> None:
    """Do extra steps for package manager."""
    # if there is a callback method defined within the particular package manager, run it
    if hasattr(rpm, "inject_files_post"):
        callback_method = getattr(rpm, "inject_files_post")
        callback_method(from_output_dir, for_output_dir, **kwargs)
