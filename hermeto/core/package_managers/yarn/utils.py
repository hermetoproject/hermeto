import os
import subprocess

from semver import Version

from hermeto.core.errors import PackageManagerError
from hermeto.core.rooted_path import RootedPath
from hermeto.core.type_aliases import SemverLike
from hermeto.core.utils import run_cmd


def run_yarn_cmd(cmd: list[str], source_dir: RootedPath, env: dict[str, str] | None = None) -> str:
    """Run a yarn command on a source directory.

    :param cmd: the command that will be executed, split in a list of strings in every space.
    :param source_dir: the directory in the repository containing the yarn source files.
    :param env: environment variables to be set during the command's execution
    :raises PackageManagerError: if the command fails.
    """
    env = env or {}
    # if the caller doesn't specify a PATH variable, then pass the PATH from the current
    # process to the subprocess
    if "PATH" not in env and (self_path := os.environ.get("PATH")):
        env = env | {"PATH": self_path}
    try:
        return run_cmd(cmd=["yarn", *cmd], params={"cwd": source_dir, "env": env})
    except subprocess.CalledProcessError as e:
        # the yarn command writes the errors to stdout
        raise PackageManagerError(f"Yarn command failed: {' '.join(cmd)}", stderr=e.stdout)


class VersionsRange:
    """Represents a version range for cleaner version constrains checks.

    Versions range is a right-open interval:
    >>> Version.parse("1.2.3") in VersionsRange("3.0.0", "4.0.0")
    False
    >>> Version.parse("1.2.3") in VersionsRange("1.0.0", "2.0.0")
    True
    >>> Version.parse("1.0.0") in VersionsRange("1.0.0", "2.0.0")
    True
    >>> Version.parse("2.0.0") in VersionsRange("1.0.0", "2.0.0")
    False

    Release candidates are a special case, they are ignored within the
    interval and cause immediate rejection on any of the boundaries:
    >>> Version.parse("2.0.0-rc1") in VersionsRange("1.0.0", "2.0.0")
    False
    >>> Version.parse("1.0.0-rc1") in VersionsRange("1.0.0", "2.0.0")
    False
    >>> Version.parse("1.5.0-rc1") in VersionsRange("1.0.0", "2.0.0")
    True
    """

    def __init__(self, min_ver: SemverLike, max_ver: SemverLike) -> None:
        """Initialize a version range."""
        self.min_ver = min_ver if isinstance(min_ver, Version) else Version.parse(min_ver)
        self.max_ver = max_ver if isinstance(max_ver, Version) else Version.parse(max_ver)

    def __contains__(self, other: Version) -> bool:
        if not isinstance(other, self.min_ver.__class__):
            return False
        # The original version check logic (as captured in UTs) broke with direct
        # version comparison rules, e.g. 4.0.0-rc1 would have been considered
        # version 4 and would  have been rejected basing on major version
        # only. Direct comparison of Version object would have resulted in 4.0.0-rc1
        # being strictly within 4.0.0 interval (since rcN is not a version yet).
        # The original implementation considered anything starting with
        # a different major version an outlier. The logic below captures this
        # with a special case for versions with prerelease field set.
        if other.prerelease:
            # Drop prerelease:
            other_ver = Version(other.major, other.minor, other.patch)
            # Treat boundaries separately:
            if other_ver == self.min_ver or other_ver == self.max_ver:
                return False
            # Continue as usual otherwise:
            return other_ver >= self.min_ver and other < self.max_ver
        else:
            return other >= self.min_ver and other < self.max_ver


def extract_yarn_version_from_env(source_dir: RootedPath, env: dict | None = None) -> Version:
    """Extract yarn version from environment."""
    env = (
        {"COREPACK_ENABLE_DOWNLOAD_PROMPT": "0", "YARN_IGNORE_PATH": "true"} if env is None else env
    )
    yarn_version_output = run_yarn_cmd(["--version"], source_dir, env=env).strip()

    try:
        installed_yarn_version = Version.parse(yarn_version_output)
    except ValueError as e:
        raise PackageManagerError(
            "The command `yarn --version` did not return a valid semver."
        ) from e
    return installed_yarn_version
