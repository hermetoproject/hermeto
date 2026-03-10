# SPDX-License-Identifier: GPL-3.0-only
from hermeto.core.models.input import Request
from hermeto.core.models.output import ProjectFile, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.npm import project as npm_project
from hermeto.core.package_managers.npm import resolver as npm_resolver

_resolve_npm = npm_resolver._resolve_npm


def _generate_component_list(
    component_infos: list[npm_project.NpmComponentInfo],
) -> list[Component]:
    """Convert a list of NpmComponentInfo objects into a list of Component objects for the SBOM."""

    def to_component(component_info: npm_project.NpmComponentInfo) -> Component:
        if component_info["missing_hash_in_file"]:
            missing_hash = frozenset({str(component_info["missing_hash_in_file"])})
        else:
            missing_hash = frozenset()

        return Component(
            name=component_info["name"],
            version=component_info["version"],
            purl=component_info["purl"],
            properties=PropertySet(
                npm_bundled=component_info["bundled"],
                npm_development=component_info["dev"],
                missing_hash_in_file=missing_hash,
            ).to_properties(),
            external_references=component_info["external_refs"],
        )

    return [to_component(component_info) for component_info in component_infos]


def fetch_npm_source(request: Request) -> RequestOutput:
    """Resolve and fetch npm dependencies for the given request.

    :param request: the request to process
    :return: A RequestOutput object with content for all npm packages in the request
    """
    component_info: list[npm_project.NpmComponentInfo] = []
    project_files: list[ProjectFile] = []

    npm_deps_dir = request.output_dir.join_within_root("deps", "npm")
    npm_deps_dir.path.mkdir(parents=True, exist_ok=True)

    for package in request.npm_packages:
        info = _resolve_npm(request.source_dir.join_within_root(package.path), npm_deps_dir)
        component_info.append(info["package"])

        for dependency in info["dependencies"]:
            component_info.append(dependency)

        for projectfile in info["projectfiles"]:
            project_files.append(projectfile)

    components = _generate_component_list(component_info)
    annotations = []
    if backend_annotation := create_backend_annotation(components, "npm"):
        annotations.append(backend_annotation)
    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=[],
        project_files=project_files,
        annotations=annotations,
    )
