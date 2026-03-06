# SPDX-License-Identifier: GPL-3.0-only
import logging
import os
from pathlib import Path
from textwrap import dedent

from packageurl import PackageURL

from hermeto import APP_NAME
from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, PackageRejected, UnsupportedFeature
from hermeto.core.models.input import BundlerBinaryFilters, Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.bundler.parser import (
    GemPlatformSpecificDependency,
    GitDependency,
    ParseResult,
    PathDependency,
    parse_lockfile,
)
from hermeto.core.package_managers.general import get_vcs_qualifiers
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import get_repo_id

log = logging.getLogger(__name__)

CONFIG_OVERRIDE = "bundler/config_override"


def fetch_bundler_source(request: Request) -> RequestOutput:
    """Resolve and process all bundler packages."""
    components: list[Component] = []
    environment_variables: list[EnvironmentVariable] = (
        _prepare_environment_variables_for_hermetic_build()
    )
    project_files: list[ProjectFile] = []
    git_paths = []

    for package in request.bundler_packages:
        path_within_root = request.source_dir.join_within_root(package.path)
        _comps, _git_paths = _resolve_bundler_package(
            package_dir=path_within_root,
            output_dir=request.output_dir,
            binary_filters=package.binary,
        )
        components.extend(_comps)
        git_paths.extend(_git_paths)
    project_files.append(
        _prepare_for_hermetic_build(request.source_dir, request.output_dir, git_paths)
    )

    annotations = []
    if backend_annotation := create_backend_annotation(components, "bundler"):
        annotations.append(backend_annotation)
    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=environment_variables,
        project_files=project_files,
        annotations=annotations,
    )


# Aliases for git dependency name and git dependency name as
# it is written to file system:
DepName = str
FSDepName = str


def _resolve_bundler_package(
    package_dir: RootedPath,
    output_dir: RootedPath,
    binary_filters: BundlerBinaryFilters | None = None,
) -> tuple[list[Component], list[tuple[DepName, FSDepName]]]:
    """Process a request for a single bundler package."""
    deps_dir = output_dir.join_within_root("deps", "bundler")
    deps_dir.path.mkdir(parents=True, exist_ok=True)
    dependencies = parse_lockfile(package_dir, binary_filters)

    name, version = _get_main_package_name_and_version(package_dir, dependencies)
    try:
        qualifiers = get_vcs_qualifiers(package_dir.root)
    except NotAGitRepo:
        if get_config().mode == Mode.STRICT:
            raise
        qualifiers = None
    main_package_purl = PackageURL(
        type="gem",
        name=name,
        version=version,
        qualifiers=qualifiers,
        subpath=str(package_dir.subpath_from_root),
    )

    components = [Component(name=name, version=version, purl=main_package_purl.to_string())]
    git_paths = []
    for dep in dependencies:
        dep.download_to(deps_dir)
        if isinstance(dep, GemPlatformSpecificDependency):
            properties = PropertySet(bundler_package_binary=True).to_properties()
        else:
            properties = []
        if isinstance(dep, GitDependency):
            git_paths.append((dep.name, dep.repo_name + "-" + dep.ref[:12]))

        c = Component(name=dep.name, version=dep.version, purl=dep.purl, properties=properties)
        components.append(c)

    return components, git_paths


def _get_main_package_name_and_version(
    package_dir: RootedPath,
    dependencies: ParseResult,
) -> tuple[str, str | None]:
    """
    Get main package name and version.

    The main package is the package that is being processed by our application.
    Not any of its dependencies.
    """
    name_and_version = _get_name_and_version_from_lockfile(dependencies)
    if name_and_version is not None:
        return name_and_version

    # fallback to origin remote
    try:
        name = _get_repo_name_from_origin_remote(package_dir)
    # if the git repository does not have an origin remote
    except UnsupportedFeature:
        raise PackageRejected(
            reason="Failed to extract package name from origin remote",
            solution=(
                f"Please specify package name and version in a way that {APP_NAME} understands,\n"
                f"or make sure that the directory {APP_NAME} is processing is a git repository with\n"
                f"an 'origin' remote, in which case {APP_NAME} will infer the package name from the remote URL."
            ),
        )

    return name, None


def _get_name_and_version_from_lockfile(dependencies: ParseResult) -> tuple[str, str] | None:
    """
    Extract the package name and version from dependencies in the Gemfile.lock.

    Gemfile.lock only contains the name and version of the package. If the gemspec file
    is explicitly defined in the Gemfile, Bundler will create a path dependency record
    representing the gem in the package directory.

    Note that having a gemspec file is an edge case when the package is not an actual gem.
    But it is possible to include a gemspec file in the package directory that defines its name,
    version, and other metadata even if the package is not a gem. So we respect this edge case.

    See design doc for more details:
    https://github.com/hermetoproject/hermeto/blob/main/docs/design/bundler.md
    """
    for dep in dependencies:
        if isinstance(dep, PathDependency) and dep.subpath == ".":
            return dep.name, dep.version

    return None


def _get_repo_name_from_origin_remote(package_dir: RootedPath) -> str:
    """Extract repository name from git origin remote in the package directory."""
    try:
        repo_id = get_repo_id(package_dir.root)
    except NotAGitRepo:
        raise PackageRejected(
            reason="Unable to infer package name from origin URL",
            solution=(
                "Provide valid metadata in the package files or ensure "
                "the package files are in a git repository whose 'origin' remote has a valid URL."
            ),
        )

    repo_name = Path(repo_id.parsed_origin_url.path).stem
    resolved_path = Path(repo_name).joinpath(package_dir.subpath_from_root)
    return str(resolved_path)


def _prepare_environment_variables_for_hermetic_build() -> list[EnvironmentVariable]:
    return [
        # Contains path to a directory where a new config could be found.
        EnvironmentVariable(name="BUNDLE_APP_CONFIG", value="${output_dir}/" + CONFIG_OVERRIDE),
    ]


def _prepare_for_hermetic_build(
    source_dir: RootedPath, output_dir: RootedPath, git_paths: list | None = None
) -> ProjectFile:
    """Prepare a package for hermetic build by injecting necessary config."""
    potential_bundle_config = source_dir.join_within_root(".bundle/config").path
    hermetic_config = dedent(
        """
        BUNDLE_CACHE_PATH: "${output_dir}/deps/bundler"
        BUNDLE_DEPLOYMENT: "true"
        BUNDLE_NO_PRUNE: "true"
        BUNDLE_ALLOW_OFFLINE_INSTALL: "true"
        BUNDLE_DISABLE_VERSION_CHECK: "true"
        BUNDLE_VERSION: "system"
    """
    )
    # Note: if a package depends on a git revision then the following variables
    # are necessary for a hermetic build:
    #  BUNDLE_DISABLE_LOCAL_BRANCH_CHECK
    #  BUNDLE_DISABLE_LOCAL_REVISION_CHECK
    # because otherwise some (potentially all, depending on exact set of
    # ecosystem components versions, environment variables and celestial
    # alignment) Bundler versions will try to fetch the latest changes of the
    # remotes which may be present even when instructed not to with --local
    # flag.
    # See https://bundler.io/guides/git.html#local-git-repos for details.
    # (or https://github.com/rubygems/bundler-site/blob/
    #             9ff3b76e9866524ecefe165633ffb547f0004a99/source/guides/git.html.md
    # if the link above ceases to exist).
    if git_paths is not None:
        hermetic_config += 'BUNDLE_DISABLE_LOCAL_BRANCH_CHECK: "true"\n'
        hermetic_config += 'BUNDLE_DISABLE_LOCAL_REVISION_CHECK: "true"\n'
        for packname, dirname in git_paths:
            # "-" in variable names is deprecated in Bundler and now generates
            # a warning and a suggestion to replace all dashes with triple
            # underscores. Package names sometimes contain dashes:
            varname = "BUNDLE_LOCAL." + packname.upper().replace("-", "___")
            location = "${output_dir}/deps/bundler/" + dirname
            config_entry = varname + f': "{location}"'
            hermetic_config += f"{config_entry}\n"
    if potential_bundle_config.is_file():
        config_data = potential_bundle_config.read_text()
        config_data += hermetic_config
    elif (alternative_config := os.getenv("BUNDLE_APP_CONFIG")) is not None:
        # Corner case: a user decides to define their own alternate config.
        # In this scenario the application must try to copy over user-defined variables
        # to its overriding alternate config.
        config_data = Path(alternative_config, "config").read_text()
        config_data += hermetic_config
    else:
        config_data = hermetic_config
    overriding_bundler_config_path = output_dir.join_within_root(CONFIG_OVERRIDE, "config").path
    return ProjectFile(abspath=overriding_bundler_config_path, template=config_data)
