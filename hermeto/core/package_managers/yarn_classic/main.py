import logging
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hermeto import APP_NAME
from hermeto.core.errors import LockfileNotFound, PackageManagerError, PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import Component, EnvironmentVariable, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.package_managers.yarn.utils import (
    VersionsRange,
    extract_yarn_version_from_env,
    run_yarn_cmd,
)
from hermeto.core.package_managers.yarn_classic.project import Project
from hermeto.core.package_managers.yarn_classic.resolver import (
    GitPackage,
    RegistryPackage,
    UrlPackage,
    YarnClassicPackage,
    resolve_packages,
)
from hermeto.core.package_managers.yarn_classic.utils import (
    get_git_tarball_mirror_name,
    get_tarball_mirror_name,
)
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


MIRROR_DIR = "deps/yarn-classic"
YARN_NETWORK_TIMEOUT_MILLISECONDS = 600000
_yarn_classic_pattern = "yarn lockfile v1"  # See [yarn_classic_trait].


class MissingLockfile(PackageRejected):
    """Indicate that a lock file is missing."""

    def __init__(self) -> None:
        """Initialize a Missing Lockfile error."""
        reason = "Yarn lockfile 'yarn.lock' missing, refusing to continue"
        solution = "Make sure your repository has a Yarn lockfile (i.e. yarn.lock) checked in"
        super().__init__(reason, solution=solution)


class NotV1Lockfile(PackageRejected):
    """Indicate that a lockfile is of wrong tyrpoe."""

    def __init__(self, package_path: Any) -> None:
        """Initialize a Missing Lockfile error."""
        reason = f"{package_path} not a Yarn v1"
        super().__init__(reason, solution=None)


def fetch_yarn_source(request: Request) -> RequestOutput:
    """Process all the yarn source directories in a request."""
    components: list[Component] = []

    def _ensure_mirror_dir_exists(output_dir: RootedPath) -> None:
        output_dir.join_within_root(MIRROR_DIR).path.mkdir(parents=True, exist_ok=True)

    for package in request.yarn_packages:
        package_path = request.source_dir.join_within_root(package.path)
        project = Project.from_source_dir(package_path)

        _verify_repository(project)
        _ensure_mirror_dir_exists(request.output_dir)
        components.extend(_resolve_yarn_project(project, request.output_dir))

    return RequestOutput.from_obj_list(
        components, _generate_build_environment_variables(), project_files=[]
    )


def _resolve_yarn_project(project: Project, output_dir: RootedPath) -> list[Component]:
    """Process a request for a single yarn source directory."""
    log.info(f"Fetching the yarn dependencies at the subpath {project.source_dir}")

    _verify_corepack_yarn_version(project.source_dir, _get_prefetch_environment_variables())
    _fetch_dependencies(project.source_dir, output_dir)
    packages = resolve_packages(project, output_dir.join_within_root(MIRROR_DIR))
    _verify_no_offline_mirror_collisions(packages)

    return _create_sbom_components(packages)


def _create_sbom_components(packages: Iterable[YarnClassicPackage]) -> list[Component]:
    """Create SBOM components from the given yarn packages."""
    result = []
    for package in packages:
        properties = PropertySet(npm_development=package.dev).to_properties()
        result.append(
            Component(
                name=package.name,
                purl=package.purl,
                version=package.version,
                properties=properties,
            )
        )

    return result


def _run_yarn_install(
    source_dir: RootedPath,
    env: dict[str, str],
    frozen_lockfile: bool = False,
    skip_integrity: bool = False,
    offline: bool = False,
) -> None:
    """Run `yarn install` in the given directory.

    :param source_dir: the directory in which the yarn command will be called.
    :param env: a mapping of environment variables to set for the `yarn` subprocess
    :param frozen_lockfile: don't generate a lockfile and fail if an update is needed
    :param skip_integrity: run install without checking if node_modules is installed
    :param offline: trigger an error if any required dependencies are not available in local cache
    :raises PackageManagerError: if the underlying 'yarn install' command fails.
    """
    cmd = [
        "install",
        "--disable-pnp",
        "--ignore-engines",
        "--no-default-rc",
        "--non-interactive",
    ]
    if frozen_lockfile:
        cmd.append("--frozen-lockfile")
    if skip_integrity:
        cmd.append("--skip-integrity-check")
    if offline:
        cmd.append("--offline")

    run_yarn_cmd(cmd, source_dir, env)


def _fetch_dependencies(source_dir: RootedPath, output_dir: RootedPath) -> None:
    """Fetch dependencies for the project.

    Install the project dependencies via a two-step process to overcome concurrency
    issues with the offline mirror.

    :param source_dir: the directory in which the yarn command will be called.
    :param output_dir: the output directory for the request.
    :raises PackageManagerError: if either install step fails
    """
    # Populate the local cache with the project dependencies
    env = _get_prefetch_environment_variables()
    _run_yarn_install(source_dir, env, frozen_lockfile=True)

    # Populate the offline mirror from the local cache
    # Since the node_modules directory is already populated, we need to force a
    # reinstall with the --skip-integrity option, otherwise it will be a no-op and
    # the offline mirror will not get filled. Use the --offline flag to exclusively
    # rely on the local cache which was already populated by the first install.
    env["YARN_YARN_OFFLINE_MIRROR"] = str(output_dir.join_within_root(MIRROR_DIR))
    env["YARN_YARN_OFFLINE_MIRROR_PRUNING"] = "false"
    _run_yarn_install(source_dir, env, skip_integrity=True, offline=True)


def _get_prefetch_environment_variables() -> dict[str, str]:
    """Get environment variables that will be used for the prefetch."""
    return {
        "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
        "COREPACK_ENABLE_PROJECT_SPEC": "0",
        "YARN_IGNORE_PATH": "true",
        "YARN_IGNORE_SCRIPTS": "true",
        "YARN_NETWORK_TIMEOUT": f"{YARN_NETWORK_TIMEOUT_MILLISECONDS}",
    }


def _generate_build_environment_variables() -> list[EnvironmentVariable]:
    """Generate environment variables that will be used for building the project.

    These ensure that yarnv1 will
    - YARN_YARN_OFFLINE_MIRROR: Maintain offline copies of packages for repeatable and reliable
        builds. Defines the cache location.
    - YARN_YARN_OFFLINE_MIRROR_PRUNING: Control automatic pruning of the offline mirror. We
        disable this, as we need to retain the cache.
    """
    env_vars = {
        "YARN_YARN_OFFLINE_MIRROR": "${output_dir}/deps/yarn-classic",
        "YARN_YARN_OFFLINE_MIRROR_PRUNING": "false",
    }

    return [EnvironmentVariable(name=key, value=value) for key, value in env_vars.items()]


def _reject_if_pnp_install(project: Project) -> None:
    if project.is_pnp_install:
        raise PackageRejected(
            reason=(f"Yarn PnP install detected; PnP installs are unsupported by {APP_NAME}"),
            solution=(
                "Please convert your project to a regular install-based one.\n"
                "If you use Yarn's PnP, please remove `installConfig.pnp: true`"
                " from 'package.json', any file(s) with glob name '*.pnp.cjs',"
                " and any 'node_modules' directories."
            ),
        )


def _get_path_to_yarn_lock(project: Project) -> Path:
    return project.source_dir.join_within_root("yarn.lock").path


def _reject_if_wrong_lockfile_version(project: Project) -> None:
    yarnlock_path = _get_path_to_yarn_lock(project)
    text = yarnlock_path.read_text()
    if _yarn_classic_pattern not in text:
        raise NotV1Lockfile(project.source_dir)


def _reject_if_lockfile_is_missing(project: Project) -> None:
    yarnlock_path = _get_path_to_yarn_lock(project)
    if not yarnlock_path.exists():
        raise LockfileNotFound(
            files=yarnlock_path,
        )


def _verify_repository(project: Project) -> None:
    _reject_if_lockfile_is_missing(project)
    _reject_if_wrong_lockfile_version(project)
    _reject_if_pnp_install(project)


def _verify_corepack_yarn_version(source_dir: RootedPath, env: dict[str, str]) -> None:
    """Verify that corepack installed the correct version of yarn by checking `yarn --version`."""
    installed_yarn_version = extract_yarn_version_from_env(source_dir, env)

    if installed_yarn_version not in VersionsRange("1.22.0", "2.0.0"):
        raise PackageManagerError(
            f"{APP_NAME} expected corepack to install yarn >=1.22.0,<2.0.0, but instead "
            f"found yarn@{installed_yarn_version}."
        )

    log.info("Processing the request using yarn@%s", installed_yarn_version)


def _verify_no_offline_mirror_collisions(packages: Iterable[YarnClassicPackage]) -> None:
    """
    Verify that there are no duplicate tarballs in the offline mirror.

    This is a safety check to ensure that the offline mirror is not corrupted.
    The only exception is when all the packages are the same. It may happen when
    yarn.lock contains multiple references to the same package, as it is with npm aliases.
    """
    tarball_collisions = defaultdict(list)

    for p in packages:
        if isinstance(p, (RegistryPackage, UrlPackage)):
            tarball_name = get_tarball_mirror_name(p.url)
        elif isinstance(p, GitPackage):
            tarball_name = get_git_tarball_mirror_name(p.url)
        else:
            # file, link, and workspace packages are not copied to the offline mirror
            continue

        tarball_collisions[tarball_name].append(p)

    for tarball_name, packages in tarball_collisions.items():
        if all(pkg == packages[0] for pkg in packages):
            continue

        raise PackageManagerError(
            f"Tarball collision in the offline mirror: {tarball_name} ({len(packages)}x)"
        )


# References
# [yarn_classic_trait]:  https://github.com/yarnpkg/berry/blob/13d5b3041794c33171808fdce635461ff4ab5c4e/packages/yarnpkg-core/sources/Project.ts#L434
