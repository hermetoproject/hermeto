# SPDX-License-Identifier: GPL-3.0-only
import base64
import logging
import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path
from typing import Any, ClassVar, NamedTuple
from urllib.parse import urlparse, urlunparse

import tomlkit
import tomlkit.exceptions
from packageurl import PackageURL
from pydantic import HttpUrl

from hermeto import APP_NAME
from hermeto.core.config import CargoSettings, get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import (
    ExitError,
    LockfileNotFound,
    NotAGitRepo,
    PackageManagerError,
    PackageRejected,
    UnexpectedFormat,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import Annotation, Component, ProjectFile, RequestOutput
from hermeto.core.models.sbom import (
    PROXY_COMMENT,
    PROXY_REF_TYPE,
    ExternalReference,
    create_backend_annotation,
    spdx_now,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import get_repo_id
from hermeto.core.utils import run_cmd

log = logging.getLogger(__name__)


class CargoVendorResult(NamedTuple):
    """
    Vendoring result from running the `cargo vendor` command.
    """

    config_template: str
    lockfile_was_generated: bool


class PackageWithCorruptLockfileRejected(PackageRejected):
    """Package lock file does not match package config."""

    _exit_error: ClassVar[ExitError] = ExitError.ERR_PACKAGE_WITH_CORRUPT_LOCKFILE_REJECTED

    def __init__(self, package_path: str) -> None:
        """Initialize the error."""
        reason = (
            f"{package_path} contains a Cargo.lock that does not match the corresponding Cargo.toml"
        )
        super().__init__(reason, solution=self.default_solution)

    default_solution = (
        "Consider reaching out to maintainer of the dependency in question to address"
        " inconsistencies between Cargo.lock and Cargo.toml"
    )


@dataclass(frozen=True)
class CargoPackage:
    """Represents a package from Cargo.lock file."""

    name: str
    version: str
    source: str | None = None  # [git|registry]+https://github.com/<org>/<package>#[|<sha>]
    checksum: str | None = None
    proxy: HttpUrl | None = None

    @cached_property
    def purl(self) -> PackageURL:
        """Return corresponding package URL."""
        qualifiers = {}
        # depends on https://github.com/hermetoproject/hermeto/issues/852
        if self.checksum is not None:
            qualifiers["checksum"] = self.checksum

        if self.source is not None:
            if self.source.startswith("git+"):
                parsed_url = urlparse(self.source)
                commit_id = parsed_url.fragment
                base_url = urlunparse(parsed_url._replace(query="", fragment=""))
                qualifiers["vcs_url"] = f"{base_url}@{commit_id}"
            elif self.source.startswith("registry+"):
                # Extract registry URL from source (format: "registry+https://...")
                registry_url = self.source.removeprefix("registry+")
                if "crates.io" not in registry_url:
                    qualifiers["repository_url"] = registry_url
            else:
                raise UnexpectedFormat(f"Unable to construct package URL from '{self.source}'.")

        return PackageURL(type="cargo", name=self.name, version=self.version, qualifiers=qualifiers)

    @property
    def _is_proxied(self) -> bool:
        # Custom registries are not proxied, so only those which actually use
        # proxy_url should be reported, vcs_urls are not proxied.
        # Note, that crates.io gets replaced with proxy_url on .cargo/config.toml level
        if self.proxy is None:
            return False
        if self.source is None:
            # This can happen to some Rust dependencies for Python project,
            # e.g. cryptography-cffi@0.1.0. This is not a local package, those
            # are handled elsewhere.
            return True
        if self.source.startswith("git+"):
            return False
        return "crates.io" in self.source

    def to_component(self) -> Component:
        """Convert CargoPackage into SBOM component."""
        ref_rest = dict(type=PROXY_REF_TYPE, comment=PROXY_COMMENT)
        proxy = [ExternalReference(url=str(self.proxy), **ref_rest)] if self._is_proxied else None
        return Component(
            name=self.name,
            version=self.version,
            purl=self.purl.to_string(),
            external_references=proxy,
        )


@dataclass
class LocalCargoPackage:
    """Represents a local dependency in the project or a workspace."""

    name: str
    version: str | None = None
    vcs_url: str | None = None
    subpath: str | None = None

    @cached_property
    def purl(self) -> PackageURL:
        """Return corresponding package URL."""
        qualifiers = {}
        if self.vcs_url is not None:
            qualifiers["vcs_url"] = self.vcs_url
        else:
            # The subpath does not make sense if there is no VCS URL. This usually happens because
            # of missing .git directory in an unpacked tarball that comes from a pip request.
            self.subpath = None

        return PackageURL(
            type="cargo",
            name=self.name,
            version=self.version,
            qualifiers=qualifiers,
            subpath=self.subpath,
        )

    def to_component(self) -> Component:
        """Convert LocalCargoPackage into SBOM component."""
        return Component(name=self.name, version=self.version, purl=self.purl.to_string())


def fetch_cargo_source(request: Request, invoked_through_pip: bool = False) -> RequestOutput:
    """Fetch the source code for all cargo packages specified in a request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []
    annotations: list[Annotation] = []

    for package in request.cargo_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        _verify_lockfile_is_present(package_dir)

        vendor_result = _fetch_dependencies(package_dir, request)
        # cargo allows to specify configuration per-package
        # https://doc.rust-lang.org/cargo/reference/config.html#hierarchical-structure
        if vendor_result.config_template:
            config_template = _swap_sources_directory_for_subsitution_slot(
                vendor_result.config_template
            )
            project_files.append(_use_vendored_sources(package_dir, config_template))
        package_components = _generate_sbom_components(package_dir, request, invoked_through_pip)

        if vendor_result.lockfile_was_generated:
            _update_permissive_mode_annotation(annotations, package_components)

        components.extend(package_components)

    if backend_annotation := create_backend_annotation(components, "cargo"):
        annotations.append(backend_annotation)
    return RequestOutput.from_obj_list(
        components=components,
        project_files=project_files,
        annotations=annotations,
    )


def _update_permissive_mode_annotation(
    annotations: list[Annotation],
    components: list[Component],
) -> None:
    """Update permissive mode SBOM annotation with subjects from the provided components."""
    text = f"{APP_NAME}:permissive-mode:cargo:generated-lockfile"
    subjects = set(c.bom_ref for c in components)
    for annotation in annotations:
        if annotation.text == text:
            annotation.subjects.update(subjects)
            return

    annotations.append(
        Annotation(
            subjects=subjects,
            annotator={"organization": {"name": "red hat"}},
            timestamp=spdx_now(),
            text=text,
        )
    )


def _fetch_dependencies(package_dir: RootedPath, request: Request) -> CargoVendorResult:
    """Fetch cargo dependencies and return a config template for hermetic build."""
    vendor_dir = request.output_dir.join_within_root("deps/cargo")
    # --locked           Assert that `Cargo.lock` will remain unchanged.
    # --versioned-dirs   Always include version in subdir name.
    # --no-delete        Don't delete older crates in the vendor directory.
    #                    It is necessary to make Cargo keep dependencies that are already
    #                    present in the vendored directory. This flag has no effect on standalone
    #                    cargo operations however is crucial when it is invoked from pip.
    # --respect-source-config tells cargo to respect config in .cargo/config.toml in the repository.
    #                         Is necessary when working through a proxy or when custom registries
    #                         must be used.
    cmd = [
        "cargo",
        "vendor",
        "--locked",
        "--versioned-dirs",
        "--no-delete",
        "--respect-source-config",
        str(vendor_dir),
    ]
    log.info("Fetching cargo dependencies at %s", package_dir)
    # NOTE: ordering is important here, a config must be sanitized first, extended to use
    # a proxy after that, otherwise proxy data will be scrubbed by the sanitizer.
    with (
        _sanitized_cargo_config_file(package_dir),
        _inject_proxy_configuration_into_config_if_needed(package_dir),
        _hide_original_cargo_config_from_cargo(package_dir),
    ):
        # Prevent Cargo from invoking rustc
        env = {"CARGO_RESOLVER_INCOMPATIBLE_RUST_VERSIONS": "allow"}
        # The necessary configuration to use the vendored sources will be printed to STDOUT.
        # https://doc.rust-lang.org/cargo/commands/cargo-vendor.html#description
        return _run_cmd_watching_out_for_lock_mismatch(
            cmd=cmd,
            params={"cwd": package_dir, "env": env},
            package_dir=package_dir.path,
        )


def _parse_toml_project_file(path: Path) -> dict[str, Any]:
    """Parse any Cargo related TOML file into a dictionary."""
    parsed_toml = tomlkit.parse(path.read_text())
    return parsed_toml.value


def _resolve_main_package(package_dir: RootedPath) -> tuple[str, str | None]:
    """Resolve package name and version from Cargo.toml."""
    parsed_toml = _parse_toml_project_file(package_dir.path / "Cargo.toml")

    package_info = parsed_toml.get("package", {})
    workspace_info = parsed_toml.get("workspace", {})

    # use default values if the project is a virtual workspace without any package information
    name = package_info.get("name", package_dir.path.stem)
    version = package_info.get("version", None)

    # check for a workspace package version
    # https://doc.rust-lang.org/cargo/reference/workspaces.html#the-package-table
    if version is None:
        version = workspace_info.get("package", {}).get("version")

    return name, version


def _verify_lockfile_is_present(package_dir: RootedPath) -> None:
    """Verify that the Cargo.lock file is present in the package directory."""
    mode = get_config().mode
    lockfile = package_dir.path / "Cargo.lock"
    if lockfile.exists():
        return

    if mode == Mode.PERMISSIVE:
        log.warning("Cargo.lock not found in %s, continuing due to permissive mode", package_dir)
    else:
        raise LockfileNotFound(
            lockfile,
            solution=f"Cargo.lock not found in {package_dir}, run `cargo generate-lockfile` or use permissive mode",
        )


def _create_cargo_config_if_missing_in(package_dir: RootedPath) -> bool:
    p = _path_to_package_config(package_dir)
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    return True


@cache
def _path_to_package_config(package_dir: RootedPath) -> Path:
    # Cargo could be told to use vendored sources instead of a registry via .cargo/config.toml.
    # Prior to cargo v1.39.0 .cargo/config.toml was known as .cargo/config.
    # After v1.39.0 this name was considered obsolete, however .cargo/config would
    # take precedence on .cargo/config.toml if present and the latter one would be ignored.
    # The recommended practice for dealing with a situation when an older build system
    # has to build a more modern project is to symlink .cargo/config.toml to .cargo/config.
    # And vice versa: renaming .cargo/config to .cargo/config.toml would have no effect on
    # any post-2019 toolchain.
    # Refer to https://doc.rust-lang.org/cargo/reference/config.html for further details.
    # Since we could potentially end up building a somewhat stale Rust-based
    # Python extension it is better to check if there is an old-style config present and
    # process it if found.
    cfn = ".cargo/config" if _old_style_config_is_present_in(package_dir) else ".cargo/config.toml"
    return package_dir.join_within_root(Path(cfn)).path


@contextmanager
def _inject_proxy_configuration_into_config_if_needed(
    package_dir: RootedPath,
) -> Generator[None, None, None]:
    hermeto_configuration = get_config().cargo
    if hermeto_configuration.proxy_url is None:
        yield
        return
    config_was_absent = _create_cargo_config_if_missing_in(package_dir)
    package_config = _path_to_package_config(package_dir)
    parsed = tomlkit.parse(package_config.read_text())
    modified_registries_section = _inject_cargo_proxy_registry(parsed.get("registries", {}))
    updated_config = tomlkit.document()
    updated_config["registries"] = modified_registries_section
    updated_config.add("source", {"crates-io": {"replace-with": "cargo-proxy"}})
    # cargo must be told explicitly to use tokens for authentication.
    if hermeto_configuration.proxy_login:
        updated_config["registry"] = {"global-credential-providers": ["cargo:token"]}
    package_config.write_text(tomlkit.dumps(updated_config))
    try:
        yield
    finally:
        if config_was_absent:
            package_config.unlink(missing_ok=True)


@contextmanager
def _hide_original_cargo_config_from_cargo(package_dir: RootedPath) -> Generator[None, None, None]:
    # From https://doc.rust-lang.org/cargo/reference/config.html#hierarchical-structure :
    #
    #   Cargo allows local configuration for a particular package as well as
    #   global configuration. It looks for configuration files in the current
    #   directory and all parent directories.
    #
    # and
    #
    #   If a key is specified in multiple config files, the values will get
    #   merged together.
    #
    # For Hermeto this means that cargo will first consult the sanitized
    # version in <project_dir>/<temporary_source_copy>/.cargo/config.toml, then
    # it will traverse the directory structure up, find the original,
    # unsanitized, .cargo/config.toml there and then will dutifully merge any
    # missing values from it. This was observed with "credential-provider" key
    # being removed by sanitizer only to reappear intact during a test run.
    # Thus it is necessary to temporarily hide the original config and then to
    # restore it back.
    # Furthermore, all configs on the way up the directory tree must be hidden
    # as well since in the case of, for example, Python Rust dependency such
    # config could be located further up the directory structure.

    concealed_identities = []
    # package_dir is a temporary directory containing a modifiable copy of
    # sources, it's parent is the source directory (the one with the original
    # intact package-level cargo config).
    for parent_dir in package_dir.path.parents:
        concealment_candidates = parent_dir / ".cargo/config", parent_dir / ".cargo/config.toml"
        for c_candidate in concealment_candidates:
            if c_candidate.exists():
                new_identity = c_candidate.with_suffix(f"{c_candidate.suffix}.back")
                concealed_identities.append(new_identity)
                os.rename(c_candidate, new_identity)
    try:
        yield
    finally:
        for new_identity in concealed_identities:
            original_identity = new_identity.with_suffix("")
            os.rename(new_identity, original_identity)


@contextmanager
def _sanitized_cargo_config_file(package_dir: RootedPath) -> Generator[None, None, None]:
    """Replace Cargo config file to keep only alternate registry settings.

    The context manager swaps the original config file with one containing only the settings necessary
    to use alternate registries during prefetch session, or hide it completely if it has no registries,
    then restores original config file after prefetch complete.
    """
    # There is a slim chance to find an old project with .cargo/config
    # instead of .cargo/config.toml. If found it has to be hidden too since it still
    # takes precedence over the now standard .cargo/config.toml
    # (https://doc.rust-lang.org/cargo/reference/config.html).
    # Note, that ordering matters here, since .cargo/config could be a symlink
    # to .cargo/config.toml for projects that are built with both old and new versions
    # of Cargo. Unlinking a symlink first is safe.
    all_possible_config_names = (".cargo/config", ".cargo/config.toml")
    configs_contents = []
    processed_paths = set()

    for cfgname in all_possible_config_names:
        config = package_dir.join_within_root(cfgname)
        if config.path.exists():
            data = config.path.read_text()
            sanitized = _sanitize_cargo_config(data)

            if sanitized:
                absolute_path = (
                    config.path.readlink().absolute()
                    if config.path.is_symlink()
                    else config.path.absolute()
                )
                if absolute_path in processed_paths:
                    continue
                processed_paths.add(absolute_path)
                configs_contents.append((config, data))
                config.path.write_text(sanitized)
            else:
                configs_contents.append((config, data))
                config.path.unlink()
    try:
        yield
    finally:
        for config, data in configs_contents:
            if data is not None:
                config.path.write_text(data)


def _make_basic_token_from_proxy_credential(cargo_config: CargoSettings) -> str:
    password = cargo_config.proxy_password
    secret = password.get_secret_value() if password is not None else ""
    credentials = f"{cargo_config.proxy_login}:{secret}"
    token = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return f"Basic {token}"


def _inject_cargo_proxy_registry(partial_cargo_config: dict) -> dict:
    modified_cargo_config = {} if partial_cargo_config is None else partial_cargo_config
    hermeto_config = get_config().cargo
    if (proxy_url := hermeto_config.proxy_url) is not None:
        modified_cargo_config["cargo-proxy"] = {"index": f"sparse+{proxy_url}"}
        if hermeto_config.proxy_login is not None:
            modified_cargo_config["cargo-proxy"] |= {
                "token": _make_basic_token_from_proxy_credential(hermeto_config)
            }
    return modified_cargo_config


def _sanitize_cargo_config(config_content: str) -> str:
    """Extract only the [registries] section from Cargo config, keeping only safe fields.

    Preserves only: index, token, credential-provider fields for each registry.
    Returns sanitized TOML with only registries and their safe fields, or empty string if none exist.
    """
    if not config_content.strip():
        return ""

    try:
        parsed = tomlkit.parse(config_content)
        registries = parsed.get("registries")
    except (tomlkit.exceptions.TOMLKitError, AttributeError):
        raise UnexpectedFormat("Cargo config file contains invalid data and cannot be parsed")

    allowed_fields = {"index", "token", "credential-provider"}
    filtered_registries = tomlkit.table(is_super_table=False)
    sanitized = tomlkit.document()

    if registries is None:
        return ""

    for registry_name, registry_config in registries.items():
        if not isinstance(registry_config, dict):
            continue
        filtered_fields = {}
        for field in registry_config:
            if field in allowed_fields:
                val = registry_config[field]
                val = val.strip() if isinstance(val, str) else val
                filtered_fields[field] = val
        if filtered_fields:
            if "credential-provider" in filtered_fields:
                cprov = filtered_fields["credential-provider"]
                # cargo requires cargo:token for authentication, everything else must be
                # scrubbed since it could end up being an arbitrary executable.
                match cprov:
                    case str():
                        if cprov != "cargo:token":
                            del filtered_fields["credential-provider"]
                        else:
                            pass
                    case list():
                        safe_providers = ["cargo:token"] if "cargo:token" in cprov else []
                        if safe_providers:
                            filtered_fields["credential-provider"] = safe_providers
                        else:
                            del filtered_fields["credential-provider"]
                    case _:
                        # Should be unreachable in practice.
                        raise PackageRejected(
                            f"Unexpected credential-provider type: {type(cprov)} ({cprov})"
                        )
            filtered_registries[registry_name] = filtered_fields

    if filtered_registries:
        sanitized["registries"] = filtered_registries
        return tomlkit.dumps(sanitized)

    return ""


@contextmanager
def _temporary_cwd(path_to_new_cwd: Path) -> Generator[None, None, None]:
    oldcwd = os.getcwd()
    os.chdir(path_to_new_cwd)
    yield
    os.chdir(oldcwd)


def _run_cmd_watching_out_for_lock_mismatch(
    cmd: list, params: dict, package_dir: Path
) -> CargoVendorResult:
    warn_about_imminent_update_to_cargo_lock = (
        f"A mismatch between Cargo.lock and Cargo.toml was detected in {package_dir}. "
        "Because of permissive mode Hermeto will now regenerate Cargo.lock "
        "to match expectation and will try to process the package again. This "
        f"is a violation of reproducibility and must be addressed by {package_dir.name} "
        "maintainers."
    )
    mode = get_config().mode
    update_cargo_lock_cmd = ["cargo", "generate-lockfile"]
    try:
        stdout = run_cmd(cmd=cmd, params=params, suppress_errors=(mode == Mode.PERMISSIVE))
        return CargoVendorResult(config_template=stdout, lockfile_was_generated=False)
    except subprocess.CalledProcessError as e:
        # Search for a very specific failure state to better report it.
        # This is not a robust solution in any way, however it seems to be the only one
        # readily available: cargo returns a generic 101 code on this failure and on multiple
        # others, thus the only way to check for this specific type of failure is to process
        # stderr. Two parts of a string are used to decrease the likelihood of false positives.
        generic_vendor_error = "failed to sync"
        # Since Cargo version 1.93.0, the error message for an unsynchronized lockfile has changed.
        specific_vendor_error_variants = (
            "needs to be updated but --locked was passed",
            "because --locked was passed to prevent this",
        )

        if generic_vendor_error in e.stderr and any(
            error in e.stderr for error in specific_vendor_error_variants
        ):
            if mode == Mode.PERMISSIVE:
                log.warning(warn_about_imminent_update_to_cargo_lock)
                with _temporary_cwd(package_dir):
                    # Extract env from params if present to pass to cargo generate-lockfile
                    env = params.get("env", {})
                    update_cmd_params = {"env": env} if env else {}
                    run_cmd(cmd=update_cargo_lock_cmd, params=update_cmd_params)
                # If it fails here then something else is horribly broken.
                # No more attempts to salvage the situation will be made.
                stdout = run_cmd(cmd=cmd, params=params)
                return CargoVendorResult(config_template=stdout, lockfile_was_generated=True)
            else:
                raise PackageWithCorruptLockfileRejected(str(package_dir))
        else:
            raise PackageManagerError(
                f"Cargo execution failed: `{' '.join(cmd)}` failed with rc={e.returncode}",
                stderr=e.stderr,
            ) from e


def _find_workspace_members(
    package_dir: RootedPath, workspace_info: dict[str, Any]
) -> dict[str, str]:
    """Find cargo workspace members and return their subpaths keyed by crate name.

    Members are paths relative to the workspace root and may be glob patterns. Every
    manifest has to be read because a crate name does not have to match the name of the
    directory the crate lives in.
    https://doc.rust-lang.org/cargo/reference/workspaces.html#the-members-and-exclude-fields
    """
    # An excluded path also excludes everything below it.
    excluded = [Path(path) for path in workspace_info.get("exclude", [])]
    members = {}

    for pattern in workspace_info.get("members", []):
        # A pattern without wildcards matches just itself, so plain paths need no special
        # treatment. Absolute paths are not valid members and Path.glob rejects them.
        if Path(pattern).is_absolute():
            continue

        for member_path in sorted(package_dir.path.glob(pattern)):
            subpath = member_path.relative_to(package_dir.path)
            if any(subpath.is_relative_to(path) for path in excluded):
                continue

            manifest = package_dir.join_within_root(subpath, "Cargo.toml")
            if not manifest.path.is_file():
                continue

            parsed_manifest = _parse_toml_project_file(manifest.path)
            # A directory that defines its own workspace is a separate workspace root,
            # cargo does not treat it as a member of the outer one.
            if "workspace" in parsed_manifest:
                continue

            if name := parsed_manifest.get("package", {}).get("name"):
                # Package URL subpaths are always separated with a forward slash.
                members[name] = subpath.as_posix()

    return members


def _find_local_packages(package_dir: RootedPath) -> dict[str, str]:
    """Find local packages in the Cargo.toml file and return their subpaths."""
    parsed_toml = _parse_toml_project_file(package_dir.path / "Cargo.toml")

    # Explicit path dependencies take precedence over workspace members: the same crate
    # can be reached both ways and the declared path is the more specific answer.
    result = _find_workspace_members(package_dir, parsed_toml.get("workspace", {}))

    runtime_deps = parsed_toml.get("dependencies", {})
    # Patched dependencies are used to override crates.io dependencies with local versions.
    # This is useful for development purposes or quick bug fixes.
    # https://doc.rust-lang.org/cargo/reference/overriding-dependencies.html
    patched_deps = parsed_toml.get("patch", {}).get("crates-io", {})
    all_deps = {**runtime_deps, **patched_deps}

    for name, dep_info in all_deps.items():
        if isinstance(dep_info, dict) and "path" in dep_info:
            result[name] = dep_info["path"]

    return result


def _generate_sbom_components(
    package_dir: RootedPath,
    request: Request,
    invoked_through_pip: bool = False,
) -> list[Component]:
    """Generate SBOM components from Cargo.lock and for the main package."""
    parsed_lockfile = _parse_toml_project_file(package_dir.path / "Cargo.lock")

    all_packages = parsed_lockfile.get("package", [])
    local_packages = _find_local_packages(package_dir)
    main_package_name, main_package_version = _resolve_main_package(package_dir)

    # When cargo is invoked from pip for extracted sdists, the source directory is
    # swapped to point at the output directory. Check if source_dir is inside output_dir
    # to detect this scenario, where we can't expect a git repository (and flip the boolean
    # for readbility).
    source_is_outside_output = not request.source_dir.path.is_relative_to(request.output_dir.path)

    # Missing git repo is tolerated in two independent cases:
    # 1. PERMISSIVE mode: validation is relaxed
    # 2. Nested PM (pip->cargo for sdists): source_dir is inside output_dir,
    #    so missing git is expected even in STRICT mode
    vcs_url = None
    try:
        vcs_url = get_repo_id(package_dir.root).as_vcs_url_qualifier()
    except NotAGitRepo:
        if get_config().mode != Mode.PERMISSIVE and source_is_outside_output:
            raise

    components = []

    for pkg in all_packages:
        pkg_name = pkg.get("name")
        pkg_version = pkg.get("version")

        if pkg_name == main_package_name:
            if invoked_through_pip:
                # The package was collected as a part of processing a python dependency,
                # it has been already reported when collected with pip, so here it must
                # be ignored.
                pass
            else:
                components.append(
                    LocalCargoPackage(
                        name=main_package_name,
                        version=main_package_version,
                        vcs_url=vcs_url,
                        subpath=str(package_dir.path.relative_to(package_dir.root)),
                    ).to_component()
                )

        elif pkg_name in local_packages:
            # Local packages have no other fields in the Cargo.lock file besides the name and version.
            components.append(
                LocalCargoPackage(
                    name=pkg_name,
                    version=pkg_version,
                    vcs_url=vcs_url,
                    subpath=local_packages.get(pkg_name),
                ).to_component()
            )
        else:
            components.append(
                CargoPackage(
                    name=pkg_name,
                    version=pkg_version,
                    source=pkg.get("source"),
                    checksum=pkg.get("checksum"),
                    proxy=get_config().cargo.proxy_url,
                ).to_component()
            )

    return components


def _swap_sources_directory_for_subsitution_slot(template: str) -> dict:
    toml_template = tomlkit.parse(template).value
    # Absolute path has to be replaced with relative path for sources relocation to work:
    toml_template["source"]["vendored-sources"]["directory"] = "${output_dir}/deps/cargo"
    # A correct output_dir value will be supplied by the application during a later stage.
    return toml_template


def _old_style_config_is_present_in(package_dir: RootedPath) -> bool:
    return (package_dir.path / ".cargo/config").exists()


def _use_vendored_sources(package_dir: RootedPath, config_template: dict) -> ProjectFile:
    """Make sure cargo will use the vendored sources when building the project."""
    config_path = _path_to_package_config(package_dir)

    merged_content = _parse_toml_project_file(config_path) if config_path.exists() else {}
    merged_content.update(config_template)
    return ProjectFile(abspath=config_path, template=tomlkit.dumps(merged_content))
