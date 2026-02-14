import copy
import json
import logging
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import parse_qs

import semver
import yaml

from hermeto import APP_NAME
from hermeto.core.errors import (
    LockfileNotFound,
    PackageManagerError,
    PackageRejected,
    UnexpectedFormat,
    UnsupportedFeature,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import (
    Component,
    EnvironmentVariable,
    ProjectFile,
    RequestOutput,
)
from hermeto.core.package_managers.js_utils import clone_repo_pack_archive

# The public parse_locator rejects git locators; we need the lower-level
# parser to inspect them for git dependency detection.
from hermeto.core.package_managers.yarn.locators import _parse_locator
from hermeto.core.package_managers.yarn.project import (
    Plugin,
    Project,
    YarnRc,
    get_semver_from_package_manager,
    get_semver_from_yarn_path,
)
from hermeto.core.package_managers.yarn.resolver import create_components, resolve_packages
from hermeto.core.package_managers.yarn.utils import (
    VersionsRange,
    extract_yarn_version_from_env,
    run_yarn_cmd,
)
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


class GitDep(TypedDict):
    """A git dependency extracted from yarn.lock."""

    name: str
    clone_url: str
    ref: str


def fetch_yarn_source(request: Request) -> RequestOutput:
    """Process all the yarn source directories in a request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []

    for package in request.yarn_packages:
        path = request.source_dir.join_within_root(package.path)
        project = Project.from_source_dir(path)

        pkg_components, pkg_project_files = _resolve_yarn_project(project, request.output_dir)
        components.extend(pkg_components)
        project_files.extend(pkg_project_files)

    return RequestOutput.from_obj_list(
        components, _generate_environment_variables(), project_files=project_files
    )


def _verify_yarnrc_paths(project: Project) -> None:
    paths_conf_opts = {
        # pnpDataPath is only configurable in Yarn v3
        project.yarn_rc.get("pnpDataPath"): "pnpDataPath",
        project.yarn_rc.get("pnpUnpluggedFolder"): "pnpUnpluggedFolder",
        project.yarn_rc.get("installStatePath"): "installStatePath",
        project.yarn_rc.get("patchFolder"): "patchFolder",
        project.yarn_rc.get("virtualFolder"): "virtualFolder",
    }

    for path in paths_conf_opts:
        if path is not None:
            try:
                project.source_dir.join_within_root(path)
            except Exception:
                raise PackageRejected(
                    (
                        f"YarnRC '{paths_conf_opts[path]}={path}' property: path points "
                        "outside of the source directory"
                    ),
                    solution=(
                        "Make sure that all Yarn RC configuration options specifying a path "
                        "point to a relative location inside the main repository"
                    ),
                )


def _check_zero_installs(project: Project) -> None:
    if project.is_zero_installs:
        raise PackageRejected(
            (f"Yarn zero install detected, PnP zero installs are unsupported by {APP_NAME}"),
            solution=(
                "Please convert your project to a regular install-based one.\n"
                "Depending on whether you use Yarn's PnP or a different node linker Yarn setting "
                "make sure to remove '.yarn/cache' or 'node_modules' directories respectively."
            ),
        )


def _check_lockfile(project: Project) -> None:
    lockfile_filename = project.yarn_rc.get("lockfileFilename", "yarn.lock")
    if not project.source_dir.join_within_root(lockfile_filename).path.exists():
        raise LockfileNotFound(
            files=project.source_dir.join_within_root(lockfile_filename).path,
        )


def _verify_repository(project: Project) -> None:
    _verify_yarnrc_paths(project)
    _check_zero_installs(project)
    _check_lockfile(project)


def _resolve_yarn_project(
    project: Project, output_dir: RootedPath
) -> tuple[list[Component], list[ProjectFile]]:
    """Process a request for a single yarn source directory.

    :param project: the directory to be processed.
    :param output_dir: the directory where the prefetched dependencies will be placed.
    :return: a tuple of (components, project_files)
    :raises PackageManagerError: if fetching dependencies fails
    """
    log.info(f"Fetching the yarn dependencies at the subpath {project.source_dir}")

    version = _configure_yarn_version(project)
    _verify_repository(project)

    git_deps = _parse_lockfile_git_deps(project)

    if git_deps:
        project_files = _clone_and_resolve_git_deps(project, git_deps, output_dir)

        # Immutable installs must be off so yarn can update the lockfile
        # to reference the local tarballs we wrote to package.json.
        _set_yarnrc_configuration(project, output_dir, version)
        project.yarn_rc["enableImmutableInstalls"] = False
        project.yarn_rc.write()
        try:
            _fetch_dependencies(project.source_dir)
        finally:
            project.yarn_rc["enableImmutableInstalls"] = True
            project.yarn_rc.write()

        project_files.append(_build_lockfile_project_file(project, output_dir))

        packages = resolve_packages(project.source_dir)
        git_purl_map = {d["name"]: _build_vcs_url(d) for d in git_deps}
        return create_components(packages, project, output_dir, git_purl_map), project_files
    else:
        _set_yarnrc_configuration(project, output_dir, version)
        packages = resolve_packages(project.source_dir)
        _fetch_dependencies(project.source_dir)
        return create_components(packages, project, output_dir), []


def _configure_yarn_version(project: Project) -> semver.Version:
    """Resolve the yarn version and set it in the package.json file if needed.

    :raises PackageRejected:
        if the yarn version can't be determined from either yarnPath or packageManager
        if there is a mismatch between the yarn version specified by yarnPath and PackageManager
    """
    yarn_path_version = get_semver_from_yarn_path(project.yarn_rc.get("yarnPath"))
    package_manager_version = get_semver_from_package_manager(
        project.package_json.get("packageManager")
    )

    version = yarn_path_version if yarn_path_version else package_manager_version

    # this check is done here to make mypy understand that version can't be Optional anymore
    if version is None:
        raise PackageRejected(
            "Unable to determine the yarn version to use to process the request",
            solution=(
                "Ensure that either yarnPath is defined in .yarnrc.yml or that packageManager "
                "is defined in package.json"
            ),
        )

    if version not in VersionsRange("3.0.0", "5.0.0"):
        raise PackageRejected(
            f"Unsupported Yarn version '{version}' detected",
            solution="Please pick a different version of Yarn (3.0.0<= Yarn version <5.0.0)",
        )

    if (
        yarn_path_version
        and package_manager_version
        and yarn_path_version != package_manager_version
    ):
        raise PackageRejected(
            (
                f"Mismatch between the yarn versions specified by yarnPath (yarn@{yarn_path_version}) "
                f"and packageManager (yarn@{package_manager_version})"
            ),
            solution=(
                "Ensure that the versions of yarn specified by yarnPath in .yarnrc.yml and "
                "packageManager in package.json agree"
            ),
        )

    if not package_manager_version:
        project.package_json["packageManager"] = f"yarn@{yarn_path_version}"
        project.package_json.write()

    _verify_corepack_yarn_version(version, project.source_dir)

    return version


def _get_plugin_allowlist(yarn_rc: YarnRc) -> list[Plugin]:
    """Return a list of plugins that can be kept in .yarnrc.yml.

    Some plugins are required for processing a specific protocol (e.g. exec), and their absence
    would make yarn commands such as 'install' and 'info' fail. Keeping this whitelist allows
    our application to get the list of packages from 'yarn info' and properly inform the user if his request
    is not processable in case it contains disallowed protocols.

    This list should only have official plugins that add new protocols and that also do not
    implement the 'fetchPackageInfo' hook, since it would allow arbitrary code execution.

    Note that starting from v4, the official plugins are enabled by default and can't be disabled.
    Since they're not present in the .yarnrc.yml file anymore, this function has no effect on v4
    projects.

    See https://v3.yarnpkg.com/advanced/plugin-tutorial#hook-fetchPackageInfo.
    """
    default_plugins = [
        Plugin(path=".yarn/plugins/@yarnpkg/plugin-exec.cjs", spec="@yarnpkg/plugin-exec"),
    ]

    return [plugin for plugin in default_plugins if plugin in yarn_rc.get("plugins", [])]


def _set_yarnrc_configuration(
    project: Project, output_dir: RootedPath, version: semver.Version
) -> None:
    """Set all the necessary configuration in yarnrc for the project processing.

    :param project: a Project instance
    :param output_dir: in case the dependencies need to be fetched, this is where they will be
        downloaded to.
    :param version: the project's Yarn version.
    """
    yarn_rc = project.yarn_rc

    yarn_rc["plugins"] = _get_plugin_allowlist(yarn_rc)
    yarn_rc["checksumBehavior"] = "throw"
    yarn_rc["enableImmutableInstalls"] = True
    yarn_rc["pnpMode"] = "strict"
    yarn_rc["enableStrictSsl"] = True
    yarn_rc["enableTelemetry"] = False
    yarn_rc["ignorePath"] = True
    yarn_rc["unsafeHttpWhitelist"] = []
    yarn_rc["enableMirror"] = False
    yarn_rc["enableScripts"] = False
    yarn_rc["enableGlobalCache"] = True
    yarn_rc["globalFolder"] = str(output_dir.join_within_root("deps", "yarn"))

    # In Yarn v4, constraints can be automatically executed as part of `yarn install`, so they
    # need to be explicitly disabled
    if version in VersionsRange("4.0.0-rc1", "5.0.0"):  # type: ignore
        yarn_rc["enableConstraintsChecks"] = False

    yarn_rc.write()


def _fetch_dependencies(source_dir: RootedPath) -> None:
    """Fetch dependencies using 'yarn install'.

    :param source_dir: the directory in which the yarn command will be called.
    :raises PackageManagerError: if the 'yarn install' command fails.
    """
    run_yarn_cmd(["install", "--mode", "skip-build"], source_dir)


def _generate_environment_variables() -> list[EnvironmentVariable]:
    """Generate environment variables that will be used for building the project."""
    env_vars = {
        "YARN_ENABLE_GLOBAL_CACHE": "false",
        "YARN_ENABLE_IMMUTABLE_CACHE": "false",
        "YARN_ENABLE_MIRROR": "true",
        "YARN_GLOBAL_FOLDER": "${output_dir}/deps/yarn",
    }

    return [EnvironmentVariable(name=key, value=value) for key, value in env_vars.items()]


def _verify_corepack_yarn_version(expected_version: semver.Version, source_dir: RootedPath) -> None:
    """Verify that corepack installed the correct version of yarn by checking `yarn --version`."""
    installed_yarn_version = extract_yarn_version_from_env(source_dir)
    if installed_yarn_version != expected_version:
        raise PackageManagerError(
            f"{APP_NAME} expected corepack to install yarn@{expected_version} but instead "
            f"found yarn@{installed_yarn_version}."
        )

    log.info("Processing the request using yarn@%s", installed_yarn_version)


def _parse_lockfile_git_deps(project: Project) -> list[GitDep]:
    """Scan yarn.lock for git-resolved dependencies.

    Unsupported variants (patched git deps, workspace+commit) are silently
    skipped here and will be reported later by resolve_packages.

    :param project: the Project whose yarn.lock will be scanned.
    :return: list of GitDep dicts with keys: name, clone_url, ref.
    """
    lockfile_filename = project.yarn_rc.get("lockfileFilename", "yarn.lock")
    lockfile_path = project.source_dir.join_within_root(lockfile_filename).path

    with lockfile_path.open("r") as f:
        lockfile_data: dict[str, Any] = yaml.safe_load(f) or {}

    git_deps: list[GitDep] = []

    for key, entry in lockfile_data.items():
        if key == "__metadata":
            continue

        resolution = entry.get("resolution") if isinstance(entry, dict) else None
        if not resolution:
            continue

        try:
            parsed = _parse_locator(resolution)
        except (UnexpectedFormat, UnsupportedFeature):
            continue

        ref = parsed.parsed_reference
        protocol = ref.protocol.removesuffix(":") if ref.protocol else None

        # Skip patched git deps and workspace+commit git deps; these unsupported
        # variants will be reported later by resolve_packages/parse_locator.
        if protocol == "patch":
            continue

        selector_qs = parse_qs(ref.selector)
        if "commit" not in selector_qs:
            continue
        if "workspace" in selector_qs:
            continue

        commit = selector_qs["commit"][0]
        clone_url = _build_clone_url(protocol, ref.source)

        name = parsed.name
        if parsed.scope:
            name = f"@{parsed.scope}/{name}"

        git_deps.append(GitDep(name=name, clone_url=clone_url, ref=commit))

    return git_deps


def _build_clone_url(protocol: str | None, source: str | None) -> str:
    """Build a clone-friendly URL from a Berry git locator's protocol and source.

    Given protocol="https" and source="//host/path.git", returns "https://host/path.git".
    For SCP-style locators like protocol="git@host", source="ns/repo.git", returns
    "git@host:ns/repo.git".

    The ``git+`` prefix (e.g. ``git+ssh``, ``git+https``) is stripped so the result
    is a URL that git can clone directly and that clone_as_tarball can apply its
    ssh-to-https fallback to correctly.
    """
    if not protocol or not source:
        raise PackageRejected(
            f"Cannot build clone URL from protocol={protocol!r}, source={source!r}",
            solution="Ensure the git dependency in yarn.lock has a valid URL.",
        )

    # Strip git+ prefix (e.g. git+ssh -> ssh, git+https -> https) so the URL
    # is directly usable by git clone and the ssh→https fallback in clone_as_tarball.
    protocol = protocol.removeprefix("git+")

    return f"{protocol}:{source}"


def _build_vcs_url(dep: GitDep) -> str:
    """Build a canonical vcs_url qualifier for PURL generation."""
    return f"git+{dep['clone_url']}@{dep['ref']}"


def _clone_and_resolve_git_deps(
    project: Project,
    git_deps: list[GitDep],
    output_dir: RootedPath,
) -> list[ProjectFile]:
    """Clone git deps, write resolutions to package.json, and return ProjectFiles.

    :param project: the Project whose package.json will be modified
    :param git_deps: list of GitDep dicts
    :param output_dir: base output directory
    :return: list of ProjectFile objects for package.json (with template paths)
    :raises PackageRejected: if two git deps share the same name but different sources
    """
    seen_names: dict[str, tuple[str, str]] = {}
    for dep in git_deps:
        key = (dep["clone_url"], dep["ref"])
        if dep["name"] in seen_names and seen_names[dep["name"]] != key:
            raise PackageRejected(
                f"Multiple git dependencies share the name '{dep['name']}' but resolve to "
                f"different sources. This cannot be expressed in a single yarn resolution.",
                solution=(
                    "Ensure all git dependencies with the same package name point to the same "
                    "repository and commit."
                ),
            )
        seen_names[dep["name"]] = key

    yarn_deps_dir = output_dir.join_within_root("deps", "yarn")
    tarball_info: dict[str, tuple[RootedPath, Path]] = {}
    cloned_sources: dict[tuple[str, str], RootedPath] = {}

    for dep in git_deps:
        source_key = (dep["clone_url"], dep["ref"])

        if source_key not in cloned_sources:
            tarball_rooted = clone_repo_pack_archive(dep["clone_url"], dep["ref"], yarn_deps_dir)
            cloned_sources[source_key] = tarball_rooted
        else:
            tarball_rooted = cloned_sources[source_key]

        rel_to_output = tarball_rooted.path.relative_to(output_dir.path)
        tarball_info[dep["name"]] = (tarball_rooted, rel_to_output)

    resolutions = project.package_json.data.get("resolutions", {})
    for name, (tarball_rooted, _) in tarball_info.items():
        resolutions[name] = f"file:{tarball_rooted.path}"
    project.package_json["resolutions"] = resolutions
    project.package_json.write()

    template_data = copy.deepcopy(project.package_json.data)
    for name, (_, rel_to_output) in tarball_info.items():
        template_data["resolutions"][name] = f"file:${{output_dir}}/{rel_to_output}"

    package_json_path = project.source_dir.join_within_root("package.json").path
    project_files = [
        ProjectFile(
            abspath=package_json_path.resolve(),
            template=json.dumps(template_data, indent=2) + "\n",
        )
    ]

    return project_files


def _build_lockfile_project_file(project: Project, output_dir: RootedPath) -> ProjectFile:
    """Read the updated yarn.lock and build a ProjectFile with templated paths.

    Replaces any occurrence of the output directory path with ${output_dir}.
    """
    lockfile_filename = project.yarn_rc.get("lockfileFilename", "yarn.lock")
    lockfile_path = project.source_dir.join_within_root(lockfile_filename).path
    lockfile_content = lockfile_path.read_text()

    output_dir_str = str(output_dir.path)
    lockfile_content = lockfile_content.replace(output_dir_str, "${output_dir}")

    return ProjectFile(
        abspath=lockfile_path.resolve(),
        template=lockfile_content,
    )
