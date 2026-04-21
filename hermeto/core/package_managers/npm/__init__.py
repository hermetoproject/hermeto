# SPDX-License-Identifier: GPL-3.0-only
from hermeto.core.package_managers.npm.main import fetch_npm_source
from hermeto.core.package_managers.npm.utils import NPM_REGISTRY_CNAMES

__all__ = ["fetch_npm_source", "NPM_REGISTRY_CNAMES"]
