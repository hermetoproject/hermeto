# SPDX-License-Identifier: GPL-3.0-only
from hermeto.core.package_managers.javascript.npm.main import fetch_npm_source
from hermeto.core.package_managers.javascript.npm.resolver import (
    async_download_with_auth,
    patch_url_to_point_to_proxy,
)
from hermeto.core.package_managers.javascript.npm.utils import (
    NPM_REGISTRY_URL,
    YARN_REGISTRY_URL,
    is_from_npm_registry,
)

__all__ = [
    "fetch_npm_source",
    "NPM_REGISTRY_URL",
    "YARN_REGISTRY_URL",
    "is_from_npm_registry",
    "async_download_with_auth",
    "patch_url_to_point_to_proxy",
]
