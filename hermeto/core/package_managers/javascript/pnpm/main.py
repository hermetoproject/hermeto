# SPDX-License-Identifier: GPL-3.0-only
from hermeto.core.models.input import Request
from hermeto.core.models.output import Annotation, Component, ProjectFile, RequestOutput


def fetch_pnpm_source(request: Request) -> RequestOutput:  # noqa
    """Process all pnpm source directories in the given request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []
    annotations: list[Annotation] = []

    return RequestOutput.from_obj_list(
        components=components,
        project_files=project_files,
        annotations=annotations,
    )
