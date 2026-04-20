# SPDX-License-Identifier: GPL-3.0-only
from hermeto.core.models.input import Request
from hermeto.core.models.output import Annotation, Component, ProjectFile, RequestOutput
from hermeto.core.package_managers.javascript.pnpm.project import PnpmLock, parse_packages


def fetch_pnpm_source(request: Request) -> RequestOutput:
    """Process all pnpm source directories in the given request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []
    annotations: list[Annotation] = []

    for package in request.pnpm_packages:
        project_dir = request.source_dir.join_within_root(package.path)
        lockfile = PnpmLock.from_dir(project_dir.path)
        parse_packages(lockfile)

    return RequestOutput.from_obj_list(
        components=components,
        project_files=project_files,
        annotations=annotations,
    )
