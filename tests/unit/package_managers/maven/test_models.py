# SPDX-License-Identifier: GPL-3.0-or-later
import json
from typing import Any

import pytest

from hermeto.core.package_managers.maven.models import (
    MavenComponent,
    MavenDependency,
    MavenLockfile,
)
from hermeto.core.rooted_path import RootedPath


# --- MavenComponent ---


def test_maven_component_construction() -> None:
    """MavenComponent stores name, purl, version, and scope."""
    component = MavenComponent(
        name="com.example:my-lib",
        purl="pkg:maven/com.example/my-lib@1.0.0",
        version="1.0.0",
        scope="compile",
    )
    assert component.name == "com.example:my-lib"
    assert component.purl == "pkg:maven/com.example/my-lib@1.0.0"
    assert component.version == "1.0.0"
    assert component.scope == "compile"


def test_maven_component_with_test_scope() -> None:
    """MavenComponent can have scope other than compile."""
    component = MavenComponent(
        name="com.example:test-lib",
        purl="pkg:maven/com.example/test-lib@2.0.0",
        version="2.0.0",
        scope="test",
    )
    assert component.scope == "test"


# --- MavenDependency ---


def _minimal_dependency_dict(
    group_id: str = "com.example",
    artifact_id: str = "my-artifact",
    version: str = "1.0.0",
    **kwargs: Any,
) -> dict[str, Any]:
    """Build minimal dependency dict with optional overrides."""
    d: dict[str, Any] = {
        "groupId": group_id,
        "artifactId": artifact_id,
        "version": version,
    }
    d.update(kwargs)
    return d


def test_maven_dependency_minimal() -> None:
    """MavenDependency reads groupId, artifactId, version from dict."""
    d = _minimal_dependency_dict()
    dep = MavenDependency(d)
    assert dep.group_id == "com.example"
    assert dep.artifact_id == "my-artifact"
    assert dep.version == "1.0.0"
    assert dep.name == "com.example:my-artifact"
    assert dep.classifier is None
    assert dep.artifact_type == "jar"
    assert dep.scope == "compile"
    assert dep.checksum is None
    assert dep.checksum_algorithm is None
    assert dep.resolved_url is None
    assert dep.children == []


def test_maven_dependency_with_classifier() -> None:
    """MavenDependency returns classifier when present."""
    d = _minimal_dependency_dict(classifier="sources")
    dep = MavenDependency(d)
    assert dep.classifier == "sources"


def test_maven_dependency_artifact_type_default() -> None:
    """MavenDependency defaults artifact type to jar when absent."""
    d = _minimal_dependency_dict()
    dep = MavenDependency(d)
    assert dep.artifact_type == "jar"


def test_maven_dependency_artifact_type_explicit() -> None:
    """MavenDependency returns type when present."""
    d = _minimal_dependency_dict(type="pom")
    dep = MavenDependency(d)
    assert dep.artifact_type == "pom"


def test_maven_dependency_scope_default() -> None:
    """MavenDependency defaults scope to compile when absent."""
    d = _minimal_dependency_dict()
    dep = MavenDependency(d)
    assert dep.scope == "compile"


def test_maven_dependency_scope_explicit() -> None:
    """MavenDependency returns scope when present."""
    d = _minimal_dependency_dict(scope="test")
    dep = MavenDependency(d)
    assert dep.scope == "test"


def test_maven_dependency_checksum_simple() -> None:
    """MavenDependency returns checksum when present."""
    d = _minimal_dependency_dict(checksum="sha256:abc123")
    dep = MavenDependency(d)
    assert dep.checksum == "sha256:abc123"


def test_maven_dependency_checksum_with_extra_text() -> None:
    """MavenDependency strips extra text after checksum (takes first token)."""
    d = _minimal_dependency_dict(checksum="sha256:abc123  some-extra-info")
    dep = MavenDependency(d)
    assert dep.checksum == "sha256:abc123"


def test_maven_dependency_checksum_algorithm() -> None:
    """MavenDependency returns checksumAlgorithm when present."""
    d = _minimal_dependency_dict(checksumAlgorithm="sha256")
    dep = MavenDependency(d)
    assert dep.checksum_algorithm == "sha256"


def test_maven_dependency_resolved_url() -> None:
    """MavenDependency returns resolved URL when present."""
    url = "https://repo.example.com/com/example/art/1.0.0/art-1.0.0.jar"
    d = _minimal_dependency_dict(resolved=url)
    dep = MavenDependency(d)
    assert dep.resolved_url == url


def test_maven_dependency_children() -> None:
    """MavenDependency returns children list when present."""
    child = _minimal_dependency_dict(group_id="com.other", artifact_id="child", version="0.1")
    d = _minimal_dependency_dict(children=[child])
    dep = MavenDependency(d)
    assert len(dep.children) == 1
    assert dep.children[0]["groupId"] == "com.other"
    assert dep.children[0]["artifactId"] == "child"
    assert dep.children[0]["version"] == "0.1"


def test_maven_dependency_to_component_minimal() -> None:
    """to_component produces MavenComponent with correct purl."""
    d = _minimal_dependency_dict()
    dep = MavenDependency(d)
    comp = dep.to_component()
    assert comp.name == "com.example:my-artifact"
    assert comp.version == "1.0.0"
    assert comp.scope == "compile"
    assert comp.purl == "pkg:maven/com.example/my-artifact@1.0.0"


def test_maven_dependency_to_component_with_classifier() -> None:
    """to_component includes classifier in purl qualifiers."""
    d = _minimal_dependency_dict(classifier="sources")
    dep = MavenDependency(d)
    comp = dep.to_component()
    assert "classifier=sources" in comp.purl


def test_maven_dependency_to_component_with_non_jar_type() -> None:
    """to_component includes type in purl qualifiers when not jar."""
    d = _minimal_dependency_dict(type="pom")
    dep = MavenDependency(d)
    comp = dep.to_component()
    assert "type=pom" in comp.purl


def test_maven_dependency_to_component_scope_preserved() -> None:
    """to_component preserves dependency scope."""
    d = _minimal_dependency_dict(scope="runtime")
    dep = MavenDependency(d)
    comp = dep.to_component()
    assert comp.scope == "runtime"


# --- MavenLockfile ---


def _minimal_lockfile_data(
    group_id: str = "com.myapp",
    artifact_id: str = "my-app",
    version: str = "2.0.0",
    dependencies: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build minimal lockfile data with optional overrides."""
    data: dict[str, Any] = {
        "groupId": group_id,
        "artifactId": artifact_id,
        "version": version,
        "dependencies": dependencies if dependencies is not None else [],
    }
    data.update(kwargs)
    return data


def test_maven_lockfile_construction(rooted_tmp_path: RootedPath) -> None:
    """MavenLockfile stores path and data and parses dependencies."""
    data = _minimal_lockfile_data()
    lockfile = MavenLockfile(rooted_tmp_path, data)
    assert lockfile.lockfile_path == rooted_tmp_path
    assert lockfile.lockfile_data == data
    assert lockfile.dependencies == []


def test_maven_lockfile_parse_dependencies_flat(rooted_tmp_path: RootedPath) -> None:
    """MavenLockfile parses flat list of dependencies."""
    data = _minimal_lockfile_data(
        dependencies=[
            _minimal_dependency_dict(group_id="g1", artifact_id="a1", version="1.0"),
            _minimal_dependency_dict(group_id="g2", artifact_id="a2", version="2.0"),
        ]
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    assert len(lockfile.dependencies) == 2
    assert lockfile.dependencies[0].name == "g1:a1"
    assert lockfile.dependencies[1].name == "g2:a2"


def test_maven_lockfile_parse_dependencies_nested(rooted_tmp_path: RootedPath) -> None:
    """MavenLockfile recursively parses children into flat dependencies list."""
    data = _minimal_lockfile_data(
        dependencies=[
            {
                "groupId": "parent",
                "artifactId": "parent-art",
                "version": "1.0",
                "children": [
                    {
                        "groupId": "child",
                        "artifactId": "child-art",
                        "version": "0.5",
                    },
                ],
            },
        ]
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    assert len(lockfile.dependencies) == 2
    assert lockfile.dependencies[0].name == "parent:parent-art"
    assert lockfile.dependencies[1].name == "child:child-art"


def test_maven_lockfile_from_file(rooted_tmp_path: RootedPath) -> None:
    """MavenLockfile.from_file loads JSON from path."""
    data = _minimal_lockfile_data()
    lockfile_path = rooted_tmp_path.join_within_root("lockfile.json")
    lockfile_path.path.write_text(json.dumps(data))

    lockfile = MavenLockfile.from_file(lockfile_path)
    assert lockfile.lockfile_path == lockfile_path
    assert lockfile.lockfile_data == data
    assert lockfile.dependencies == []


def test_maven_lockfile_get_main_package(rooted_tmp_path: RootedPath) -> None:
    """get_main_package returns MavenComponent from lockfile groupId/artifactId/version."""
    data = _minimal_lockfile_data(
        group_id="org.myorg",
        artifact_id="my-service",
        version="3.1.0",
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    main = lockfile.get_main_package()
    assert main.name == "org.myorg:my-service"
    assert main.version == "3.1.0"
    assert main.scope == "compile"
    assert main.purl == "pkg:maven/org.myorg/my-service@3.1.0"


def test_maven_lockfile_get_sbom_components(rooted_tmp_path: RootedPath) -> None:
    """get_sbom_components returns list of MavenComponent from dependencies."""
    data = _minimal_lockfile_data(
        dependencies=[
            _minimal_dependency_dict(group_id="org.apache.maven", artifact_id="test-libs", version="3.9.11"),
        ]
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    components = lockfile.get_sbom_components()
    assert len(components) == 1
    assert components[0].name == "org.apache.maven:test-libs"
    assert components[0].purl == "pkg:maven/org.apache.maven/test-libs@3.9.11"


def test_maven_lockfile_get_dependencies_to_download_empty(rooted_tmp_path: RootedPath) -> None:
    """get_dependencies_to_download returns empty dict when no resolved URLs."""
    data = _minimal_lockfile_data(
        dependencies=[_minimal_dependency_dict()],  # no resolved
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    result = lockfile.get_dependencies_to_download()
    assert result == {}


def test_maven_lockfile_get_dependencies_to_download_with_resolved(
    rooted_tmp_path: RootedPath,
) -> None:
    """get_dependencies_to_download includes only deps with resolved URL."""
    url = "https://repo.example.com/org/apache/maven/test-libs/3.9.11/test-libs-3.9.11.jar"
    data = _minimal_lockfile_data(
        dependencies=[
            _minimal_dependency_dict(group_id="org.apache.maven", artifact_id="test-libs", version="3.9.11", resolved=url),
        ]
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    result = lockfile.get_dependencies_to_download()
    assert url in result
    assert result[url]["group_id"] == "org.apache.maven"
    assert result[url]["artifact_id"] == "test-libs"
    assert result[url]["version"] == "3.9.11"
    assert result[url]["classifier"] is None
    assert result[url]["artifact_type"] == "jar"


def test_maven_lockfile_get_dependencies_to_download_includes_checksum(
    rooted_tmp_path: RootedPath,
) -> None:
    """get_dependencies_to_download includes checksum and algorithm."""
    url = "https://repo.example.com/org/apache/maven/test-libs/3.9.11/test-libs-3.9.11.jar"
    data = _minimal_lockfile_data(
        dependencies=[
            _minimal_dependency_dict(
                group_id="org.apache.maven",
                artifact_id="test-libs",
                version="3.9.11",
                resolved=url,
                checksum="sha256:deadbeef",
                checksumAlgorithm="sha256",
            ),
        ]
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    result = lockfile.get_dependencies_to_download()
    assert result[url]["checksum"] == "sha256:deadbeef"
    assert result[url]["checksum_algorithm"] == "sha256"


def test_maven_lockfile_get_plugins_to_download_empty(rooted_tmp_path: RootedPath) -> None:
    """get_plugins_to_download returns empty dict when no mavenPlugins."""
    data = _minimal_lockfile_data()
    lockfile = MavenLockfile(rooted_tmp_path, data)
    result = lockfile.get_plugins_to_download()
    assert result == {}


def test_maven_lockfile_get_plugins_to_download_plugin_entry(rooted_tmp_path: RootedPath) -> None:
    """get_plugins_to_download includes plugin entries from mavenPlugins."""
    url = "https://repo.example.com/org/apache/maven/plugins/maven-compiler-plugin/3.0/maven-compiler-plugin-3.0.jar"
    data = _minimal_lockfile_data(
        mavenPlugins=[
            {
                "groupId": "org.apache.maven.plugins",
                "artifactId": "maven-compiler-plugin",
                "version": "3.0",
                "resolved": url,
                "checksum": "sha256:abc",
                "checksumAlgorithm": "sha256",
            },
        ]
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    result = lockfile.get_plugins_to_download()
    assert url in result
    assert result[url]["group_id"] == "org.apache.maven.plugins"
    assert result[url]["artifact_id"] == "maven-compiler-plugin"
    assert result[url]["version"] == "3.0"
    assert result[url]["checksum"] == "sha256:abc"


def test_maven_lockfile_get_plugins_to_download_plugin_dependencies(
    rooted_tmp_path: RootedPath,
) -> None:
    """get_plugins_to_download recursively includes plugin dependencies."""
    plugin_url = "https://repo.example.com/org/plugin/my-plugin/1.0/my-plugin-1.0.jar"
    dep_url = "https://repo.example.com/org/dep/plugin-dep/0.1/plugin-dep-0.1.jar"
    data = _minimal_lockfile_data(
        mavenPlugins=[
            {
                "groupId": "org.plugin",
                "artifactId": "my-plugin",
                "version": "1.0",
                "resolved": plugin_url,
                "dependencies": [
                    {
                        "groupId": "org.dep",
                        "artifactId": "plugin-dep",
                        "version": "0.1",
                        "resolved": dep_url,
                    },
                ],
            },
        ]
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    result = lockfile.get_plugins_to_download()
    assert plugin_url in result
    assert dep_url in result
    assert result[dep_url]["artifact_id"] == "plugin-dep"


def test_maven_lockfile_get_plugins_to_download_plugin_dependencies_nested(
    rooted_tmp_path: RootedPath,
) -> None:
    """get_plugins_to_download recursively includes plugin dependencies."""
    plugin_url = "https://repo.example.com/org/plugin/my-plugin/1.0/my-plugin-1.0.jar"
    dep_url = "https://repo.example.com/org/dep/plugin-dep/0.1/plugin-dep-0.1.jar"
    dep_child_url = "https://repo.example.com/org/dep2/plugin-dep2/0.2/plugin-dep2-0.2.jar"
    data = _minimal_lockfile_data(
        mavenPlugins=[
            {
                "groupId": "org.plugin",
                "artifactId": "my-plugin",
                "version": "1.0",
                "resolved": plugin_url,
                "dependencies": [
                    {
                        "groupId": "org.dep",
                        "artifactId": "plugin-dep",
                        "version": "0.1",
                        "resolved": dep_url,
                        "children": [
                            {
                                "groupId": "org.dep2",
                                "artifactId": "plugin-dep2",
                                "version": "0.2",
                                "resolved": dep_child_url,
                            },
                        ],
                    },
                ],
            },
        ]
    )
    lockfile = MavenLockfile(rooted_tmp_path, data)
    result = lockfile.get_plugins_to_download()
    assert plugin_url in result
    assert dep_child_url in result
    assert result[dep_child_url]["artifact_id"] == "plugin-dep2"
