# SPDX-License-Identifier: GPL-3.0-only
from hermeto.core.models.input import Request
from hermeto.core.models.output import Component, EnvironmentVariable, ProjectFile, RequestOutput


def fetch_uv_source(request: Request) -> RequestOutput:
    """Resolve and fetch uv dependencies for the given request."""
    components: list[Component] = []
    environment_variables: list[EnvironmentVariable] = []
    project_files: list[ProjectFile] = []

    for package in request.uv_packages:
        pass

    return RequestOutput.from_obj_list(components, environment_variables, project_files)
