# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

from hermeto.core.package_managers.maven.main import (
    MIRROR_ID,
    _get_matching_pom_files,
    _write_remote_repositories_files,
)
from hermeto.core.package_managers.maven.models import MavenArtifact


def test_get_matching_pom_files(tmp_path: Path) -> None:
    ma = MavenArtifact.model_validate(
        {
            "groupId": "g",
            "artifactId": "a",
            "version": "1",
            "checksumAlgorithm": "SHA-256",
            "checksum": "abcdef",
            "resolved": "https://repo.maven.apache.org/maven2/g/a/1/a-1.jar",
            "scope": "compile",
        }
    )
    poms = _get_matching_pom_files(tmp_path, [ma])
    url = next(iter(poms.keys()))
    assert url == "https://repo.maven.apache.org/maven2/g/a/1/a-1.pom"


def test_write_remote_repositories_files(tmp_path: Path) -> None:
    ma = MavenArtifact.model_validate(
        {
            "groupId": "g",
            "artifactId": "a",
            "version": "1",
            "checksumAlgorithm": "SHA-256",
            "checksum": "abcdef",
            "resolved": "https://repo.maven.apache.org/maven2/g/a/1/a-1.jar",
            "scope": "compile",
        }
    )
    ma_dir = tmp_path.joinpath("g", "a", "1")
    ma_dir.mkdir(parents=True)

    _write_remote_repositories_files(tmp_path, [ma])
    text = ma_dir.joinpath("_remote.repositories").read_text()
    assert f"a-1.jar>{MIRROR_ID}=" in text
