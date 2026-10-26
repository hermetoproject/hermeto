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
import os
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
)
from hermeto.core.package_managers.general import download_binary_file
from hermeto.core.utils import get_cache_dir

log = logging.getLogger(__name__)

BERRY_URL = "https://repo.yarnpkg.com/{version}/packages/yarnpkg-cli/bin/yarn.js"
CLASSIC_TARBALL_URL = "https://registry.npmjs.org/yarn/-/yarn-{version}.tgz"


def _yarn_cache_dir() -> Path:
    """Create and check the directory used for downloaded Yarn releases."""
    cache_dir = get_cache_dir().absolute() / "yarn"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Directories also need search permission to access their contents.
        if not os.access(cache_dir, os.R_OK | os.W_OK | os.X_OK):
            raise PermissionError(errno.EACCES, "Permission denied", str(cache_dir))
    except OSError as e:
        raise PackageManagerError(
            f"Could not use Yarn cache directory {cache_dir}: {e}",
            solution=(
                "Check the cache path, directory permissions, and available disk space, "
                "or set XDG_CACHE_HOME to a readable and writable directory."
            ),
        ) from e
    return cache_dir


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


def install_yarn(version: Version) -> Path:
    """Install a Yarn release into Hermeto's cache and return its entry point path.

    :param version: yarn version, optionally with a packageManager checksum.
    :return: absolute path to the cached `yarn.js` file.
    :raises InvalidChecksum: when the checksum in the version is malformed or unsupported.
    :raises ChecksumVerificationFailed: when the downloaded artifact does not match the checksum.
    :raises FetchError: when the release download fails.
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
        log.debug("Using cached yarn %s at %s", version, entry_point)
        return entry_point

    log.info("Installing yarn@%s to %s", version, dest_dir)

    release_error = partial(
        PackageManagerError,
        solution="Retry the request.",
    )

    try:
        with TemporaryDirectory(prefix=f".{dest_dir.name}-", dir=dest_dir.parent) as temp_dir:
            artifact_path, package_dir = _prepare_artifact_path(Path(temp_dir), is_classic)
            download_binary_file(url, artifact_path)
            _verify_checksum(checksum, artifact_path)

            if is_classic:
                with tarfile.open(artifact_path, "r:gz") as tf:
                    tf.extractall(package_dir, filter="data")
                # Classic releases use either npm's package/ or yarn-v<version>/ layout.
                package_dir /= (
                    "package" if (package_dir / "package").is_dir() else f"yarn-v{release}"
                )

            package_dir.rename(dest_dir)
    except (PackageManagerError, ChecksumVerificationFailed, FetchError):
        raise
    except (tarfile.ReadError, tarfile.FilterError) as e:
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
