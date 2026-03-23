# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for uv lockfile parser."""
from pathlib import Path

import pytest

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound
from hermeto.core.package_managers.uv.lockfile import (
    Artifact,
    Dependency,
    PackageSource,
    SourceType,
    UvPackage,
    parse_uv_lockfile,
)

TEST_DATA_DIR = Path(__file__).parent / "test_data"


class TestParseUvLockfile:
    """Tests for the parse_uv_lockfile function."""

    def test_parse_minimal_lockfile(self):
        lockfile = parse_uv_lockfile(TEST_DATA_DIR / "minimal_uv.lock")

        assert lockfile.version == 1
        assert lockfile.requires_python == ">=3.12"
        assert not lockfile.is_workspace

        # Should have 7 packages: my-project + certifi + charset-normalizer + idna + requests + urllib3 + pytest
        assert len(lockfile.packages) == 7

        # Check the project package
        project = lockfile.get_package("my-project")
        assert project is not None
        assert project.is_editable
        assert project.version == "0.1.0"
        assert len(project.dependencies) == 1
        assert project.dependencies[0].name == "requests"

        # Check dev deps on the project
        assert "dev" in project.dev_dependencies
        assert len(project.dev_dependencies["dev"]) == 1
        assert project.dev_dependencies["dev"][0].name == "pytest"

    def test_parse_registry_package(self):
        lockfile = parse_uv_lockfile(TEST_DATA_DIR / "minimal_uv.lock")

        requests_pkg = lockfile.get_package("requests")
        assert requests_pkg is not None
        assert requests_pkg.is_registry
        assert requests_pkg.version == "2.32.3"
        assert requests_pkg.sdist is not None
        assert requests_pkg.sdist.url.endswith("requests-2.32.3.tar.gz")
        assert len(requests_pkg.wheels) == 1
        assert len(requests_pkg.dependencies) == 4

    def test_parse_git_source(self):
        lockfile = parse_uv_lockfile(TEST_DATA_DIR / "git_source_uv.lock")

        git_pkg = lockfile.get_package("my-git-dep")
        assert git_pkg is not None
        assert git_pkg.is_git
        assert "abc123def456" in git_pkg.source.url

    def test_parse_url_source(self):
        lockfile = parse_uv_lockfile(TEST_DATA_DIR / "url_source_uv.lock")

        url_pkg = lockfile.get_package("my-url-dep")
        assert url_pkg is not None
        assert url_pkg.is_url
        assert url_pkg.source.url == "https://example.com/packages/my-url-dep-2.0.0.whl"

    def test_parse_workspace(self):
        lockfile = parse_uv_lockfile(TEST_DATA_DIR / "workspace_uv.lock")

        assert lockfile.is_workspace
        assert lockfile.manifest_members == ["pkg-a", "pkg-b"]

        # Editable workspace members
        pkg_a = lockfile.get_package("pkg-a")
        assert pkg_a is not None
        assert pkg_a.is_editable

    def test_unsupported_version_raises(self):
        with pytest.raises(InvalidLockfileFormat, match="Unsupported lockfile version"):
            parse_uv_lockfile(TEST_DATA_DIR / "bad_version_uv.lock")

    def test_missing_lockfile_raises(self):
        with pytest.raises(LockfileNotFound):
            parse_uv_lockfile(Path("/nonexistent/uv.lock"))


class TestPackageSource:
    """Tests for PackageSource.from_dict."""

    def test_registry_source(self):
        source = PackageSource.from_dict({"registry": "https://pypi.org/simple"})
        assert source.source_type == SourceType.REGISTRY
        assert source.url == "https://pypi.org/simple"

    def test_git_source(self):
        source = PackageSource.from_dict({"git": "https://github.com/org/repo.git?tag=v1#abc"})
        assert source.source_type == SourceType.GIT

    def test_directory_source(self):
        source = PackageSource.from_dict({"directory": "../local-pkg"})
        assert source.source_type == SourceType.DIRECTORY

    def test_editable_source(self):
        source = PackageSource.from_dict({"editable": "."})
        assert source.source_type == SourceType.EDITABLE

    def test_virtual_source(self):
        source = PackageSource.from_dict({"virtual": "."})
        assert source.source_type == SourceType.VIRTUAL

    def test_unknown_source_raises(self):
        with pytest.raises(InvalidLockfileFormat, match="Unknown source type"):
            PackageSource.from_dict({"weird": "something"})


class TestUvPackageProperties:
    """Tests for UvPackage property helpers."""

    def _make_pkg(self, source_type: SourceType) -> UvPackage:
        return UvPackage(
            name="test",
            version="1.0.0",
            source=PackageSource(source_type=source_type),
        )

    def test_is_registry(self):
        assert self._make_pkg(SourceType.REGISTRY).is_registry

    def test_is_git(self):
        assert self._make_pkg(SourceType.GIT).is_git

    def test_is_url(self):
        assert self._make_pkg(SourceType.URL).is_url

    def test_is_path(self):
        assert self._make_pkg(SourceType.DIRECTORY).is_path

    def test_is_editable_for_editable(self):
        assert self._make_pkg(SourceType.EDITABLE).is_editable

    def test_is_editable_for_virtual(self):
        assert self._make_pkg(SourceType.VIRTUAL).is_editable


class TestArtifact:
    """Tests for Artifact parsing."""

    def test_from_dict_full(self):
        data = {"url": "https://example.com/pkg.whl", "hash": "sha256:abc123", "size": 1234}
        artifact = Artifact.from_dict(data)
        assert artifact.url == "https://example.com/pkg.whl"
        assert artifact.hash == "sha256:abc123"
        assert artifact.size == 1234

    def test_from_dict_minimal(self):
        artifact = Artifact.from_dict({})
        assert artifact.url == ""
        assert artifact.hash == ""
        assert artifact.size == 0


class TestDependency:
    """Tests for Dependency parsing."""

    def test_from_dict_with_marker(self):
        dep = Dependency.from_dict({"name": "foo", "marker": "sys_platform == 'win32'"})
        assert dep.name == "foo"
        assert dep.marker == "sys_platform == 'win32'"

    def test_from_dict_without_marker(self):
        dep = Dependency.from_dict({"name": "bar"})
        assert dep.name == "bar"
        assert dep.marker is None
