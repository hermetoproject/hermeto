# SPDX-License-Identifier: GPL-3.0-only
"""Install Yarn releases into Hermeto's cache for hermetic builds.

Downloads are cached, so repeated requests will skip network usage.

Berry (v3+):
    We fetch the standalone yarn.js bundle directly from
    repo.yarnpkg.com and run it via `node`.

Classic (v1.x):
    The yarn npm package ships a full tree with a `bin/yarn.js` entry point.
    We extract the tree and point node at that entry.
"""

import errno
import hashlib
import logging
import shutil
import tarfile
from functools import partial
from pathlib import Path
from string import hexdigits
from tempfile import TemporaryDirectory

from semver import Version

from hermeto.core.checksum import SUPPORTED_ALGORITHMS, ChecksumInfo, must_match_any_checksum
from hermeto.core.errors import (
    ChecksumVerificationFailed,
    FetchError,
    InvalidChecksum,
    PackageManagerError,
    PathOutsideRoot,
)
from hermeto.core.package_managers.general import download_binary_file
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import get_cache_dir

log = logging.getLogger(__name__)

BERRY_URL = "https://repo.yarnpkg.com/{version}/packages/yarnpkg-cli/bin/yarn.js"
CLASSIC_TARBALL_URL = "https://registry.npmjs.org/yarn/-/yarn-{version}.tgz"


def _yarn_cache_dir() -> Path:
    """Return the directory where downloaded yarn binaries are cached."""
    return get_cache_dir().absolute() / "yarn"


def _get_checksum(version: Version) -> ChecksumInfo | None:
    """Read packageManager optional algorithm.hex checksum from version build metadata."""
    if version.build is None:
        return None

    algorithm, _, digest = version.build.partition(".")
    if (
        algorithm not in SUPPORTED_ALGORITHMS
        or not digest
        or any(char not in hexdigits for char in digest)
        or len(digest) != hashlib.new(algorithm).digest_size * 2
    ):
        raise InvalidChecksum(
            version.build,
            solution=(
                "Use yarn@<version>+<algorithm>.<hex-digest> in packageManager, "
                "with a supported hash algorithm and its complete hexadecimal digest."
            ),
        )
    return ChecksumInfo(algorithm, digest.lower())


def _verify_checksum(checksum: ChecksumInfo | None, artifact_path: Path) -> None:
    if checksum is not None:
        must_match_any_checksum(artifact_path, [checksum])


def _prepare_artifact_path(staging_dir: Path, is_classic: bool) -> tuple[Path, Path]:
    package_dir = staging_dir / "package"
    package_dir.mkdir()
    artifact_path = staging_dir / "yarn.tgz" if is_classic else package_dir / "yarn.js"

    return (artifact_path, package_dir)


def _is_complete_installation(directory: Path, is_classic: bool) -> bool:
    """Check for the files needed to start the selected Yarn release."""
    required_files = ("bin/yarn.js", "lib/cli.js") if is_classic else ("yarn.js",)
    return all((directory / path).is_file() for path in required_files)


def install_yarn(version: Version) -> Path:
    """Install a Yarn release into Hermeto's cache and return its entry point path.

    :param version: yarn version, optionally with a packageManager checksum.
    :return: absolute path to the cached `yarn.js` file.
    :raises InvalidChecksum: when the checksum in the version is malformed or unsupported.
    :raises ChecksumVerificationFailed: when the downloaded artifact does not match the checksum.
    :raises FetchError: when the release download fails.
    :raises PathOutsideRoot: when an archive member escapes the extraction directory.
    :raises PackageManagerError: when the archive is invalid or installation fails.
    """
    checksum = _get_checksum(version)
    release = version.replace(build=None)
    is_classic = release < Version.parse("2.0.0")
    relative_entry_point = Path("bin/yarn.js") if is_classic else Path("yarn.js")
    url = (CLASSIC_TARBALL_URL if is_classic else BERRY_URL).format(version=release)
    dest_dir = _yarn_cache_dir() / str(version)
    entry_point = dest_dir / relative_entry_point

    if dest_dir.exists():
        if not _is_complete_installation(dest_dir, is_classic):
            raise PackageManagerError(
                f"Cached Yarn installation at {dest_dir} is incomplete",
                solution="Remove the cached Yarn installation and retry the request.",
            )
        log.debug("Using cached yarn %s at %s", version, entry_point)
        return entry_point

    log.info("Installing yarn@%s to %s", version, dest_dir)

    try:
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise PackageManagerError(
            f"Could not create Yarn cache directory at {dest_dir.parent}",
            solution="Check the cache path, directory permissions, and available disk space.",
        ) from e

    release_error = partial(
        PackageManagerError,
        solution=(
            "Retry the request. If it still fails, report the affected Yarn release "
            "to the Hermeto maintainers."
        ),
    )

    try:
        with TemporaryDirectory(prefix=f".{dest_dir.name}-", dir=dest_dir.parent) as temp_dir:
            artifact_path, package_dir = _prepare_artifact_path(Path(temp_dir), is_classic)
            download_binary_file(url, artifact_path)
            _verify_checksum(checksum, artifact_path)

            if is_classic:
                with tarfile.open(artifact_path, "r:gz") as tf:
                    _extract_classic(tf, package_dir, release)

            if not _is_complete_installation(package_dir, is_classic):
                raise release_error(f"Yarn release is incomplete after downloading {url}")

            try:
                package_dir.rename(dest_dir)
            except OSError as e:
                if e.errno in (errno.EEXIST, errno.ENOTEMPTY) and _is_complete_installation(
                    dest_dir, is_classic
                ):
                    return entry_point  # Another process already cached it.
                raise PackageManagerError(
                    f"Could not cache Yarn at {dest_dir}",
                    solution=(
                        "Check the cache directory permissions and remove any incomplete "
                        "Yarn installation at this path before retrying."
                    ),
                ) from e
    except (PackageManagerError, ChecksumVerificationFailed, FetchError, PathOutsideRoot):
        raise
    except tarfile.ReadError as e:
        raise release_error(f"Failed to extract yarn@{version} from {url}") from e
    except OSError as e:
        raise PackageManagerError(
            f"Could not install Yarn at {dest_dir}",
            solution="Check the cache path, directory permissions, and available disk space.",
        ) from e
    except Exception as e:
        raise PackageManagerError(
            f"Failed to download yarn@{version} from {url}",
            solution=(
                "Make sure the requested yarn version exists on the npm registry "
                "and that network access is available."
            ),
        ) from e

    return entry_point


def _extract_classic(tf: tarfile.TarFile, dest_dir: Path, version: Version) -> None:
    """Extract the full classic yarn tree, removing its archive root.

    Classic’s entry point loads modules from the adjacent
    lib directory, so the complete release tree is required.

    Only regular files and directories under `package/` or `yarn-v<version>/`
    are supported. Report all unsupported entry types and layouts before extraction
    and fail installation if any are found.

    :raises PackageManagerError: when an archive contains an unsupported entry type or layout.
    """
    roots = {"package", f"yarn-v{version}"}
    members = tf.getmembers()
    n_problems = 0
    for member in members:
        if not (member.isfile() or member.isdir()):
            log.error(
                "Unsupported entry %r of type %r; only regular files and directories are supported.",
                member.name,
                member.type,
            )
            n_problems += 1

        archive_root, _, rel_path = member.name.partition("/")
        if archive_root not in roots or (not rel_path and not member.isdir()):
            log.error(
                "Unexpected entry %r; expected files under 'package/' or 'yarn-v%s/'.",
                member.name,
                version,
            )
            n_problems += 1

    if n_problems:
        raise PackageManagerError(
            f"Found {n_problems} problems in Yarn Classic {version} archive, "
            "more details in the logs.",
            solution=(
                "Report the affected Yarn release and the problems listed in the logs "
                "to the Hermeto maintainers."
            ),
        )

    extraction_root = RootedPath(dest_dir.resolve())
    for member in members:
        _, _, rel_path = member.name.partition("/")
        if not rel_path:
            continue

        target = extraction_root.join_within_root(rel_path).path

        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            fileobj = tf.extractfile(member)
            if fileobj is not None:
                with target.open("wb") as out:
                    shutil.copyfileobj(fileobj, out)
