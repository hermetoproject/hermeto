# SPDX-License-Identifier: GPL-3.0-only
# Temporary compatibility shim — replaced commit by commit.
from hermeto.core.package_managers.npm._npm_legacy import (  # noqa: F401
    DEPENDENCY_TYPES,
    NPM_REGISTRY_CNAMES,
    NormalizedUrl,
    NpmComponentInfo,
    Package,
    PackageLock,
    ResolvedNpmPackage,
    _classify_resolved_url,
    _extract_git_info_npm,
    _generate_component_list,
    _normalize_resolved_url,
    _Purlifier,
    _resolve_npm,
    _update_vcs_url_with_full_hostname,
    create_backend_annotation,
    fetch_npm_source,
    get_config,
    get_repo_id,
)
from hermeto.core.package_managers.npm.fetch import (  # noqa: F401
    _async_download_tar,
    _clone_repo_pack_archive,
    _get_npm_dependencies,
    _patch_url_to_point_to_a_proxy,
    async_download_files,
    clone_as_tarball,
    must_match_any_checksum,
)
from hermeto.core.package_managers.npm.project_files import (  # noqa: F401
    _should_replace_dependency,
    _update_package_json_files,
    _update_package_lock_with_local_paths,
)
