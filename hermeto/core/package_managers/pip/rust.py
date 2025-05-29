"""This module provides functionality to handle Rust extensions in Python packages."""

import logging
import shutil
import tarfile
from pathlib import Path
from textwrap import dedent
from typing import Any

from pybuild_deps import parsers

from hermeto.core.models.input import CargoPackageInput, Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.package_managers.cargo import fetch_cargo_source

log = logging.getLogger(__name__)


def _has_rust_build_deps(raw_build_dependencies: list[str]) -> bool:
    rust_build_deps = ("maturin", "setuptools-rust", "setuptools_rust")
    for dep in raw_build_dependencies:
        if dep.strip().lower().startswith(rust_build_deps):
            return True
    return False


def _depends_on_rust(source_tarball: tarfile.TarFile) -> bool:
    file_parser_map = {
        "pyproject.toml": parsers.parse_pyproject_toml,
        "setup.cfg": parsers.parse_setup_cfg,
        "setup.py": parsers.parse_setup_py,
    }
    for file_name, parser in file_parser_map.items():
        pkg_name = source_tarball.getnames()[0].split("/")[0]
        try:
            file = source_tarball.extractfile(f"{pkg_name}/{file_name}")
        except KeyError:
            continue
        # the file is decoded as utf-8-sig because plain utf-8 has proven to
        # be problematic with certain sources from pypi
        # see https://github.com/hermetoproject/pybuild-deps/blob/4dc40ffabddb8aad1279978b8741111fb64452e6/src/pybuild_deps/finder.py#L45-L51
        # mypy: it thinks file type is "IO[bytes] | None", but that's not right as .extractfile won't return None.
        file_contents = file.read().decode("utf-8-sig")  # type: ignore
        try:
            build_dependencies = parser(file_contents)
        except parsers.SetupPyParsingError:
            # unfortunately pybuild-deps parser has some known edge-cases for older packages
            # relying on setup.py
            log.error("Unable to parse build dependencies for %s.", pkg_name)
            continue
        if _has_rust_build_deps(build_dependencies):
            return True
    return False


def filter_packages_with_rust_code(packages: list[dict[str, Any]]) -> list[CargoPackageInput]:
    """Filter packages that contain Rust code from a list of pip packages."""
    packages_containing_rust_code = []
    tar_packages = [p for p in packages if tarfile.is_tarfile(p.get("path", ""))]
    for p in tar_packages:
        fname = p.get("path", "")
        # File name and package name may differ e.g. when there is a hyphen in
        # package name it might be replaced by an underscore in a file name.
        pname = Path(Path(fname).name)
        while pname.suffix in (".tar", ".gz", ".tgz"):
            pname = pname.with_suffix("")
        tf = tarfile.open(fname)
        toplevel_cargo = f"{pname}/Cargo.toml"
        try:
            tf.getmember(toplevel_cargo)
            rust_root = pname
        except KeyError:
            # only skip if no Cargo.toml is present in the package
            if not any([fname.endswith("Cargo.toml") for fname in tf.getnames()]):
                continue
            # considering it has a Cargo.toml, let's check if it depends on the typical toolchain
            # for python+rust to rule out false positives
            if not _depends_on_rust(tf):
                continue
            # find the top-most Cargo.toml in the package - that's not necessarily the most accurate
            # solution, but this heuristic has proven to work on the most popular python packages
            # that have rust dependencies; if this stops working, then we would probably need to check
            # pyproject toml config section for maturin or setuptools-rust...
            # More info on this issue in the design doc
            # https://github.com/hermetoproject/hermeto/blob/e5fa5c0fcd0dff62cf02be5b0d219e04c1ea440c/docs/design/cargo-support.md#L806
            cargo_manifests = [name for name in tf.getnames() if name.endswith("Cargo.toml")]
            rust_root = Path(sorted(cargo_manifests, key=len)[0]).parent

        tf.extractall(path=Path(str(tf.name)).parent, filter="data")
        packages_containing_rust_code.append(CargoPackageInput(type="cargo", path=rust_root))

    return packages_containing_rust_code


def _config_data() -> str:
    return dedent(
        """
        [source.crates-io]
        replace-with = "local"

        [source.local]
        directory = "${output_dir}/deps/cargo"
        """
    )


def _config_path(request: Request) -> Path:
    return request.output_dir.join_within_root(".cargo/config.toml").path


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

        # A config pointing to deps/cargo directory and an environment variable
        # poiting to the config are necessary for pip to be able to build the extension.
        ev = [EnvironmentVariable(name="CARGO_HOME", value="${output_dir}/.cargo")]
        pf = [ProjectFile(abspath=_config_path(request), template=_config_data())]

        remove_extracted(packages_containing_rust_code)
        return result + RequestOutput.from_obj_list([], ev, pf)

    return RequestOutput.from_obj_list([], [], [])
