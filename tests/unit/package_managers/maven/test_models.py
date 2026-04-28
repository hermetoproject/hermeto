# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path

from hermeto.core.package_managers.maven.models import MavenLockfile, parse_maven_artifacts


def test_parse_deduplicates_identical_artifacts(tmp_path: Path) -> None:
    shared_url = "https://repo.maven.apache.org/maven2/g/a/1/a-1.jar"
    plugin_url = "https://repo.maven.apache.org/maven2/x/y/1/y-1.jar"

    shared_artifact = {
        "groupId": "g",
        "artifactId": "a",
        "version": "1",
        "checksumAlgorithm": "SHA-256",
        "checksum": "abcdef",
        "resolved": shared_url,
        "scope": "compile",
    }
    lockfile_data = {
        "groupId": "root",
        "artifactId": "root",
        "version": "1",
        "pom": {},
        "dependencies": [shared_artifact],
        "mavenPlugins": [
            {
                "groupId": "x",
                "artifactId": "y",
                "version": "1",
                "checksumAlgorithm": "SHA-256",
                "checksum": "abcdef",
                "resolved": plugin_url,
                "scope": "compile",
                "dependencies": [shared_artifact],
            }
        ],
    }
    lockfile_path = tmp_path / "lockfile.json"
    lockfile_path.write_text(json.dumps(lockfile_data))

    lockfile = MavenLockfile.from_file(lockfile_path)
    artifacts = parse_maven_artifacts(lockfile)
    assert {a.url for a in artifacts} == {shared_url, plugin_url}
