# SPDX-License-Identifier: GPL-3.0-only
import base64
import binascii
import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from hermeto.core.errors import ChecksumVerificationFailed, InvalidChecksum, MissingChecksum
from hermeto.core.type_aliases import StrPath

log = logging.getLogger(__name__)


SUPPORTED_ALGORITHMS = hashlib.algorithms_guaranteed


class ChecksumInfo(NamedTuple):
    """A cryptographic algorithm and a hex-encoded checksum calculated by that algorithm."""

    algorithm: str
    hexdigest: str

    def to_sri(self) -> str:
        """Return the Subresource Integrity representation of this ChecksumInfo.

        https://developer.mozilla.org/en-US/docs/Web/Security/Subresource_Integrity

        Note: npm and Yarn classic (v1) use this format in their lockfiles; newer Yarn
        versions use a different integrity representation that does not go through this helper.
        """
        bytes_sha = bytes.fromhex(self.hexdigest)
        base64_sha = base64.b64encode(bytes_sha).decode("utf-8")
        return f"{self.algorithm}-{base64_sha}"

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.hexdigest}"

    @classmethod
    def from_sri(cls, sri: str) -> "ChecksumInfo":
        """Convert the input Subresource Integrity value to ChecksumInfo.

        The integrity string may contain multiple hashes separated by whitespace according to the
        SRI specification [https://www.w3.org/TR/sri-2/].

        When multiple hashes are present, we pick the strongest algorithm
        (sha512 > sha384 > sha256 > others). Note that while the SRI specification only mentions
        SHA-2 algorithms (SHA-256, SHA-384, SHA-512), in practice "others" can include legacy
        algorithms like sha1, which may appear when Git commit hashes are referenced in
        integrity fields.
        """

        sri_hash_priorities = {"sha512": 3, "sha384": 2, "sha256": 1}
        best = None

        for part in sri.split():
            if "-" not in part:
                continue

            alg, val = part.strip().split("-", 1)
            alg, val = alg.lower(), val.strip()

            if not alg or not val:
                continue

            # (priority, algorithm, value)
            current = (sri_hash_priorities.get(alg, 0), alg, val)
            if best is None or current > best:
                best = current

        try:
            # If best is None, unpacking will raise TypeError
            _, alg, val = best  # type: ignore[misc]
            return ChecksumInfo(alg, base64.b64decode(val).hex())
        except (TypeError, binascii.Error) as e:
            # best is None (no valid checksum found) -> pass None
            # best contains malformed data -> pass the malformed value
            checksum_value = None if best is None else f"{best[1]}-{best[2]}"
            if checksum_value is None:
                raise MissingChecksum(
                    None,
                    solution=(
                        "Integrity value is missing. "
                        "Please check the integrity value in your lockfile."
                    ),
                ) from e
            else:
                raise InvalidChecksum(
                    checksum=checksum_value,
                    solution=(
                        "Integrity value is malformed. "
                        "Please check the integrity value in your lockfile."
                    ),
                ) from e

    @classmethod
    def from_hash(cls, h: str) -> "ChecksumInfo":
        """Convert the input hash to ChecksumInfo."""
        algorithm, _, digest = h.partition(":")
        return ChecksumInfo(algorithm, digest)


class _MismatchInfo(NamedTuple):
    algorithm: str
    maybe_digest: str | None  # None == algorithm is not supported


def must_match_any_checksum(
    file_path: StrPath,
    expected_checksums: Iterable[ChecksumInfo],
    chunk_size: int = 10240,
) -> None:
    """Verify that the file matches at least one of the expected checksums.

    Note: any checksum algorithms not supported by python's hashlib will be skipped.

    If none of the checksums match, log all the mismatches and skipped algorithms at WARNING level,
    then raise an exception.

    :param file_path: path to the file to verify
    :param expected_checksums: all the possible checksums for this file
    :param chunk_size: when computing checksums, read the file in chunks of this size
    :raises ChecksumVerificationFailed: if none of the expected checksums matched the actual
                                        checksum (for any of the supported algorithms)
    """
    filename = Path(file_path).name
    log.info("Verifying checksums of %s", filename)
    mismatches: list[_MismatchInfo] = []

    for algorithm, expected_digests in _group_by_algorithm(expected_checksums).items():
        if algorithm in SUPPORTED_ALGORITHMS:
            digest = _get_hexdigest(file_path, algorithm, chunk_size)
        else:
            digest = None

        if digest not in expected_digests:
            mismatches.append(_MismatchInfo(algorithm, digest))
        else:
            log.debug("%s: %s checksum matches: %s", filename, algorithm, digest)
            return

    _log_mismatches(filename, mismatches)
    raise ChecksumVerificationFailed(filename)


def _group_by_algorithm(checksums: Iterable[ChecksumInfo]) -> dict[str, set[str]]:
    digests_by_algorithm = defaultdict(set)
    for algorithm, digest in checksums:
        digests_by_algorithm[algorithm].add(digest)
    return digests_by_algorithm


def _get_hexdigest(file_path: StrPath, algorithm: str, chunk_size: int) -> str:
    with open(file_path, "rb") as f:
        hasher = hashlib.new(algorithm)
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
        return hasher.hexdigest()


def _log_mismatches(filename: str, mismatches: list[_MismatchInfo]) -> None:
    for algorithm, digest in mismatches:
        if digest is not None:
            log.warning("%s: %s checksum does not match (got: %s)", filename, algorithm, digest)
        else:
            log.warning(
                "%s: %s checksum not supported (supported: %s)",
                filename,
                algorithm,
                ", ".join(sorted(SUPPORTED_ALGORITHMS)),
            )
