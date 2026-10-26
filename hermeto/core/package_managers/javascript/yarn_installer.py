# SPDX-License-Identifier: GPL-3.0-only
"""Download and cache yarn binaries/files for hermetic builds.

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
import tarfile
from pathlib import Path
from string import hexdigits
from tempfile import TemporaryDirectory

from semver import Version

from hermeto.core.checksum import SUPPORTED_ALGORITHMS, ChecksumInfo, must_match_any_checksum
from hermeto.core.errors import ChecksumVerificationFailed, InvalidChecksum, PackageManagerError
from hermeto.core.package_managers.general import download_binary_file
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import get_cache_dir

log = logging.getLogger(__name__)

BERRY_URL = "https://repo.yarnpkg.com/{version}/packages/yarnpkg-cli/bin/yarn.js"
CLASSIC_TARBALL_URL = "https://registry.npmjs.org/yarn/-/yarn-{version}.tgz"


def _yarn_cache_dir() -> Path:
    """Return the directory where downloaded yarn binaries are cached."""
    return get_cache_dir() / "yarn"


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


def install_yarn(version: Version) -> Path:
    """Download and cache a yarn release, returning the path to JS entry point.

    :param version: yarn version, optionally with a packageManager checksum.
    :return: absolute path to the cached `yarn.js` file.
    :raises InvalidChecksum: when the checksum in the version is malformed or unsupported.
    :raises ChecksumVerificationFailed: when the downloaded artifact does not match the checksum.
    :raises PackageManagerError: when the download or extraction fails.
    """
    checksum = _get_checksum(version)
    release = version.replace(build=None)
    is_classic = release < Version.parse("2.0.0")
    relative_entry_point = Path("bin/yarn.js") if is_classic else Path("yarn.js")
    url = (CLASSIC_TARBALL_URL if is_classic else BERRY_URL).format(version=release)
    dest_dir = _yarn_cache_dir() / str(version)
    entry_point = dest_dir / relative_entry_point

    if entry_point.is_file():
        log.debug("Using cached yarn %s at %s", version, entry_point)
        return entry_point

    log.info("Installing yarn@%s to %s", version, dest_dir)

    try:
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=f".{dest_dir.name}-", dir=dest_dir.parent) as temp_dir:
            staging_dir = Path(temp_dir)
            package_dir = staging_dir / "package"
            package_dir.mkdir()

            artifact_path = staging_dir / "yarn.tgz" if is_classic else package_dir / "yarn.js"
            download_binary_file(url, artifact_path)
            if checksum is not None:
                must_match_any_checksum(artifact_path, [checksum])

            if is_classic:
                with tarfile.open(artifact_path, "r:gz") as tf:
                    _extract_classic(tf, package_dir, release)

            if not (package_dir / relative_entry_point).is_file():
                raise PackageManagerError(
                    f"Yarn entry point {relative_entry_point} not found after downloading {url}",
                    solution="This may indicate a change in the Yarn release layout.",
                )

            try:
                package_dir.rename(dest_dir)
            except OSError as e:
                if e.errno in (errno.EEXIST, errno.ENOTEMPTY) and entry_point.is_file():
                    return entry_point  # Another process already cached it.
                raise PackageManagerError(
                    f"Could not cache Yarn at {dest_dir}",
                    solution=(
                        "Check the cache directory permissions and remove any incomplete "
                        "Yarn installation at this path before retrying."
                    ),
                ) from e
    except (PackageManagerError, ChecksumVerificationFailed):
        raise
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

    Classic yarn's entry point `bin/yarn.js` requires sibling modules at
    runtime, so we extract everything under `package/` into `dest_dir`.
    Older releases use `yarn-v<version>/` as the archive root instead.

    Only regular files and directories are extracted; symlinks, device nodes
    and other special entries are skipped (for safety).
    """
    roots = {"package", f"yarn-v{version}"}
    extraction_root = RootedPath(dest_dir.resolve())
    for member in tf:
        archive_root, _, rel_path = member.name.partition("/")
        if archive_root not in roots or not rel_path:
            continue

        target = extraction_root.join_within_root(rel_path).path

        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            fileobj = tf.extractfile(member)
            if fileobj is not None:
                target.write_bytes(fileobj.read())
