# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import MavenPackageInput, Request
from hermeto.core.package_managers.maven import main as maven_main
from hermeto.core.rooted_path import RootedPath


def _minimal_lockfile_data(
    group_id: str = "com.myapp",
    artifact_id: str = "my-app",
    version: str = "1.0.0",
    dependencies: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Minimal lockfile data for tests."""
    data: dict[str, Any] = {
        "groupId": group_id,
        "artifactId": artifact_id,
        "version": version,
        "dependencies": dependencies if dependencies is not None else [],
    }
    data.update(kwargs)
    return data


def _write_lockfile(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# --- fetch_maven_source ---


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_fetch_maven_source_returns_request_output(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """fetch_maven_source returns RequestOutput with MAVEN_OPTS and creates deps dir."""
    lockfile_path = rooted_tmp_path.path / "lockfile.json"
    _write_lockfile(lockfile_path, _minimal_lockfile_data())

    request = Request(
        source_dir=rooted_tmp_path,
        output_dir=rooted_tmp_path,
        packages=[MavenPackageInput(type="x-maven", path=Path("."))],
    )

    result = maven_main.fetch_maven_source(request)

    assert result.build_config.environment_variables
    maven_opts = [
        ev for ev in result.build_config.environment_variables if ev.name == "MAVEN_OPTS"
    ]
    assert len(maven_opts) == 1
    assert "${output_dir}/deps/maven" in maven_opts[0].value
    deps_dir = rooted_tmp_path.path / "deps" / "maven"
    assert deps_dir.is_dir()


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_fetch_maven_source_no_packages_to_download_does_not_call_run_cmd(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """fetch_maven_source does not call run_cmd when lockfile has no artifacts to download."""
    _write_lockfile(rooted_tmp_path.path / "lockfile.json", _minimal_lockfile_data())

    request = Request(
        source_dir=rooted_tmp_path,
        output_dir=rooted_tmp_path,
        packages=[MavenPackageInput(type="x-maven", path=Path("."))],
    )

    maven_main.fetch_maven_source(request)

    mock_run_cmd.assert_not_called()


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_fetch_maven_source_calls_run_cmd_for_each_artifact(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """fetch_maven_source invokes run_cmd with mvn dependency:get for each artifact to download."""
    url = "https://repo.example.com/com/example/lib/1.0/lib-1.0.jar"
    lockfile_data = _minimal_lockfile_data(
        dependencies=[
            {
                "groupId": "com.example",
                "artifactId": "lib",
                "version": "1.0",
                "resolved": url,
            },
        ]
    )
    _write_lockfile(rooted_tmp_path.path / "lockfile.json", lockfile_data)

    request = Request(
        source_dir=rooted_tmp_path,
        output_dir=rooted_tmp_path,
        packages=[MavenPackageInput(type="x-maven", path=Path("."))],
    )

    maven_main.fetch_maven_source(request)

    mock_run_cmd.assert_called()
    call_args = mock_run_cmd.call_args
    cmd = call_args[0][0]
    params = call_args[0][1]
    assert cmd[0] == "mvn"
    assert "maven-dependency-plugin" in cmd[1]
    assert "-DgroupId=com.example" in cmd
    assert "-DartifactId=lib" in cmd
    assert "-Dversion=1.0" in cmd
    assert "-Dmaven.repo.local=" in str(cmd)
    assert "-Dtransitive=false" in cmd
    assert params["cwd"] == rooted_tmp_path.path


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_fetch_maven_source_resolves_each_maven_package(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """fetch_maven_source processes each maven package at its path."""
    pkg_dir = rooted_tmp_path.path / "subpkg"
    pkg_dir.mkdir()
    _write_lockfile(pkg_dir / "lockfile.json", _minimal_lockfile_data())

    request = Request(
        source_dir=rooted_tmp_path,
        output_dir=rooted_tmp_path,
        packages=[MavenPackageInput(type="x-maven", path=Path("subpkg"))],
    )

    maven_main.fetch_maven_source(request)

    # No artifacts to download, so run_cmd only used if there were; we just check no exception
    # and that lockfile was read from subpkg (no PackageRejected)
    assert (pkg_dir / "lockfile.json").exists()


# --- _resolve_maven ---


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_resolve_maven_rejects_when_lockfile_missing(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """_resolve_maven raises PackageRejected when lockfile.json is not present."""
    package_dir = rooted_tmp_path
    deps_dir = rooted_tmp_path.join_within_root("deps", "maven")
    deps_dir.path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(PackageRejected) as exc_info:
        maven_main._resolve_maven(package_dir, deps_dir)

    assert "lockfile.json" in str(exc_info.value)
    mock_run_cmd.assert_not_called()


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_resolve_maven_downloads_artifacts_and_saves_checksums(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """_resolve_maven calls run_cmd for artifacts and writes checksum files."""
    url = "https://repo.example.com/com/example/art/2.0/art-2.0.jar"
    lockfile_data = _minimal_lockfile_data(
        dependencies=[
            {
                "groupId": "com.example",
                "artifactId": "art",
                "version": "2.0",
                "resolved": url,
                "checksum": "abc123",
                "checksumAlgorithm": "SHA-256",
            },
        ]
    )
    _write_lockfile(rooted_tmp_path.path / "lockfile.json", lockfile_data)
    deps_dir = rooted_tmp_path.join_within_root("deps", "maven")
    deps_dir.path.mkdir(parents=True, exist_ok=True)
    # Artifact dir would be created by Maven when run_cmd runs; since we mock run_cmd, create it
    (deps_dir.path / "com" / "example" / "art" / "2.0").mkdir(parents=True)

    maven_main._resolve_maven(rooted_tmp_path, deps_dir)

    mock_run_cmd.assert_called_once()
    # Checksum file should be written: deps/maven/com/example/art/2.0/art-2.0.jar.sha256
    checksum_path = deps_dir.path / "com" / "example" / "art" / "2.0" / "art-2.0.jar.sha256"
    assert checksum_path.exists()
    assert checksum_path.read_text() == "abc123"


# --- _download_maven_artifacts ---


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_download_maven_artifacts_cmd_without_classifier_or_packaging(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """_download_maven_artifacts builds mvn command without -Dclassifier/-Dpackaging for default jar."""
    deps_dir = rooted_tmp_path.path / "deps"
    deps_dir.mkdir()
    dependencies = {
        "https://repo.example.com/g/a/1.0/a-1.0.jar": {
            "group_id": "g",
            "artifact_id": "a",
            "version": "1.0",
            "classifier": None,
            "artifact_type": "jar",
        },
    }

    maven_main._download_maven_artifacts(
        rooted_tmp_path,
        deps_dir,
        dependencies,
        {},
    )

    mock_run_cmd.assert_called_once()
    cmd = mock_run_cmd.call_args[0][0]
    assert "-DgroupId=g" in cmd
    assert "-DartifactId=a" in cmd
    assert "-Dversion=1.0" in cmd
    assert "-Dmaven.repo.local=" in str(cmd)
    # No classifier or packaging for default jar
    assert not any("-Dclassifier=" in str(x) for x in cmd)
    assert not any("-Dpackaging=" in str(x) for x in cmd)


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_download_maven_artifacts_cmd_with_classifier_and_packaging(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """_download_maven_artifacts adds -Dclassifier and -Dpackaging when provided."""
    deps_dir = rooted_tmp_path.path / "deps"
    deps_dir.mkdir()
    dependencies = {
        "https://repo.example.com/g/a/1.0/a-1.0-sources.jar": {
            "group_id": "g",
            "artifact_id": "a",
            "version": "1.0",
            "classifier": "sources",
            "artifact_type": "jar",
        },
    }

    maven_main._download_maven_artifacts(
        rooted_tmp_path,
        deps_dir,
        dependencies,
        {},
    )

    cmd = mock_run_cmd.call_args[0][0]
    assert "-Dclassifier=sources" in cmd


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_download_maven_artifacts_cmd_with_packaging_pom(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """_download_maven_artifacts adds -Dpackaging when type is not jar."""
    deps_dir = rooted_tmp_path.path / "deps"
    deps_dir.mkdir()
    dependencies = {
        "https://repo.example.com/g/a/1.0/a-1.0.pom": {
            "group_id": "g",
            "artifact_id": "a",
            "version": "1.0",
            "classifier": "",
            "artifact_type": "pom",
        },
    }

    maven_main._download_maven_artifacts(
        rooted_tmp_path,
        deps_dir,
        dependencies,
        {},
    )

    cmd = mock_run_cmd.call_args[0][0]
    assert "-Dpackaging=pom" in cmd


@mock.patch("hermeto.core.package_managers.maven.main.run_cmd")
def test_download_maven_artifacts_merges_dependencies_and_plugins(
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """_download_maven_artifacts runs run_cmd for both dependencies and plugins."""
    deps_dir = rooted_tmp_path.path / "deps"
    deps_dir.mkdir()
    dependencies = {
        "https://repo.example.com/dep.jar": {
            "group_id": "g1",
            "artifact_id": "a1",
            "version": "1.0",
            "classifier": None,
            "artifact_type": "jar",
        },
    }
    plugins = {
        "https://repo.example.com/plugin.jar": {
            "group_id": "g2",
            "artifact_id": "a2",
            "version": "2.0",
            "classifier": None,
            "artifact_type": "jar",
        },
    }

    maven_main._download_maven_artifacts(
        rooted_tmp_path,
        deps_dir,
        dependencies,
        plugins,
    )

    assert mock_run_cmd.call_count == 2
    calls = [mock_run_cmd.call_args_list[i][0][0] for i in range(2)]
    artifact_ids = set()
    for cmd in calls:
        for part in cmd:
            if part.startswith("-DartifactId="):
                artifact_ids.add(part.split("=", 1)[1])
                break
    assert artifact_ids == {"a1", "a2"}


# --- _save_checksums ---


def test_save_checksums_writes_checksum_file(rooted_tmp_path: RootedPath) -> None:
    """_save_checksums writes checksum file in Maven repo layout."""
    deps_dir = rooted_tmp_path.path / "deps"
    (deps_dir / "com" / "example" / "lib" / "1.0").mkdir(parents=True)
    url = "https://repo.example.com/com/example/lib/1.0/lib-1.0.jar"
    artifacts = {
        url: {
            "group_id": "com.example",
            "artifact_id": "lib",
            "version": "1.0",
            "checksum": "deadbeef",
            "checksum_algorithm": "SHA-256",
        },
    }

    maven_main._save_checksums(artifacts, deps_dir)

    path = deps_dir / "com" / "example" / "lib" / "1.0" / "lib-1.0.jar.sha256"
    assert path.exists()
    assert path.read_text() == "deadbeef"


def test_save_checksums_skips_when_no_checksum(rooted_tmp_path: RootedPath) -> None:
    """_save_checksums skips artifacts without checksum or algorithm."""
    deps_dir = rooted_tmp_path.path / "deps"
    deps_dir.mkdir()
    url = "https://repo.example.com/g/a/1.0/a-1.0.jar"
    artifacts = {
        url: {
            "group_id": "g",
            "artifact_id": "a",
            "version": "1.0",
            # no checksum / checksum_algorithm
        },
    }

    maven_main._save_checksums(artifacts, deps_dir)

    group_dir = deps_dir / "g" / "a" / "1.0"
    assert not group_dir.exists() or not list(group_dir.iterdir())


def test_save_checksums_uses_filename_from_url(rooted_tmp_path: RootedPath) -> None:
    """_save_checksums uses URL path filename when it matches artifact-version pattern."""
    deps_dir = rooted_tmp_path.path / "deps"
    (deps_dir / "com" / "example" / "foo" / "1.0").mkdir(parents=True)
    url = "https://repo.example.com/com/example/foo/1.0/foo-1.0.jar"
    artifacts = {
        url: {
            "group_id": "com.example",
            "artifact_id": "foo",
            "version": "1.0",
            "checksum": "cafe",
            "checksum_algorithm": "SHA-256",
        },
    }

    maven_main._save_checksums(artifacts, deps_dir)

    path = deps_dir / "com" / "example" / "foo" / "1.0" / "foo-1.0.jar.sha256"
    assert path.exists()


def test_save_checksums_constructs_filename_when_url_name_mismatch(
    rooted_tmp_path: RootedPath,
) -> None:
    """_save_checksums constructs artifact-version.ext when URL filename does not match."""
    deps_dir = rooted_tmp_path.path / "deps"
    (deps_dir / "com" / "example" / "bar" / "1.0").mkdir(parents=True)
    url = "https://repo.example.com/com/example/bar/1.0/something-else.jar"
    artifacts = {
        url: {
            "group_id": "com.example",
            "artifact_id": "bar",
            "version": "1.0",
            "checksum": "babe",
            "checksum_algorithm": "SHA-256",
        },
    }

    maven_main._save_checksums(artifacts, deps_dir)

    # Should use bar-1.0.jar (artifact-version.ext) because something-else.jar doesn't start with bar-1.0
    path = deps_dir / "com" / "example" / "bar" / "1.0" / "bar-1.0.jar.sha256"
    assert path.exists()
    assert path.read_text() == "babe"


def test_save_checksums_uses_sha1_algorithm(rooted_tmp_path: RootedPath) -> None:
    """_save_checksums writes .sha1 extension for SHA-1 algorithm."""
    deps_dir = rooted_tmp_path.path / "deps"
    (deps_dir / "g" / "a" / "1.0").mkdir(parents=True)
    url = "https://repo.example.com/g/a/1.0/a-1.0.jar"
    artifacts = {
        url: {
            "group_id": "g",
            "artifact_id": "a",
            "version": "1.0",
            "checksum": "abc",
            "checksum_algorithm": "SHA-1",
        },
    }

    maven_main._save_checksums(artifacts, deps_dir)

    path = deps_dir / "g" / "a" / "1.0" / "a-1.0.jar.sha1"
    assert path.exists()
    assert path.read_text() == "abc"
