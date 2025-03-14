import logging
import string
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Set

import pydantic

from cachi2.core.errors import BaseError
from cachi2.core.models.sbom import Component, Sbom, merge_component_properties
from cachi2.core.models.validators import unique_sorted

log = logging.getLogger(__name__)


class EnvironmentVariable(pydantic.BaseModel):
    """An environment variable high-level representation.

    An environment variable is represented as a string template that is evaluated (i.e.
    placeholders substituted) right before dumping the actual scalar value of the environment
    variable to the output.
    Templating as the base solution for this representation is useful in cases where the exact
    value of a given environment variable can't be determined at the time of instantiation.

    Note that legacy implementation of this model differentiated between 2 kinds of variables:
    'path' & 'literal' with the following behaviour:
        - for "literal" variables, the resolved value is simply the value it was created with
        - for "path" variables, the value is joined to the specified path.

    The new implementation is backwards compatible with the legacy handling in terms of input
    parsing, but the produced output is not.
    """

    name: str
    value: str
    kind: Optional[Literal["literal", "path"]] = pydantic.Field(default=None, exclude=True)

    def resolve_value(self, mappings: Dict[str, str]) -> str:
        """Return the resolved value of this templated environment variable.

        :param mappings: dictionary of template mappings to substitute

        The environment variable value will be converted to a string template which will then
        substitute all placeholders defined; if no placeholders are contained within the value
        string, substitution is a NOOP (e.g. legacy "literal" variables)
        """

        def get_placeholders(t: string.Template) -> Set[str]:
            """Return a set of placeholders in a template.

            Implementation is based on [1] which appeared in 3.11 [2] without additional error
            handling which we don't need for our mostly internal use case.
            [1] https://github.com/python/cpython/blob/3b4cd48d2988e74405838accde5edcc3b71bec48/Lib/string.py#L157
            [2] https://docs.python.org/3.11/library/string.html#string.Template.get_identifiers

            TODO: Drop this after bumping minimum required version to 3.11
            """
            placeholders = set()
            matches = t.pattern.finditer(t.template)
            for m in matches:
                if placeholder := m.group("named") or m.group("braced"):
                    placeholders.add(placeholder)

            return placeholders

        # legacy path variable handling, need to prepend the base path placeholder
        if self.kind == "path" and "output_dir" in mappings:
            log.debug(f"Adjusting a legacy path variable value '{self.name}={self.value}'")
            self.value = "${output_dir}/" + self.value

        # "Recursively" resolve potentially nested variables up to len(mappings) tries
        log.debug(f"Resolving environment variable '{self.name}={self.value}'")
        ret = self.value
        substituted: Set[str] = set()
        for i, m in enumerate(mappings):
            log.debug(f"EnvironmentVariable resolution iteration {i + 1}.: {ret}")

            t_old = string.Template(ret)
            ret = t_old.safe_substitute(mappings)
            t_new = string.Template(ret)
            p_new = get_placeholders(t_new)

            substituted |= get_placeholders(t_old) & mappings.keys()
            if substituted & p_new:
                raise BaseError(
                    f"Detected a cycle in environment variable expansion of '{self.name}'",
                    solution=(
                        "Inspect all relevant environment variables and make sure their "
                        "expansion doesn't lead to a cycle. Ideally, avoiding nesting of "
                        "variables altogether."
                    ),
                )

        return ret


class ProjectFile(pydantic.BaseModel):
    """A file to be written into the user's project directory.

    Typically should be used to inject configuration files (e.g. .npmrc) or to modify lockfiles
    (e.g. requirements.txt).

    The content of the file is interpreted as a string.Template. The following placeholders will
    be replaced:
        * ${output_dir} - the absolute path to the output directory
    """

    abspath: Path
    template: str

    def resolve_content(self, output_dir: Path) -> str:
        """Return the resolved content of this file.

        Uses Template.safe_substitute, so if the template contains invalid placeholders,
        they will simply stay unresolved rather than causing errors.

        Example:
            foo @ file://${output_dir}/deps/pip/...
            bar==1.0.0  # comment with $placeholder
            baz==1.0.0  # comment with $ invalid placeholder

            =>
            foo @ file:///hermeto/output/deps/pip/...
            bar==1.0.0  # comment with $placeholder
            baz==1.0.0  # comment with $ invalid placeholder
        """
        template = string.Template(self.template)
        return template.safe_substitute(output_dir=output_dir)


class BuildConfig(pydantic.BaseModel):
    """Holds output used to configure a repository for a build."""

    environment_variables: list[EnvironmentVariable] = []
    project_files: list[ProjectFile] = []
    options: Optional[Dict[str, Any]] = None

    @pydantic.field_validator("environment_variables")
    def _unique_env_vars(cls, env_vars: list[EnvironmentVariable]) -> list[EnvironmentVariable]:
        """Sort and de-duplicate environment variables by name."""
        return unique_sorted(env_vars, by=lambda env_var: env_var.name)

    @pydantic.field_validator("project_files")
    def _unique_project_files(cls, project_files: list[ProjectFile]) -> list[ProjectFile]:
        """Sort and de-duplicate project files by path."""
        return unique_sorted(project_files, by=lambda f: f.abspath)


class RequestOutput(pydantic.BaseModel):
    """Results of processing one or more package managers."""

    components: list[Component]
    build_config: BuildConfig

    def generate_sbom(self) -> Sbom:
        """Generate the SBOM for this RequestOutput.

        Note that RequestOutput may contain duplicated components, we de-duplicate them here
        while merging their `properties`.
        """
        return Sbom(components=merge_component_properties(self.components))

    @classmethod
    def empty(cls) -> "RequestOutput":
        """Return an empty RequestOutput."""
        return cls(components=[], build_config=BuildConfig())

    @classmethod
    def from_obj_list(
        cls,
        components: list[Component],
        environment_variables: Optional[list[EnvironmentVariable]] = None,
        project_files: Optional[list[ProjectFile]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> "RequestOutput":
        """Create a RequestOutput from components, environment variables and project files."""
        if environment_variables is None:
            environment_variables = []

        if project_files is None:
            project_files = []

        return RequestOutput(
            components=components,
            build_config=BuildConfig(
                environment_variables=environment_variables,
                project_files=project_files,
                options=options,
            ),
        )
