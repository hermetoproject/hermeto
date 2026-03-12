# SPDX-License-Identifier: GPL-3.0-only
from hermeto.core.package_managers.npm.fetch import (  # noqa: F401
    _async_download_tar,
    _clone_repo_pack_archive,
    _get_npm_dependencies,
    _patch_url_to_point_to_a_proxy,
)
from hermeto.core.package_managers.npm.main import (  # noqa: F401
    _generate_component_list,
    _resolve_npm,
    fetch_npm_source,
)
from hermeto.core.package_managers.npm.package_lock import (  # noqa: F401
    DEPENDENCY_TYPES,
    NPM_REGISTRY_CNAMES,
    NormalizedUrl,
    NpmComponentInfo,
    Package,
    PackageLock,
    ResolvedNpmPackage,
    _classify_resolved_url,
    _extract_git_info_npm,
    _normalize_resolved_url,
    _Purlifier,
    _update_vcs_url_with_full_hostname,
)
from hermeto.core.package_managers.npm.project_files import (  # noqa: F401
    _should_replace_dependency,
    _update_package_json_files,
    _update_package_lock_with_local_paths,
)
