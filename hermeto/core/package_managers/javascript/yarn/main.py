# SPDX-License-Identifier: GPL-3.0-only
import copy
import json
import logging
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator, cast
from urllib.parse import parse_qs

import semver
import yaml
from hermeto import APP_NAME
from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import (
    PackageManagerError,
    PackageRejected,
    UnexpectedFormat,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import (
    Component,
    EnvironmentVariable,
    ProjectFile,
    RequestOutput,
)
from hermeto.core.models.sbom import create_backend_annotation
from hermeto.core.package_managers.javascript.js_utils import (
    clone_repo_pack_archive,
    parse_git_clone_url,
)
from hermeto.core.package_managers.javascript.yarn.locators import (
    parse_locator,
    parse_raw_locator,
)
from hermeto.core.package_managers.javascript.yarn.project import (
    PackageJson,
    Plugin,
    Project,
    YarnRc,
    get_semver_from_package_manager,
    get_semver_from_yarn_path,
)
from hermeto.core.package_managers.javascript.yarn.resolver import (
    create_components,
    resolve_packages,
)
from hermeto.core.package_managers.javascript.yarn.utils import (
    TarballVcsUrlMap,
    VcsUrl,
    VersionsRange,
    extract_yarn_version_from_env,
    run_yarn_cmd,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID, canonicalize_origin_url

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitDep:
    """A git dependency extracted from yarn.lock."""

    name: str
    clone_url: str
    ref: str


def _try_git_dep(resolution: str) -> GitDep | None:
    """Classify a yarn.lock resolution for git-dep rewriting.

    Returns a GitDep for plain rewriteable git dependencies, None for entries to
    ignore (non-git, unparseable, supported npm/tarball patches), or raises for
    unsupported git forms.

    :param resolution: a yarn.lock ``resolution`` locator string
    :return: a GitDep, or None if the resolution should be ignored
    :raises UnsupportedFeature: if the resolution is a patched git dep or selects a
        git workspace via ``#workspace=…&commit=…``
    :raises PackageRejected: if a git locator has no usable protocol/source for cloning
    """
    try:
        parsed = parse_raw_locator(resolution)
        ref = parsed.parsed_reference
    except UnexpectedFormat:
        log.debug("Skipping resolution %r: could not parse locator", resolution)
        return None

    protocol = ref.protocol.removesuffix(":") if ref.protocol else None

    # Validate patches (nested git fails; npm patches pass), then skip —
    # they are not git deps to rewrite.
    if protocol == "patch":
        _validate_resolution(resolution)
        return None

    selector_qs = parse_qs(ref.selector)
    if "commit" not in selector_qs:
        return None
    if "workspace" in selector_qs:
        # Raises UnsupportedFeature for #workspace=…&commit=…
        _validate_resolution(resolution)

    commit = selector_qs["commit"][0]
    clone_url = _build_clone_url(protocol, ref.source)

    name = parsed.name
    if parsed.scope:
        name = f"@{parsed.scope}/{name}"

    return GitDep(name, clone_url, commit)


def _validate_resolution(resolution: str) -> None:
    """Raise if the resolution is unsupported; discard the parsed locator."""
    parse_locator(resolution)


def fetch_yarn_source(request: Request) -> RequestOutput:
    """Process all the yarn source directories in a request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []

    for package in request.yarn_packages:
        path = request.source_dir.join_within_root(package.path)
        project = Project.from_source_dir(path)

        pkg_components, pkg_project_files, _ = _resolve_yarn_project(
            project, request.output_dir, get_config().mode, package.workspaces
        )
        components.extend(pkg_components)
        project_files.extend(pkg_project_files)

    annotations = []
    if backend_annotation := create_backend_annotation(components, "yarn"):
        annotations.append(backend_annotation)

    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=_generate_environment_variables(),
        project_files=project_files,
        annotations=annotations,
    )


def _resolve_yarn_project(
    project: Project,
    output_dir: RootedPath,
    mode: Mode,
    workspaces: list[str] | None = None,
) -> tuple[list[Component], list[ProjectFile], list[GitDep]]:
    """Process a request for a single yarn source directory.

    :param project: the directory to be processed.
    :param output_dir: the directory where the prefetched dependencies will be placed.
    :param mode: the processing mode (strict or permissive).
    :param workspaces: optional list of workspace names to focus on (Yarn v4 only).
    :return: a tuple of (components, project_files, git_deps)
    :raises PackageManagerError: if fetching dependencies fails
    :raises PackageRejected: if git deps are found in strict mode
    """
    log.info(f"Fetching the yarn dependencies at the subpath {project.source_dir}")

    project.yarn_rc["plugins"] = _get_plugin_allowlist(project.yarn_rc)
    project.yarn_rc.write()
    version = _configure_yarn_version(project)

    if workspaces and version < semver.Version.parse("4.0.0"):
        raise PackageRejected(
            f"Workspace focus requires Yarn v4 or later, but this project uses Yarn {version}",
            solution="Either upgrade to Yarn v4 or remove the 'workspaces' field from the input.",
        )

    git_deps = _parse_lockfile_git_deps(project)

    # Reject before mutating yarnrc / the lockfile when git deps need permissive mode.
    if git_deps and mode != Mode.PERMISSIVE:
        raise PackageRejected(
            "Git dependencies in Yarn Berry projects cannot be processed in strict mode "
            "because the lockfile must be modified to reference local tarballs."
        )

    _set_yarnrc_configuration(project, output_dir, version)
    if workspaces:
        # Focus cannot use --mode skip-build, and enableScripts:false does not
        # cover workspace scripts
        # https://github.com/yarnpkg/berry/blob/7744e6678de126a2ca2398d4123e3f7e009256b8/packages/docusaurus/static/configuration/yarnrc.json#L252
        _strip_workspace_scripts(project.source_dir)

    if not git_deps:
        packages = resolve_packages(project.source_dir, workspaces)

        _fetch_dependencies(project.source_dir, workspaces)

        with _hide_dev_dependencies(project):
            prod_packages = resolve_packages(project.source_dir, workspaces)

        return create_components(packages, prod_packages, project, output_dir), [], git_deps

    # When git deps are present the processing order is intentionally inverted
    # compared to the normal path above: we must fetch first (to update the
    # lockfile with local tarball references) and then resolve (to parse
    # package data from the now-modified lockfile).
    project_files, tarball_vcs_url_map = _clone_and_resolve_git_deps(project, git_deps, output_dir)

    project.yarn_rc["enableImmutableInstalls"] = False
    project.yarn_rc.write()
    try:
        _fetch_dependencies(project.source_dir, workspaces)
    finally:
        project.yarn_rc["enableImmutableInstalls"] = True
        project.yarn_rc.write()

    project_files.append(_build_lockfile_project_file(project, output_dir))

    packages = resolve_packages(project.source_dir, workspaces)

    if workspaces:
        _strip_workspace_scripts(project.source_dir)

    _fetch_dependencies(project.source_dir, workspaces)

    with _hide_dev_dependencies(project):
        prod_packages = resolve_packages(project.source_dir, workspaces)

    log.debug("Resolved %d total locators", len(packages))
    log.debug("Resolved %d production locators", len(prod_packages))
    return (
        create_components(packages, prod_packages, project, output_dir, tarball_vcs_url_map),
        project_files,
        git_deps,
    )


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

    config = get_config()
    if (proxy_url := config.yarn.proxy_url) is not None:
        yarn_rc["npmRegistryServer"] = str(proxy_url)
        login = config.yarn.proxy_login
        password = config.yarn.proxy_password
        if login is not None and password is not None:
            yarn_rc["npmAlwaysAuth"] = True
            yarn_rc["npmAuthIdent"] = f"{login}:{password}"

    # In Yarn v4, constraints can be automatically executed as part of `yarn install`, so they
    # need to be explicitly disabled
    if version in VersionsRange("4.0.0-rc1", "5.0.0"):  # type: ignore
        yarn_rc["enableConstraintsChecks"] = False

    yarn_rc.write()


@contextmanager
def _hide_dev_dependencies(project: Project) -> Generator[None, None, None]:
    """Temporarily remove devDependencies from every package.json in the source directory."""
    global_folder = project.yarn_rc.get("globalFolder")
    with tempfile.TemporaryDirectory() as temp_dir:
        # Prevent any changes to the hermeto-output/deps directory.
        # Otherwise, yarn will re-package file dependencies after modifying their package.json files.
        project.yarn_rc["globalFolder"] = temp_dir
        project.yarn_rc.write()

        saved_deps: list[tuple[PackageJson, dict[str, str]]] = []
        for path in project.source_dir.path.glob("**/package.json"):
            if "node_modules" in path.parts:
                continue

            package_json = PackageJson.from_file(path)
            if "devDependencies" not in package_json:
                continue

            log.debug('Found "devDependencies" in %s', path)
            saved_deps.append((package_json, package_json["devDependencies"]))
            del package_json["devDependencies"]
            package_json.write()

        filename = project.yarn_rc.get("lockfileFilename", "yarn.lock")
        lockfile = project.source_dir.join_within_root(filename)
        content = lockfile.path.read_text()
        try:
            run_yarn_cmd(["install", "--mode", "update-lockfile"], project.source_dir)
            yield
        finally:
            for package_json, deps in saved_deps:
                package_json["devDependencies"] = deps
                package_json.write()

            lockfile.path.write_text(content)
            project.yarn_rc["globalFolder"] = global_folder
            project.yarn_rc.write()


def _remove_scripts_field(package_json: PackageJson) -> None:
    """Delete the scripts field from a package.json if present and write it back."""
    if "scripts" in package_json:
        del package_json["scripts"]
        package_json.write()


def _workspace_package_dirs(source_dir: RootedPath) -> list[Path]:
    """Resolve workspace package directories from the root package.json workspaces field.

    Supports both the array form (``["packages/*"]``) and the object form
    (``{"packages": ["packages/*"]}``). Paths are constrained to ``source_dir``.
    """
    workspaces = PackageJson.from_dir(source_dir.path).data.get("workspaces", [])
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages", [])

    dirs: list[Path] = []
    for pattern in workspaces:
        for path in source_dir.path.glob(pattern):
            if not path.is_dir() or not (path / "package.json").exists():
                continue
            # Reject workspace globs that escape the source tree.
            source_dir.join_within_root(path.relative_to(source_dir.path))
            dirs.append(path)
    return dirs


def _strip_workspace_scripts(source_dir: RootedPath) -> None:
    """Remove scripts from the root and all workspace package.json files.

    ``yarn workspaces focus`` does not support ``--mode skip-build``, and
    ``enableScripts: false`` does not apply to workspace scripts
    (https://github.com/yarnpkg/berry/pull/4781). Stripping the scripts field
    prevents lifecycle scripts from executing during focus.

    Discovers workspaces from the root package.json ``workspaces`` field so it
    can also run on the git-deps path before ``resolve_packages``.
    """
    _remove_scripts_field(PackageJson.from_dir(source_dir.path))
    for workspace_dir in _workspace_package_dirs(source_dir):
        _remove_scripts_field(PackageJson.from_dir(workspace_dir))


def _fetch_dependencies(source_dir: RootedPath, workspaces: list[str] | None = None) -> None:
    """Fetch dependencies using 'yarn install' or 'yarn workspaces focus'.

    When workspaces are specified, only the dependencies of those workspaces (and their
    transitive workspace dependencies) are installed via 'yarn workspaces focus'.

    :param source_dir: the directory in which the yarn command will be called.
    :param workspaces: optional list of workspace names to focus on (Yarn v4 only).
    :raises PackageManagerError: if the yarn command fails.
    """
    try:
        if workspaces:
            run_yarn_cmd(["workspaces", "focus", *workspaces], source_dir)
        else:
            run_yarn_cmd(["install", "--mode", "skip-build"], source_dir)
    except PackageManagerError as e:
        # TODO: this follows a precedent set in resolver. Either a more robust way for
        # dealing with this must be found or a comment provided that such methods do not exist.
        has_proxy = get_config().yarn.proxy_url is not None
        if has_proxy and e.stderr and "Invalid authentication" in e.stderr:
            raise PackageManagerError(
                "Proxy requires authentication. Invalid or no authentication was provided",
                solution="Verify that proxy URL, login and password are set correctly.",
            )
        raise


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

    Loads the lockfile from disk, then delegates classification to
    ``_git_deps_from_lockfile``.

    :param project: the Project whose yarn.lock will be scanned.
    :return: list of GitDep instances.
    :raises UnsupportedFeature: if a patched or workspace-selecting git dep is found.
    """
    with project.lockfile_path.path.open("r") as f:
        lockfile_data: dict[str, Any] = yaml.safe_load(f) or {}
    return _git_deps_from_lockfile(lockfile_data)


def _git_deps_from_lockfile(lockfile_data: dict[str, Any]) -> list[GitDep]:
    """Extract rewriteable git dependencies from parsed yarn.lock data.

    Each ``resolution`` is classified by ``_try_git_dep``, which rejects
    unsupported variants (patched git deps, workspace+commit) so Hermeto never
    rewrites ``resolutions`` over them. Supported npm/tarball patches are ignored.

    :param lockfile_data: parsed yarn.lock contents.
    :return: list of GitDep instances.
    :raises UnsupportedFeature: if a patched or workspace-selecting git dep is found.
    """
    git_deps: list[GitDep] = []

    for key, entry in lockfile_data.items():
        if key == "__metadata":
            continue

        resolution = entry.get("resolution") if isinstance(entry, dict) else None
        if not resolution:
            continue

        if dep := _try_git_dep(resolution):
            git_deps.append(dep)

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
            f"Cannot construct clone URL from protocol={protocol!r}, source={source!r}",
            solution="Ensure the git dependency in yarn.lock has a valid URL.",
        )

    # Strip git+ prefix (e.g. git+ssh -> ssh, git+https -> https) so the URL
    # is directly usable by git clone and the ssh-to-https fallback in clone_as_tarball.
    protocol = protocol.removeprefix("git+")

    return f"{protocol}:{source}"


def _build_vcs_url(dep: GitDep) -> VcsUrl:
    """Build a canonical vcs_url qualifier for PURL generation.

    SCP-style clone URLs are normalized to ssh:// so Yarn PURLs match npm /
    RepoID output (e.g. git+ssh://git@host/path@ref).
    """
    return RepoID(
        canonicalize_origin_url(dep.clone_url),
        dep.ref,
    ).as_vcs_url_qualifier()


def _ensure_unique_git_dep_names(git_deps: list[GitDep]) -> None:
    """Reject git deps that share a package name but resolve to different sources.

    Yarn resolutions are keyed by package name, so conflicting sources cannot be
    expressed in a single package.json.
    """
    seen_names: dict[str, tuple[str, str]] = {}
    for dep in git_deps:
        url_ref_pair = (dep.clone_url, dep.ref)
        if dep.name in seen_names and seen_names[dep.name] != url_ref_pair:
            raise PackageRejected(
                f"Multiple git dependencies share the name '{dep.name}' but resolve to "
                f"different sources. This cannot be expressed in a single yarn resolution.",
                solution=(
                    "Ensure all git dependencies with the same package name point to the same "
                    "repository and commit."
                ),
            )
        seen_names[dep.name] = url_ref_pair


def _clone_git_deps(
    git_deps: list[GitDep],
    output_dir: RootedPath,
) -> tuple[dict[str, tuple[RootedPath, Path]], TarballVcsUrlMap]:
    """Clone git deps to local tarballs, deduplicating identical sources.

    :return: tuple of (package name -> (tarball path, path relative to output_dir),
        tarball path -> vcs_url qualifier map)
    """
    yarn_deps_dir = output_dir.join_within_root("deps", "yarn")
    tarball_info: dict[str, tuple[RootedPath, Path]] = {}
    cloned_sources: dict[tuple[str, str, str, str], RootedPath] = {}
    tarball_vcs_url_map: TarballVcsUrlMap = {}

    for dep in git_deps:
        clone_url = parse_git_clone_url(dep.clone_url, dep.ref)
        source_key = (clone_url.host, clone_url.namespace, clone_url.repo, clone_url.ref)
        if not all(source_key):
            raise UnexpectedFormat(
                f"Cannot parse git URL: {dep.clone_url}",
                solution="Ensure the git dependency has a valid URL and commit ref.",
            )

        source_key = cast(tuple[str, str, str, str], source_key)
        if source_key in cloned_sources:
            tarball_rooted = cloned_sources[source_key]
        else:
            tarball_rooted = clone_repo_pack_archive(clone_url, yarn_deps_dir)
            cloned_sources[source_key] = tarball_rooted
            tarball_vcs_url_map[str(tarball_rooted.path)] = _build_vcs_url(dep)

        rel_to_output = tarball_rooted.path.relative_to(output_dir.path)
        tarball_info[dep.name] = (tarball_rooted, rel_to_output)

    return tarball_info, tarball_vcs_url_map


def _apply_git_dep_resolutions(
    project: Project,
    tarball_info: dict[str, tuple[RootedPath, Path]],
) -> list[ProjectFile]:
    """Write file: resolutions into package.json and return a templated ProjectFile."""
    resolutions = project.package_json.data.get("resolutions", {})
    for name, (tarball_rooted, _) in tarball_info.items():
        resolutions[name] = f"file:{tarball_rooted.path}"
    project.package_json["resolutions"] = resolutions
    project.package_json.write()

    template_data = copy.deepcopy(project.package_json.data)
    for name, (_, rel_to_output) in tarball_info.items():
        template_data["resolutions"][name] = f"file:${{output_dir}}/{rel_to_output}"

    package_json_path = project.source_dir.join_within_root("package.json").path
    return [
        ProjectFile(
            abspath=package_json_path.resolve(),
            template=json.dumps(template_data, indent=2) + "\n",
        )
    ]


def _clone_and_resolve_git_deps(
    project: Project,
    git_deps: list[GitDep],
    output_dir: RootedPath,
) -> tuple[list[ProjectFile], TarballVcsUrlMap]:
    """Clone git deps, write resolutions to package.json, and return ProjectFiles.

    :param project: the Project whose package.json will be modified
    :param git_deps: list of GitDep instances
    :param output_dir: base output directory
    :return: tuple of (project files for the updated package.json, map of tarball paths
        to vcs_url qualifiers for PURL generation)
    :raises PackageRejected: if two git deps share the same name but different sources
    """
    _ensure_unique_git_dep_names(git_deps)
    tarball_info, tarball_vcs_url_map = _clone_git_deps(git_deps, output_dir)
    project_files = _apply_git_dep_resolutions(project, tarball_info)
    return project_files, tarball_vcs_url_map


def _build_lockfile_project_file(project: Project, output_dir: RootedPath) -> ProjectFile:
    """Read the updated yarn.lock and build a ProjectFile with templated paths.

    Replaces any occurrence of the output directory path with ${output_dir}.
    """
    lockfile_path = project.lockfile_path.path
    lockfile_content = lockfile_path.read_text()

    output_dir_str = str(output_dir.path)
    lockfile_content = lockfile_content.replace(output_dir_str, "${output_dir}")

    return ProjectFile(
        abspath=lockfile_path.resolve(),
        template=lockfile_content,
    )
