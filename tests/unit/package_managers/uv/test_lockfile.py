# SPDX-License-Identifier: GPL-3.0-only
import hashlib
from pathlib import Path
from unittest import mock

import pytest

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound, UnsupportedFeature
from hermeto.core.models.input import Request
from hermeto.core.package_managers.uv.lockfile import parse_uv_lockfile
from hermeto.core.package_managers.uv.main import _artifact_filename, fetch_uv_source
from hermeto.core.rooted_path import RootedPath


def test_parse_uv_lockfile_missing(tmp_path: Path) -> None:
    with pytest.raises(LockfileNotFound, match=r"Required files not found"):
        parse_uv_lockfile(RootedPath(tmp_path))


@pytest.mark.parametrize(
    "content, expect_error",
    [
        pytest.param("not = [valid", r"uv.lock", id="invalid_toml"),
        pytest.param("[[package]]\nname = \"foo\"\n", r"version", id="missing_version"),
        pytest.param("version = 1\npackage = \"foo\"\n", r"package", id="package_not_array"),
        pytest.param(
            "version = 1\npackage = [\"foo\"]\n",
            r"package",
            id="package_contains_non_table",
        ),
    ],
)
def test_parse_uv_lockfile_invalid(tmp_path: Path, content: str, expect_error: str) -> None:
    (tmp_path / "uv.lock").write_text(content)

    with pytest.raises(InvalidLockfileFormat, match=expect_error):
        parse_uv_lockfile(RootedPath(tmp_path))


def test_parse_uv_lockfile_valid(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("version = 1\n")

    parsed = parse_uv_lockfile(RootedPath(tmp_path))
    assert parsed["version"] == 1


def test_fetch_uv_source_with_virtual_only_package(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(
        """
version = 1

[[package]]
name = "project"
version = "0.1.0"
source = { virtual = "." }
""".strip()
        + "\n"
    )

    request = Request(
        source_dir=tmp_path,
        output_dir=tmp_path,
        packages=[{"type": "x-uv"}],
    )

    output = fetch_uv_source(request)
    assert len(output.components) == 1
    assert output.components[0].name == "project"


@mock.patch("hermeto.core.package_managers.uv.main.download_binary_file")
def test_fetch_uv_source_downloads_remote_artifact(
    mock_download_binary_file: mock.Mock, tmp_path: Path
) -> None:
    payload = b"hello from uv artifact"
    checksum = hashlib.sha256(payload).hexdigest()

    (tmp_path / "uv.lock").write_text(
        f"""
version = 1

[[package]]
name = "sniffio"
version = "1.3.1"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://example.com/sniffio-1.3.1.tar.gz", hash = "sha256:{checksum}" }}
""".strip()
        + "\n"
    )

    def _fake_download(url: str, download_path: Path, *args: object, **kwargs: object) -> None:
        Path(download_path).parent.mkdir(parents=True, exist_ok=True)
        Path(download_path).write_bytes(payload)

    mock_download_binary_file.side_effect = _fake_download

    request = Request(
        source_dir=tmp_path,
        output_dir=tmp_path,
        packages=[{"type": "x-uv"}],
    )

    output = fetch_uv_source(request)

    assert len(output.components) == 1
    assert output.components[0].name == "sniffio"
    assert output.components[0].purl == "pkg:pypi/sniffio@1.3.1"
    assert (tmp_path / "deps" / "uv" / "sniffio-1.3.1.tar.gz").exists()


def test_fetch_uv_source_rejects_git_source_in_strict_mode(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(
        """
version = 1

[[package]]
name = "foo"
version = "1.0.0"
source = { git = "https://github.com/example/foo", rev = "0123456789abcdef" }
""".strip()
        + "\n"
    )

    request = Request(
        source_dir=tmp_path,
        output_dir=tmp_path,
        packages=[{"type": "x-uv"}],
    )

    with pytest.raises(UnsupportedFeature, match=r"source kind 'git'"):
        fetch_uv_source(request)


def test_fetch_uv_source_skips_git_source_in_permissive_mode(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(
        """
version = 1

[[package]]
name = "foo"
version = "1.0.0"
source = { git = "https://github.com/example/foo", rev = "0123456789abcdef" }
""".strip()
        + "\n"
    )

    request = Request(
        source_dir=tmp_path,
        output_dir=tmp_path,
        packages=[{"type": "x-uv"}],
        mode="permissive",
    )

    output = fetch_uv_source(request)
    assert output.components == []


@pytest.mark.parametrize(
    "url, expected",
    [
        pytest.param("https://example.com/sniffio-1.3.1.tar.gz", "sniffio-1.3.1.tar.gz", id="normal"),
        pytest.param("https://example.com/", "artifact.bin", id="root_path"),
        pytest.param("https://example.com", "artifact.bin", id="empty_path"),
    ],
)
def test_artifact_filename(url: str, expected: str) -> None:
    assert _artifact_filename(url) == expected
