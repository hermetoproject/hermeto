# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Literal

import pytest

from hermeto.core.checksum import SUPPORTED_ALGORITHMS, ChecksumInfo, must_match_any_checksum
from hermeto.core.errors import ChecksumVerificationFailed, PackageRejected

FILE_CONTENT = "Beetlejuice! Beetlejuice! Beetlejuice!"

SHA512 = "da518fe8b800b3325fe35ca680085fe37626414d0916937a01a25ef8f5d7aa769b7233073235fce85eec717e02bb9d72062656cf2d79223792a784910c267b54"
SHA256 = "ed1f8cd69bfacf0528744b6a7084f36e8841b6128de0217503e215612a0ee835"
MD5 = "308764bc995153f7d853827a675e6731"
SHA512_SRI = "sha512-Ml8Hhh4KuIjZBgaxB0/elW/TlU3MTG5Bjb/52KqDQrVQdIFAiDK/qsjkjzRNxlDI3w+BgsAnHtn6IzqjLDKYOQ=="
SHA512_HEX = (
    "325f07861e0ab888d90606b1074fde956fd3954dcc4c6e418dbff9d8aa8342b5507481408832bfaac8e48f344"
    "dc650c8df0f8182c0271ed9fa233aa32c329839"
)

SUPPORTED_ALG_STR = ", ".join(sorted(SUPPORTED_ALGORITHMS))

AlgorithmName = Literal["sha256", "sha512", "md5"]


def correct(algorithm: AlgorithmName) -> ChecksumInfo:
    digest = {"sha256": SHA256, "sha512": SHA512, "md5": MD5}[algorithm]
    return ChecksumInfo(algorithm, digest)


def wrong(algorithm: AlgorithmName) -> ChecksumInfo:
    hexlen = {"sha256": 64, "sha512": 128, "md5": 32}[algorithm]
    wrong_digest = "a" * hexlen
    return ChecksumInfo(algorithm, wrong_digest)


unknown = ChecksumInfo("sha0", "a" * 40)


@pytest.mark.parametrize(
    "checksums, expect_log_msg",
    [
        (
            [correct("sha256")],
            f"sha256 checksum matches: {SHA256}",
        ),
        (
            [correct("sha512")],
            f"sha512 checksum matches: {SHA512}",
        ),
        (
            [correct("md5")],
            f"md5 checksum matches: {MD5}",
        ),
        (
            [correct("sha256"), correct("sha512")],
            f"sha256 checksum matches: {SHA256}",
        ),
        (
            [correct("sha512"), correct("sha256")],
            f"sha512 checksum matches: {SHA512}",
        ),
        (
            [wrong("sha256"), correct("sha256")],
            f"sha256 checksum matches: {SHA256}",
        ),
        (
            [wrong("sha512"), correct("sha256")],
            f"sha256 checksum matches: {SHA256}",
        ),
        (
            [unknown, correct("sha256")],
            f"sha256 checksum matches: {SHA256}",
        ),
        (
            # sha256 is computed first and maches the one later in the list
            [wrong("sha256"), correct("sha512"), correct("sha256")],
            f"sha256 checksum matches: {SHA256}",
        ),
    ],
)
def test_verify_checksum(
    checksums: list[ChecksumInfo],
    expect_log_msg: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    filename = "spells.txt"
    path = tmp_path.joinpath(filename)
    path.write_text(FILE_CONTENT)
    caplog.set_level("DEBUG")

    must_match_any_checksum(path, checksums)

    assert caplog.messages == [
        f"Verifying checksums of {filename}",
        f"{filename}: {expect_log_msg}",
    ]


@pytest.mark.parametrize(
    "checksums, expect_log_msgs",
    [
        (
            [wrong("sha256")],
            [f"sha256 checksum does not match (got: {SHA256})"],
        ),
        (
            [wrong("sha512")],
            [f"sha512 checksum does not match (got: {SHA512})"],
        ),
        (
            [wrong("md5")],
            [f"md5 checksum does not match (got: {MD5})"],
        ),
        (
            [unknown],
            [f"sha0 checksum not supported (supported: {SUPPORTED_ALG_STR})"],
        ),
        (
            [wrong("sha256"), wrong("sha512"), unknown],
            [
                f"sha256 checksum does not match (got: {SHA256})",
                f"sha512 checksum does not match (got: {SHA512})",
                f"sha0 checksum not supported (supported: {SUPPORTED_ALG_STR})",
            ],
        ),
        (
            # log the mismatch for each algorithm only once
            [
                ChecksumInfo("sha256", "bad1"),
                ChecksumInfo("sha256", "bad2"),
                ChecksumInfo("sha512", "bad3"),
                ChecksumInfo("sha512", "bad4"),
            ],
            [
                f"sha256 checksum does not match (got: {SHA256})",
                f"sha512 checksum does not match (got: {SHA512})",
            ],
        ),
    ],
)
def test_verify_checksum_failure(
    checksums: list[ChecksumInfo],
    expect_log_msgs: list[str],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    file = tmp_path.joinpath("spells.txt")
    file.write_text(FILE_CONTENT)
    caplog.set_level("WARNING")

    with pytest.raises(ChecksumVerificationFailed):
        must_match_any_checksum(file, checksums)

    expect_messages = [f"spells.txt: {msg}" for msg in expect_log_msgs]
    assert caplog.messages == expect_messages


@pytest.mark.parametrize(
    "checksum, algorithm, expected",
    [
        (SHA512_HEX, "sha512", SHA512_SRI),
        ("a" * 40, "sha1", "sha1-qqqqqqqqqqqqqqqqqqqqqqqqqqo="),
    ],
)
def test_convert_hex_checksum_to_sri(checksum: str, algorithm: str, expected: str) -> None:
    assert ChecksumInfo(algorithm, checksum).to_sri() == expected


@pytest.mark.parametrize(
    "integrity, algorithm, expected",
    [
        (SHA512_SRI, "sha512", SHA512_HEX),
        (
            "sha1-ignored== sha256-ignored-too== " + SHA512_SRI,
            "sha512",
            SHA512_HEX,
        ),
        (
            "sha512 " + SHA512_SRI,
            "sha512",
            SHA512_HEX,
        ),
    ],
)
def test_convert_sri_to_hex_checksum(integrity: str, algorithm: str, expected: str) -> None:
    rv = ChecksumInfo.from_sri(integrity)
    assert rv.algorithm == algorithm
    assert rv.hexdigest == expected


@pytest.mark.parametrize(
    "integrity",
    [
        "",
        "   ",
        "sha512",
        "sha512-",
        "-noalgo",
        "sha256-",
        "sha512-not-base64",
    ],
)
def test_convert_sri_to_hex_checksum_invalid(integrity: str) -> None:
    with pytest.raises(PackageRejected):
        ChecksumInfo.from_sri(integrity)
