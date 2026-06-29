# SPDX-License-Identifier: GPL-3.0-only
"""This module provides functionality to handle Rust extensions in Python packages."""

import logging
import shutil
from collections.abc import Iterable
from pathlib import Path

import tomlkit

from hermeto.core.models.input import CargoPackageInput, Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.package_managers.cargo import fetch_cargo_source
from hermeto.core.package_managers.python.pip.packages import PipPackage
from hermeto.core.package_managers.python.pip.project_files import PyProjectTOML, SetupCFG, SetupPY
from hermeto.core.package_managers.python.pip.requirements import WHEEL_FILE_EXTENSION
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


def _has_rust_build_deps(raw_build_dependencies: list[str]) -> bool:
    rust_build_deps = ("maturin", "setuptools-rust", "setuptools_rust")
    for dep in raw_build_dependencies:
        if dep.strip().lower().startswith(rust_build_deps):
            return True
    return False


def _depends_on_rust(extracted_dir: Path) -> bool:
    root = RootedPath(extracted_dir)
    python_files_map = {
        "pyproject.toml": PyProjectTOML(root).get_build_system_requires,
        "setup.cfg": SetupCFG(root).get_setup_requires,
        "setup.py": SetupPY(root).get_setup_requires,
    }

    for file, get_build_deps_method in python_files_map.items():
        if not root.join_within_root(file).path.exists():
            continue

        build_deps = get_build_deps_method()
        if _has_rust_build_deps(build_deps):
            return True

    return False


def _shortest_path_parent(files: Iterable[Path]) -> Path:
    return min(files, key=lambda x: len(x.parts)).parent


def _get_rust_root_dir(source_dir: Path) -> Path | None:
    """
    Find the Rust project root directory within a Python package source tree.

    Recursively search the source directory for Cargo manifests. Return the directory containing
    the shallowest (closest to the root) Cargo.lock if present, otherwise the directory containing
    the shallowest Cargo.toml. Return None if neither is found.
    """
    if lockfiles := list(source_dir.rglob("Cargo.lock")):
        return _shortest_path_parent(lockfiles)

    if tomlfiles := list(source_dir.rglob("Cargo.toml")):
        return _shortest_path_parent(tomlfiles)

    return None


def filter_packages_with_rust_code(packages: list[PipPackage]) -> list[CargoPackageInput]:
    """Filter packages that contain Rust code from a list of pip packages."""
    packages_containing_rust_code = []

    for p in packages:
        # File name and package name may differ e.g. when there is a hyphen in
        # package name it might be replaced by an underscore in a file name.
        if p.path.suffix == WHEEL_FILE_EXTENSION:
            continue

        filename = Path(p.path.name)
        extract_filter = "data" if filename.suffix != ".zip" else None
        while filename.suffix in (".zip", ".tar", ".gz", ".tgz"):
            filename = filename.with_suffix("")

        pip_deps_dir = p.path.parent
        extract_dir = pip_deps_dir / filename
        shutil.unpack_archive(p.path, extract_dir=extract_dir, filter=extract_filter)  # type: ignore[arg-type]

        # The unpacked URL/VCS package may have an arbitrary directory name that we cannot control.
        # Therefore, it is inside a predictable directory derived from the package name.
        nitems = sum(1 for _ in extract_dir.iterdir())
        if nitems != 1:
            # non-standard packaging scheme that doesn't come with a top-level directory
            source_dir = extract_dir
        else:
            source_dir = next(extract_dir.iterdir())

        if not _depends_on_rust(source_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
            continue

        rust_root_dir = _get_rust_root_dir(source_dir)
        if rust_root_dir is None:
            log.warning("No Rust root directory found in %s", source_dir)
            shutil.rmtree(extract_dir, ignore_errors=True)
            continue

        packages_containing_rust_code.append(
            CargoPackageInput(
                type="cargo",
                path=rust_root_dir.relative_to(pip_deps_dir),
            )
        )

    return packages_containing_rust_code


def _config_path(request: Request) -> Path:
    return request.output_dir.join_within_root(".cargo/config.toml").path


def _merge_cargo_config_files(project_files: list[ProjectFile]) -> str:
    """
    Merge cargo config project files into a single TOML template.

    Each cargo config file may contain git dependencies, so the final template must be dynamically
    generated from all Rust extensions in the Python project. Currently, the cargo backend only
    produces config files (no other project files).
    """
    all_sources = {}
    for pf in project_files:
        all_sources.update(tomlkit.parse(pf.template).get("source", {}))

    return tomlkit.dumps({"source": all_sources})


def find_and_fetch_rust_dependencies(
    request: Request, packages_containing_rust_code: list[CargoPackageInput]
) -> RequestOutput:
    """Fetch Rust dependencies for Python packages that contain Rust code."""
    pip_deps_dir = request.output_dir.join_within_root("deps/pip")

    def remove_extracted(packages: list[CargoPackageInput]) -> None:
        """Remove extracted tarballs in the output directory that contain Rust code."""
        for pkg in packages:
            # in case the Rust code was in a subdirectory of the package tarball
            pip_package_root = pkg.path.parts[0]
            shutil.rmtree(pip_deps_dir.join_within_root(pip_package_root), ignore_errors=True)

    if packages_containing_rust_code:
        # Need to swap source for output since this should be happening within output_dir:
        # pip downloads packages to output_dir first, but then these packages have to
        # be processed by cargo, thus output_dir must become source_dir for cargo.
        # Note that output_dir remains the same which results in cargo dependencies being
        # neatly placed right next to pip dependencies.
        cargo_request = request.model_copy(
            update={"packages": packages_containing_rust_code, "source_dir": pip_deps_dir}
        )
        result = fetch_cargo_source(cargo_request)

        template = _merge_cargo_config_files(result.build_config.project_files)
        # A config pointing to deps/cargo directory and an environment variable
        # poiting to the config are necessary for pip to be able to build the extension.
        ev = [EnvironmentVariable(name="CARGO_HOME", value="${output_dir}/.cargo")]
        pf = [ProjectFile(abspath=_config_path(request), template=template)]

        remove_extracted(packages_containing_rust_code)
        return RequestOutput.from_obj_list(
            components=result.components,
            environment_variables=ev + result.build_config.environment_variables,
            project_files=pf,
            options=result.build_config.options,
            annotations=result.annotations,
        )

    return RequestOutput.empty()
