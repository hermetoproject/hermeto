import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import MavenPackageInput, Request
from hermeto.core.models.output import RequestOutput
from hermeto.core.package_managers.maven.main import (
    MavenDependency,
    MavenLockfile,
    fetch_maven_source,
    _convert_java_checksum_algorithm_to_python,
)
from hermeto.core.rooted_path import RootedPath


def test_maven_dependency_properties():
    """Test that MavenDependency correctly parses dependency properties."""
    dep_dict = {
        "groupId": "org.example",
        "artifactId": "test-artifact",
        "version": "1.0.0",
        "scope": "compile",
        "checksum": "abc123",
        "checksumAlgorithm": "SHA-256",
        "resolved": "https://repo.maven.apache.org/maven2/org/example/test-artifact/1.0.0/test-artifact-1.0.0.jar",
        "included": True,
        "children": [],
    }
    
    dep = MavenDependency(dep_dict)
    
    assert dep.group_id == "org.example"
    assert dep.artifact_id == "test-artifact"
    assert dep.version == "1.0.0"
    assert dep.name == "org.example:test-artifact"
    assert dep.scope == "compile"
    assert dep.checksum == "abc123"
    assert dep.checksum_algorithm == "SHA-256"
    assert dep.resolved_url == "https://repo.maven.apache.org/maven2/org/example/test-artifact/1.0.0/test-artifact-1.0.0.jar"
    assert dep.included is True
    assert len(dep.children) == 0


def test_maven_dependency_to_component_info():
    """Test that MavenDependency.to_component_info() creates correct component info."""
    dep_dict = {
        "groupId": "org.example",
        "artifactId": "test-artifact",
        "version": "1.0.0",
        "scope": "compile",
        "checksum": "abc123",
        "checksumAlgorithm": "SHA-256",
        "resolved": "https://repo.maven.apache.org/maven2/org/example/test-artifact/1.0.0/test-artifact-1.0.0.jar",
        "included": True,
        "children": [],
    }
    
    dep = MavenDependency(dep_dict)
    component_info = dep.to_component_info()
    
    assert component_info["name"] == "org.example:test-artifact"
    assert component_info["version"] == "1.0.0"
    assert component_info["scope"] == "compile"
    assert component_info["included"] is True
    assert component_info["purl"] == "pkg:maven/org.example/test-artifact@1.0.0"
    assert component_info["missing_hash_in_file"] is None


def test_maven_dependency_missing_checksum():
    """Test that MavenDependency reports missing checksum correctly."""
    dep_dict = {
        "groupId": "org.example",
        "artifactId": "test-artifact",
        "version": "1.0.0",
        "scope": "compile",
        "resolved": "https://repo.maven.apache.org/maven2/org/example/test-artifact/1.0.0/test-artifact-1.0.0.jar",
        "included": True,
        "children": [],
    }
    
    dep = MavenDependency(dep_dict)
    component_info = dep.to_component_info()
    
    assert component_info["missing_hash_in_file"] == Path("lockfile.json")


def test_convert_java_checksum_algorithm_to_python():
    """Test that Java MessageDigest algorithm names are converted to Python hashlib names."""
    # Test supported algorithms
    assert _convert_java_checksum_algorithm_to_python("SHA-256") == "sha256"
    assert _convert_java_checksum_algorithm_to_python("SHA-1") == "sha1"
    assert _convert_java_checksum_algorithm_to_python("SHA-512") == "sha512"
    assert _convert_java_checksum_algorithm_to_python("SHA-224") == "sha224"
    assert _convert_java_checksum_algorithm_to_python("SHA-384") == "sha384"
    assert _convert_java_checksum_algorithm_to_python("MD5") == "md5"
    
    # Test unsupported algorithm
    with pytest.raises(PackageRejected) as exc_info:
        _convert_java_checksum_algorithm_to_python("UNSUPPORTED-ALGORITHM")
    assert "Unsupported checksum algorithm: UNSUPPORTED-ALGORITHM" in str(exc_info.value)


def test_maven_lockfile_from_file_missing(tmp_path):
    """Test that MavenLockfile.from_file raises PackageRejected when file is missing."""
    lockfile_path = RootedPath(tmp_path).join_within_root("lockfile.json")
    
    with pytest.raises(PackageRejected):
        MavenLockfile.from_file(lockfile_path)


def test_maven_lockfile_from_file_invalid_json(tmp_path):
    """Test that MavenLockfile.from_file raises UnexpectedFormat for invalid JSON."""
    lockfile_path = RootedPath(tmp_path).join_within_root("lockfile.json")
    lockfile_path.path.write_text("invalid json")
    
    with pytest.raises(Exception):  # Should raise UnexpectedFormat or similar
        MavenLockfile.from_file(lockfile_path)


def test_maven_lockfile_basic_parsing(tmp_path):
    """Test basic parsing of a Maven lockfile."""
    lockfile_data = {
        "artifactId": "test-project",
        "groupId": "org.example",
        "version": "1.0.0-SNAPSHOT",
        "lockFileVersion": 1,
        "dependencies": [
            {
                "groupId": "org.example",
                "artifactId": "test-dep",
                "version": "1.0.0",
                "scope": "compile",
                "checksum": "abc123",
                "checksumAlgorithm": "SHA-256",
                "resolved": "https://repo.maven.apache.org/maven2/org/example/test-dep/1.0.0/test-dep-1.0.0.jar",
                "included": True,
                "children": [],
            }
        ],
    }
    
    lockfile_path = RootedPath(tmp_path).join_within_root("lockfile.json")
    lockfile_path.path.write_text(json.dumps(lockfile_data))
    
    lockfile = MavenLockfile.from_file(lockfile_path)
    
    assert len(lockfile.dependencies) == 1
    assert lockfile.main_package_info["groupId"] == "org.example"
    assert lockfile.main_package_info["artifactId"] == "test-project"
    assert lockfile.main_package_info["version"] == "1.0.0-SNAPSHOT"


def test_maven_lockfile_nested_dependencies(tmp_path):
    """Test parsing of nested dependencies."""
    lockfile_data = {
        "artifactId": "test-project",
        "groupId": "org.example",
        "version": "1.0.0-SNAPSHOT",
        "lockFileVersion": 1,
        "dependencies": [
            {
                "groupId": "org.example",
                "artifactId": "parent-dep",
                "version": "1.0.0",
                "scope": "compile",
                "checksum": "abc123",
                "checksumAlgorithm": "SHA-256",
                "resolved": "https://repo.maven.apache.org/maven2/org/example/parent-dep/1.0.0/parent-dep-1.0.0.jar",
                "included": True,
                "children": [
                    {
                        "groupId": "org.example",
                        "artifactId": "child-dep",
                        "version": "2.0.0",
                        "scope": "compile",
                        "checksum": "def456",
                        "checksumAlgorithm": "SHA-256",
                        "resolved": "https://repo.maven.apache.org/maven2/org/example/child-dep/2.0.0/child-dep-2.0.0.jar",
                        "included": True,
                        "children": [],
                    }
                ],
            }
        ],
    }
    
    lockfile_path = RootedPath(tmp_path).join_within_root("lockfile.json")
    lockfile_path.path.write_text(json.dumps(lockfile_data))
    
    lockfile = MavenLockfile.from_file(lockfile_path)
    
    # Should have 2 dependencies total (parent + child)
    assert len(lockfile.dependencies) == 2
    
    # Check that both dependencies are correctly parsed
    dep_names = [dep.name for dep in lockfile.dependencies]
    assert "org.example:parent-dep" in dep_names
    assert "org.example:child-dep" in dep_names


@patch("hermeto.core.package_managers.maven.main.asyncio.run")
@patch("hermeto.core.package_managers.maven.main.must_match_any_checksum")
def test_fetch_maven_source_basic(mock_checksum, mock_asyncio_run, tmp_path):
    """Test basic functionality of fetch_maven_source."""
    # Create a test lockfile
    lockfile_data = {
        "artifactId": "test-project",
        "groupId": "org.example",
        "version": "1.0.0-SNAPSHOT",
        "lockFileVersion": 1,
        "dependencies": [
            {
                "groupId": "org.example",
                "artifactId": "test-dep",
                "version": "1.0.0",
                "scope": "compile",
                "checksum": "abc123",
                "checksumAlgorithm": "SHA-256",
                "resolved": "https://repo.maven.apache.org/maven2/org/example/test-dep/1.0.0/test-dep-1.0.0.jar",
                "included": True,
                "children": [],
            }
        ],
    }
    
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    
    lockfile_path = source_dir / "lockfile.json"
    lockfile_path.write_text(json.dumps(lockfile_data))
    
    # Create test request
    request = Request(
        source_dir=tmp_path / "source",
        output_dir=tmp_path / "output",
        packages=[MavenPackageInput(type="maven", path=Path("."))],
    )
    
    # Mock async download
    mock_asyncio_run.return_value = None
    
    # Call fetch_maven_source
    result = fetch_maven_source(request)
    
    # Verify results
    assert isinstance(result, RequestOutput)
    assert len(result.components) == 2  # main package + 1 dependency
    assert len(result.build_config.project_files) == 1  # lockfile
    
    # Check component names
    component_names = [comp.name for comp in result.components]
    assert "org.example:test-project" in component_names
    assert "org.example:test-dep" in component_names


def test_fetch_maven_source_missing_lockfile(tmp_path):
    """Test that fetch_maven_source raises PackageRejected when lockfile is missing."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    
    # Create test request without lockfile
    request = Request(
        source_dir=tmp_path / "source",
        output_dir=tmp_path / "output",
        packages=[MavenPackageInput(type="maven", path=Path("."))],
    )
    
    # Should raise PackageRejected
    with pytest.raises(PackageRejected):
        fetch_maven_source(request) 