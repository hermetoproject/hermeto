# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for uv package manager backend (main.py)."""
from pathlib import Path
from unittest import mock

import pytest

from hermeto.core.errors import PackageRejected
from hermeto.core.package_managers.uv.lockfile import (
    Artifact,
    Dependency,
    PackageSource,
    SourceType,
    UvLockfile,
    UvPackage,
)
from hermeto.core.package_managers.uv.main import (
    _filter_packages,
    _get_dev_only_packages,
    _parse_git_source_url,
    _parse_hash,
    _url_to_filename,
    _validate_lockfile_freshness,
)


class TestFilterPackages:
    """Tests for _filter_packages."""

    def _make_lockfile(self, packages: list[UvPackage]) -> UvLockfile:
        return UvLockfile(version=1, requires_python=">=3.12", packages=packages)

    def _registry_pkg(self, name: str, deps=None) -> UvPackage:
        return UvPackage(
            name=name,
            version="1.0.0",
            source=PackageSource(source_type=SourceType.REGISTRY),
            dependencies=deps or [],
        )

    def _editable_pkg(self, name: str, deps=None, dev_deps=None) -> UvPackage:
        return UvPackage(
            name=name,
            version="0.1.0",
            source=PackageSource(source_type=SourceType.VIRTUAL, url="."),
            dependencies=deps or [],
            dev_dependencies=dev_deps or {},
        )

    def _path_pkg(self, name: str) -> UvPackage:
        return UvPackage(
            name=name,
            version="0.1.0",
            source=PackageSource(source_type=SourceType.DIRECTORY, url="../local"),
        )

    def test_skips_editable_packages(self):
        project = self._editable_pkg("my-project", deps=[Dependency(name="requests")])
        requests = self._registry_pkg("requests")
        lockfile = self._make_lockfile([project, requests])

        result = _filter_packages(lockfile, include_dev=True)
        names = [p.name for p in result]
        assert "my-project" not in names
        assert "requests" in names

    def test_rejects_path_dependencies(self):
        project = self._editable_pkg("my-project", deps=[Dependency(name="local-pkg")])
        local = self._path_pkg("local-pkg")
        lockfile = self._make_lockfile([project, local])

        with pytest.raises(PackageRejected, match="Path dependency"):
            _filter_packages(lockfile, include_dev=True)

    def test_filters_dev_only_packages(self):
        project = self._editable_pkg(
            "my-project",
            deps=[Dependency(name="requests")],
            dev_deps={"dev": [Dependency(name="pytest")]},
        )
        requests = self._registry_pkg("requests")
        pytest_pkg = self._registry_pkg("pytest")
        lockfile = self._make_lockfile([project, requests, pytest_pkg])

        # With include_dev=False, pytest should be excluded
        result = _filter_packages(lockfile, include_dev=False)
        names = [p.name for p in result]
        assert "requests" in names
        assert "pytest" not in names

    def test_includes_dev_packages_when_requested(self):
        project = self._editable_pkg(
            "my-project",
            deps=[Dependency(name="requests")],
            dev_deps={"dev": [Dependency(name="pytest")]},
        )
        requests = self._registry_pkg("requests")
        pytest_pkg = self._registry_pkg("pytest")
        lockfile = self._make_lockfile([project, requests, pytest_pkg])

        result = _filter_packages(lockfile, include_dev=True)
        names = [p.name for p in result]
        assert "requests" in names
        assert "pytest" in names


class TestGetDevOnlyPackages:
    """Tests for _get_dev_only_packages."""

    def test_identifies_dev_only_packages(self):
        project = UvPackage(
            name="my-project",
            version="0.1.0",
            source=PackageSource(source_type=SourceType.VIRTUAL, url="."),
            dependencies=[Dependency(name="requests")],
            dev_dependencies={"dev": [Dependency(name="pytest")]},
        )
        requests = UvPackage(
            name="requests",
            version="2.32.3",
            source=PackageSource(source_type=SourceType.REGISTRY),
            dependencies=[Dependency(name="certifi")],
        )
        certifi = UvPackage(
            name="certifi",
            version="2024.2.2",
            source=PackageSource(source_type=SourceType.REGISTRY),
        )
        pytest_pkg = UvPackage(
            name="pytest",
            version="8.2.0",
            source=PackageSource(source_type=SourceType.REGISTRY),
            dependencies=[Dependency(name="pluggy")],
        )
        pluggy = UvPackage(
            name="pluggy",
            version="1.5.0",
            source=PackageSource(source_type=SourceType.REGISTRY),
        )

        lockfile = UvLockfile(
            version=1,
            requires_python=">=3.12",
            packages=[project, requests, certifi, pytest_pkg, pluggy],
        )

        dev_only = _get_dev_only_packages(lockfile)

        # pytest and pluggy are only reachable via dev path
        assert "pytest" in dev_only
        assert "pluggy" in dev_only
        # requests and certifi are prod deps
        assert "requests" not in dev_only
        assert "certifi" not in dev_only

    def test_shared_dep_not_dev_only(self):
        """A dependency used by both prod and dev should NOT be dev-only."""
        project = UvPackage(
            name="my-project",
            version="0.1.0",
            source=PackageSource(source_type=SourceType.VIRTUAL, url="."),
            dependencies=[Dependency(name="shared-lib")],
            dev_dependencies={"dev": [Dependency(name="shared-lib")]},
        )
        shared = UvPackage(
            name="shared-lib",
            version="1.0.0",
            source=PackageSource(source_type=SourceType.REGISTRY),
        )

        lockfile = UvLockfile(
            version=1, requires_python=">=3.12", packages=[project, shared]
        )

        dev_only = _get_dev_only_packages(lockfile)
        assert "shared-lib" not in dev_only

    def test_no_project_package(self):
        """When there's no editable project, return empty set."""
        pkg = UvPackage(
            name="solo",
            version="1.0.0",
            source=PackageSource(source_type=SourceType.REGISTRY),
        )
        lockfile = UvLockfile(version=1, requires_python=">=3.12", packages=[pkg])

        assert _get_dev_only_packages(lockfile) == set()


class TestValidateLockfileFreshness:
    """Tests for _validate_lockfile_freshness."""

    def test_fresh_lockfile_passes(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\ndependencies = ["requests>=2.32"]\n'
        )

        lockfile = UvLockfile(
            version=1,
            requires_python=">=3.12",
            packages=[
                UvPackage(
                    name="requests",
                    version="2.32.3",
                    source=PackageSource(source_type=SourceType.REGISTRY),
                ),
            ],
        )

        # Should not raise
        _validate_lockfile_freshness(pyproject, lockfile)

    def test_stale_lockfile_raises(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\ndependencies = ["requests>=2.32", "flask>=3.0"]\n'
        )

        lockfile = UvLockfile(
            version=1,
            requires_python=">=3.12",
            packages=[
                UvPackage(
                    name="requests",
                    version="2.32.3",
                    source=PackageSource(source_type=SourceType.REGISTRY),
                ),
            ],
        )

        with pytest.raises(PackageRejected, match="flask"):
            _validate_lockfile_freshness(pyproject, lockfile)

    def test_empty_deps_skips_check(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\n")

        lockfile = UvLockfile(version=1, requires_python=">=3.12", packages=[])

        # Should not raise (no declared deps to check)
        _validate_lockfile_freshness(pyproject, lockfile)

    def test_bad_pyproject_skips_check(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("this is not valid toml {{{")

        lockfile = UvLockfile(version=1, requires_python=">=3.12", packages=[])

        # Should not raise (gracefully skips)
        _validate_lockfile_freshness(pyproject, lockfile)

    def test_dependency_groups_checked(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[dependency-groups]\ndev = ["pytest>=8.0"]\n'
        )

        lockfile = UvLockfile(
            version=1,
            requires_python=">=3.12",
            packages=[
                UvPackage(
                    name="pytest",
                    version="8.2.0",
                    source=PackageSource(source_type=SourceType.REGISTRY),
                ),
            ],
        )

        # Should not raise
        _validate_lockfile_freshness(pyproject, lockfile)


class TestUtilityFunctions:
    """Tests for utility/helper functions."""

    def test_url_to_filename(self):
        assert _url_to_filename("https://files.python.org/packages/foo-1.0.tar.gz") == "foo-1.0.tar.gz"
        assert _url_to_filename("https://example.com/pkg.whl?query=1") == "pkg.whl"

    def test_parse_git_source_url_with_tag_and_commit(self):
        repo_url, commit = _parse_git_source_url(
            "https://github.com/org/repo.git?tag=v1.0.0#abc123def"
        )
        assert repo_url == "https://github.com/org/repo.git"
        assert commit == "abc123def"

    def test_parse_git_source_url_with_rev(self):
        repo_url, commit = _parse_git_source_url(
            "https://github.com/org/repo.git?rev=main#deadbeef"
        )
        assert repo_url == "https://github.com/org/repo.git"
        assert commit == "deadbeef"

    def test_parse_git_source_url_bare(self):
        repo_url, commit = _parse_git_source_url("https://github.com/org/repo.git")
        assert repo_url == "https://github.com/org/repo.git"
        assert commit == "HEAD"

    def test_parse_hash(self):
        checksum = _parse_hash("sha256:abcdef1234567890")
        assert checksum.algorithm == "sha256"
        assert checksum.hexdigest == "abcdef1234567890"
