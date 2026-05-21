# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from hermeto.core.errors import InvalidLockfileFormat, PackageRejected
from hermeto.core.package_managers.pip.pylock import (
    PyLockArchivePackage,
    PyLockArtifact,
    PyLockfileV1,
    PyLockIndexPackage,
    PyLockPackage,
    PyLockVCSPackage,
)
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF

LOCKFILE_PATH = Path("pylock.toml")


def write_pylock_toml(
    root: RootedPath,
    toml_content: str,
    filename: str = LOCKFILE_PATH.name,
) -> RootedPath:
    """Write a pylock.toml file and return its RootedPath."""
    lockfile = root.join_within_root(filename)
    lockfile.path.write_text(dedent(toml_content))
    return lockfile


class TestPyLockArtifact:
    """Tests for PyLockArtifact.from_source shared field parsing."""

    def test_parses_url_and_hashes(self) -> None:
        source = {"url": "https://x.org/foo.tar.gz", "hashes": {"sha256": "aaa", "md5": "bbb"}}
        artifact = PyLockArtifact.from_source(source, "foo", LOCKFILE_PATH)

        assert artifact.url == "https://x.org/foo.tar.gz"
        assert "sha256:aaa" in artifact.hashes
        assert "md5:bbb" in artifact.hashes
        assert artifact.subdirectory is None

    def test_parses_subdirectory(self) -> None:
        source = {"url": "https://x.org/foo.tar.gz", "subdirectory": "subdir"}
        artifact = PyLockArtifact.from_source(source, "foo", LOCKFILE_PATH)

        assert artifact.subdirectory == "subdir"

    def test_local_path_rejected(self) -> None:
        source = {"path": "/local/foo"}
        with pytest.raises(PackageRejected):
            PyLockArtifact.from_source(source, "foo", LOCKFILE_PATH)


class TestPyLockPackageFactory:
    """Tests for PyLockPackage.from_package_data dispatch and common fields."""

    @pytest.mark.parametrize(
        "extra_data, expected_type",
        [
            pytest.param(
                {"vcs": {"type": "git", "url": "https://github.com/foo/bar", "commit-id": GIT_REF}},
                PyLockVCSPackage,
                id="vcs",
            ),
            pytest.param(
                {"archive": {"url": "https://x.org/foo.tar.gz"}},
                PyLockArchivePackage,
                id="archive",
            ),
            pytest.param(
                {"version": "1.0"},
                PyLockIndexPackage,
                id="version_only",
            ),
            pytest.param(
                {
                    "version": "1.0",
                    "wheels": [
                        {"name": "foo-1.0-py3-none-any.whl", "url": "https://x.org/foo.whl"}
                    ],
                },
                PyLockIndexPackage,
                id="with_wheels",
            ),
        ],
    )
    def test_dispatch(self, extra_data: dict[str, Any], expected_type: type) -> None:
        data = {"name": "foo", **extra_data}
        pkg = PyLockPackage.from_package_data(data, LOCKFILE_PATH)

        assert isinstance(pkg, expected_type)

    def test_common_fields_set(self) -> None:
        data = {
            "name": "Foo-Bar",
            "version": "1.2.3",
            "dependencies": ["bar", "baz"],
        }
        pkg = PyLockPackage.from_package_data(data, LOCKFILE_PATH)

        assert pkg.name == "Foo-Bar"
        assert pkg.version == "1.2.3"
        assert pkg.dependencies == ["bar", "baz"]
        assert pkg.package == "foo-bar"
        assert pkg.raw_package == "Foo-Bar"

    def test_common_fields_missing_optionals(self) -> None:
        data = {"name": "foo", "version": "1.0"}
        pkg = PyLockPackage.from_package_data(data, LOCKFILE_PATH)

        assert pkg.version == "1.0"
        assert pkg.dependencies is None


class TestPyLockVCSPackage:
    """Tests for PyLockVCSPackage.from_data."""

    def test_vcs_with_url(self) -> None:
        data = {
            "name": "foo",
            "vcs": {
                "type": "git",
                "url": "https://github.com/foo/bar",
                "commit-id": GIT_REF,
            },
        }
        pkg = PyLockPackage.from_package_data(data, LOCKFILE_PATH)

        assert isinstance(pkg, PyLockVCSPackage)
        assert pkg.kind == "vcs"
        assert pkg.vcs_type == "git"
        assert pkg.commit_id == GIT_REF
        assert pkg.download_line == f"foo @ git+https://github.com/foo/bar@{GIT_REF}"

    def test_vcs_with_path_rejected(self) -> None:
        data = {
            "name": "foo",
            "vcs": {"type": "git", "path": "/local/foo", "commit-id": GIT_REF},
        }
        with pytest.raises(PackageRejected):
            PyLockPackage.from_package_data(data, LOCKFILE_PATH)

    def test_vcs_optional_fields(self) -> None:
        data = {
            "name": "foo",
            "vcs": {
                "type": "git",
                "url": "https://github.com/foo/bar",
                "commit-id": GIT_REF,
                "subdirectory": "subdir",
            },
        }
        pkg = PyLockPackage.from_package_data(data, LOCKFILE_PATH)

        assert isinstance(pkg, PyLockVCSPackage)
        assert pkg.subdirectory == "subdir"


class TestPyLockDirectoryPackageRejected:
    """Directory packages are rejected because they cannot be fetched or verified."""

    def test_directory_package_rejected(self) -> None:
        data = {"name": "foo", "directory": {"path": "./foo"}}
        with pytest.raises(PackageRejected):
            PyLockPackage.from_package_data(data, LOCKFILE_PATH)


class TestPyLockArchivePackage:
    """Tests for PyLockArchivePackage.from_data."""

    def test_archive_with_url(self) -> None:
        data = {
            "name": "foo",
            "archive": {"url": "https://x.org/foo-1.0.tar.gz", "hashes": {"sha256": "aaa"}},
        }
        pkg = PyLockPackage.from_package_data(data, LOCKFILE_PATH)

        assert isinstance(pkg, PyLockArchivePackage)
        assert pkg.url == "https://x.org/foo-1.0.tar.gz"
        assert pkg.kind == "url"
        assert pkg.download_line == "foo @ https://x.org/foo-1.0.tar.gz"
        assert pkg.hashes == ["sha256:aaa"]

    def test_archive_with_path_rejected(self) -> None:
        data = {"name": "foo", "archive": {"path": "/local/foo.tar.gz"}}
        with pytest.raises(PackageRejected):
            PyLockPackage.from_package_data(data, LOCKFILE_PATH)


class TestPyLockIndexPackage:
    """Tests for PyLockIndexPackage — packages resolved via PyPI."""

    @pytest.mark.parametrize(
        "extra_data",
        [
            pytest.param({}, id="no_artifacts"),
            pytest.param(
                {
                    "sdist": {
                        "name": "foo-1.0.tar.gz",
                        "url": "https://x.org/foo-1.0.tar.gz",
                        "hashes": {"sha256": "sdist_hash"},
                    }
                },
                id="sdist_only",
            ),
            pytest.param(
                {
                    "wheels": [
                        {
                            "name": "foo-1.0-py3-none-any.whl",
                            "url": "https://x.org/foo.whl",
                            "hashes": {"sha256": "wheel_hash"},
                        }
                    ]
                },
                id="wheels_only",
            ),
            pytest.param(
                {
                    "sdist": {
                        "name": "foo-1.0.tar.gz",
                        "url": "https://x.org/foo-1.0.tar.gz",
                        "hashes": {"sha256": "sdist_hash"},
                    },
                    "wheels": [
                        {
                            "name": "foo-1.0-py3-none-any.whl",
                            "url": "https://x.org/foo.whl",
                            "hashes": {"sha256": "wheel_hash"},
                        }
                    ],
                },
                id="sdist_and_wheels",
            ),
        ],
    )
    def test_resolves_via_pypi(self, extra_data: dict[str, Any]) -> None:
        """All index packages resolve via PyPI regardless of artifacts present."""
        data = {"name": "foo", "version": "1.0", **extra_data}
        pkg = PyLockPackage.from_package_data(data, LOCKFILE_PATH)

        assert isinstance(pkg, PyLockIndexPackage)
        assert pkg.kind == "pypi"
        assert pkg.download_line == "foo==1.0"
        assert pkg.version_specs == [("==", "1.0")]

    def test_sdist_hashes_collected(self) -> None:
        """Only sdist hashes are used; wheel hashes are ignored."""
        data = {
            "name": "foo",
            "version": "1.0",
            "sdist": {
                "name": "foo-1.0.tar.gz",
                "url": "https://x.org/foo-1.0.tar.gz",
                "hashes": {"sha256": "sdist_hash"},
            },
            "wheels": [
                {
                    "name": "foo-1.0-cp310.whl",
                    "url": "https://x.org/foo-cp310.whl",
                    "hashes": {"sha256": "wheel_hash"},
                },
            ],
        }
        pkg = PyLockPackage.from_package_data(data, LOCKFILE_PATH)

        assert isinstance(pkg, PyLockIndexPackage)
        assert pkg.hashes == ["sha256:sdist_hash"]

    def test_missing_version_rejected(self) -> None:
        data = {"name": "foo"}
        with pytest.raises(PackageRejected):
            PyLockPackage.from_package_data(data, LOCKFILE_PATH)


class TestPyLockfileV1:
    """Tests for PyLockfileV1 file loading and version validation."""

    def test_valid_lockfile(self, rooted_tmp_path: RootedPath) -> None:
        lockfile = write_pylock_toml(
            rooted_tmp_path,
            """\
            lock-version = "1.0"

            [[packages]]
            name = "foo"
            version = "1.0"

            [[packages.wheels]]
            name = "foo-1.0-py3-none-any.whl"
            url = "https://x.org/foo-1.0.whl"

            [packages.wheels.hashes]
            sha256 = "abcdef"
            """,
        )
        parsed = PyLockfileV1(lockfile)

        assert len(parsed.packages) == 1
        assert parsed.packages[0].name == "foo"

    @pytest.mark.parametrize("version", ["1.0", "1.1"])
    def test_compatible_versions_accepted(self, rooted_tmp_path: RootedPath, version: str) -> None:
        lockfile = write_pylock_toml(
            rooted_tmp_path,
            f"""\
            lock-version = "{version}"
            packages = []
            """,
        )
        PyLockfileV1(lockfile)

    @pytest.mark.parametrize("version", ["0.9", "2.0"])
    def test_incompatible_versions_rejected(
        self, rooted_tmp_path: RootedPath, version: str
    ) -> None:
        lockfile = write_pylock_toml(
            rooted_tmp_path,
            f"""\
            lock-version = "{version}"
            """,
        )
        with pytest.raises(InvalidLockfileFormat):
            PyLockfileV1(lockfile)

    def test_invalid_toml_raises(self, rooted_tmp_path: RootedPath) -> None:
        lockfile = write_pylock_toml(rooted_tmp_path, "not a valid toml {{{")

        with pytest.raises(InvalidLockfileFormat):
            PyLockfileV1(lockfile)

    def test_mixed_package_types(self, rooted_tmp_path: RootedPath) -> None:
        lockfile = write_pylock_toml(
            rooted_tmp_path,
            f"""\
            lock-version = "1.0"

            [[packages]]
            name = "foo"
            version = "1.0"

            [[packages.wheels]]
            name = "foo-1.0-py3-none-any.whl"
            url = "https://x.org/foo.whl"

            [packages.wheels.hashes]
            sha256 = "aaa"

            [[packages]]
            name = "bar"

            [packages.vcs]
            type = "git"
            url = "https://github.com/foo/bar"
            commit-id = "{GIT_REF}"

            [[packages]]
            name = "baz"

            [packages.archive]
            url = "https://x.org/baz.tar.gz"
            """,
        )
        parsed = PyLockfileV1(lockfile)

        assert len(parsed.packages) == 3
        types = {type(pkg) for pkg in parsed.packages}
        assert PyLockIndexPackage in types
        assert PyLockVCSPackage in types
        assert PyLockArchivePackage in types
