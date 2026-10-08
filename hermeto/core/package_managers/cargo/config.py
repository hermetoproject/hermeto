# SPDX-License-Identifier: GPL-3.0-only
import base64
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from functools import cache
from pathlib import Path

import tomlkit
import tomlkit.exceptions

from hermeto.core.config import CargoSettings, get_config
from hermeto.core.errors import PackageRejected, UnexpectedFormat
from hermeto.core.models.output import ProjectFile
from hermeto.core.package_managers.cargo.resolver import _parse_toml_project_file
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


def _old_style_config_is_present_in(package_dir: RootedPath) -> bool:
    return (package_dir.path / ".cargo/config").exists()


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


def _create_cargo_config_if_missing_in(package_dir: RootedPath) -> bool:
    p = _path_to_package_config(package_dir)
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    return True


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


def _swap_sources_directory_for_subsitution_slot(template: str) -> dict:
    toml_template = tomlkit.parse(template).value
    # Absolute path has to be replaced with relative path for sources relocation to work:
    toml_template["source"]["vendored-sources"]["directory"] = "${output_dir}/deps/cargo"
    # A correct output_dir value will be supplied by the application during a later stage.
    return toml_template


def _use_vendored_sources(package_dir: RootedPath, config_template: dict) -> ProjectFile:
    """Make sure cargo will use the vendored sources when building the project."""
    config_path = _path_to_package_config(package_dir)

    merged_content = _parse_toml_project_file(config_path) if config_path.exists() else {}
    merged_content.update(config_template)
    return ProjectFile(abspath=config_path, template=tomlkit.dumps(merged_content))
