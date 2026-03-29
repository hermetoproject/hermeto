# SPDX-License-Identifier: GPL-3.0-only
from hermeto.core.errors import UnsupportedFeature
from hermeto.core.models.input import Request
from hermeto.core.models.output import RequestOutput
from hermeto.core.package_managers.uv.lockfile import parse_uv_lockfile


def fetch_uv_source(request: Request) -> RequestOutput:
    """Validate uv inputs and lockfiles for the experimental uv backend."""
    for package in request.uv_packages:
        package_dir = request.source_dir.join_within_root(package.path)
        parse_uv_lockfile(package_dir)

    raise UnsupportedFeature(
        "The experimental 'x-uv' backend is wired, but dependency fetching is not implemented yet.",
        solution="Follow docs/design/uv.md for the implementation roadmap.",
    )
