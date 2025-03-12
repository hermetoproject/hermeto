from typing import Iterable

from hermeto.core.models.output import RequestOutput


def merge_outputs(outputs: Iterable[RequestOutput]) -> RequestOutput:
    """Merge RequestOutput instances."""
    components = []
    env_vars = []
    project_files = []

    for output in outputs:
        components.extend(output.components)
        env_vars.extend(output.build_config.environment_variables)
        project_files.extend(output.build_config.project_files)

    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=env_vars,
        project_files=project_files,
        options=output.build_config.options if output.build_config.options else None,
    )
