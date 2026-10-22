# SPDX-License-Identifier: GPL-3.0-only
import logging
from typing import Any

import pytest
from pydantic import ValidationError

from hermeto.core.errors import InvalidLockfileFormat, PackageRejected
from hermeto.core.package_managers.python.pylock.models import Pylock, PylockPackage
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF

SHA = "0" * 64


def _index_package(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "click",
        "version": "8.1.7",
        "sdist": {"url": "https://example.org/click-8.1.7.tar.gz", "hashes": {"sha256": SHA}},
        "wheels": [
            {"url": "https://example.org/click-8.1.7-py3-none-any.whl", "hashes": {"sha256": SHA}}
        ],
    }
    data.update(overrides)
    return data


def _vcs_package(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "flask",
        "vcs": {"type": "git", "url": "git://github.com/pallets/flask.git", "commit-id": GIT_REF},
    }
    data.update(overrides)
    return data


def _archive_package(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "foo",
        "archive": {"url": "https://example.org/foo-1.0.tar.gz", "hashes": {"sha256": SHA}},
    }
    data.update(overrides)
    return data


class TestPylockPackage:
    def test_index_package(self) -> None:
        pkg = PylockPackage.model_validate(_index_package(index="https://pypi.org/simple/"))
        assert pkg.kind == "index"
        assert pkg.version == "8.1.7"
        assert pkg.index == "https://pypi.org/simple/"
        assert pkg.sdist is not None and pkg.sdist.url.endswith(".tar.gz")
        assert pkg.wheels is not None and pkg.wheels[0].hashes == {"sha256": SHA}

    def test_index_package_wheels_only(self) -> None:
        pkg = PylockPackage.model_validate(
            {
                "name": "click",
                "version": "8.1.7",
                "wheels": [
                    {"url": "https://example.org/click.whl", "hashes": {"sha256": SHA}},
                ],
            }
        )
        assert pkg.kind == "index"
        assert pkg.sdist is None

    def test_vcs_package(self) -> None:
        pkg = PylockPackage.model_validate(_vcs_package())
        assert pkg.kind == "vcs"
        assert pkg.vcs is not None
        assert pkg.vcs.type == "git"
        assert pkg.vcs.commit_id == GIT_REF

    def test_archive_package(self) -> None:
        pkg = PylockPackage.model_validate(_archive_package())
        assert pkg.kind == "archive"
        assert pkg.archive is not None
        assert pkg.archive.hashes == {"sha256": SHA}

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param(
                {"name": "x", "archive": {"path": "/tmp/x.tar.gz", "hashes": {"sha256": SHA}}},
                id="archive_local_path",
            ),
            pytest.param(
                {"name": "x", "sdist": {"path": "/tmp/x.tar.gz", "hashes": {"sha256": SHA}}},
                id="sdist_local_path",
            ),
            pytest.param(
                {
                    "name": "x",
                    "version": "1.0",
                    "wheels": [{"path": "/tmp/x.whl", "hashes": {"sha256": SHA}}],
                },
                id="wheel_local_path",
            ),
            pytest.param(
                {"name": "x", "vcs": {"type": "git", "path": "/tmp/x", "commit-id": GIT_REF}},
                id="vcs_local_path",
            ),
            pytest.param(
                {
                    "name": "x",
                    "archive": {"url": "file:///tmp/x.tar.gz", "hashes": {"sha256": SHA}},
                },
                id="archive_file_url",
            ),
            pytest.param(
                {"name": "x", "vcs": {"type": "git", "url": "file:///tmp/x", "commit-id": GIT_REF}},
                id="vcs_file_url",
            ),
            pytest.param(
                {"name": "x", "archive": {"url": "https://example.org/x.tar.gz", "hashes": {}}},
                id="archive_empty_hashes",
            ),
            pytest.param(
                {"name": "x", "version": "1.0", "sdist": {"url": "https://example.org/x.tar.gz"}},
                id="sdist_missing_hashes",
            ),
            pytest.param(
                {
                    "name": "x",
                    "version": "1.0",
                    "wheels": [{"url": "https://example.org/x.whl"}],
                },
                id="wheel_missing_hashes",
            ),
            pytest.param(
                {"name": "x", "directory": {"path": "./x"}},
                id="directory",
            ),
            pytest.param(
                {
                    "name": "x",
                    "archive": {"url": "https://example.org/x.tar.gz", "hashes": {"sha256": SHA}},
                    "vcs": {"type": "git", "url": "https://g/x.git", "commit-id": GIT_REF},
                },
                id="conflicting_sources",
            ),
            pytest.param(
                {
                    "name": "x",
                    "sdist": {"url": "https://example.org/x.tar.gz", "hashes": {"sha256": SHA}},
                },
                id="index_without_version",
            ),
            pytest.param(
                {"name": "x", "vcs": {"type": "hg", "url": "https://h/x", "commit-id": GIT_REF}},
                id="non_git_vcs",
            ),
            pytest.param(
                {
                    "name": "x",
                    "vcs": {"type": "git", "url": "ext::sh -c malicious", "commit-id": GIT_REF},
                },
                id="git_command_transport",
            ),
            pytest.param(
                {
                    "name": "x",
                    "vcs": {"type": "git", "url": "https://g/x.git", "commit-id": "abcdef"},
                },
                id="git_commit_id_too_short",
            ),
        ],
    )
    def test_rejected_packages(self, data: dict[str, Any]) -> None:
        with pytest.raises(PackageRejected):
            PylockPackage.model_validate(data)

    def test_source_less_package_is_rejected(self) -> None:
        """A package with no fetchable source is rejected."""
        with pytest.raises(PackageRejected):
            PylockPackage.model_validate({"name": "smmap", "version": "5.0.2"})

    def test_root_directory_is_accepted_as_skippable(self) -> None:
        """The project's own entry (directory path '.') is accepted; the backend skips it."""
        pkg = PylockPackage.model_validate({"name": "myproj", "directory": {"path": "."}})
        assert pkg.kind == "directory"

    def test_root_directory_with_conflicting_source_is_rejected(self) -> None:
        """A '.' directory alongside another source is malformed."""
        with pytest.raises(PackageRejected):
            PylockPackage.model_validate(
                {
                    "name": "myproj",
                    "directory": {"path": "."},
                    "archive": {"url": "https://example.org/x.tar.gz", "hashes": {"sha256": SHA}},
                }
            )


class TestPylock:
    def test_lock_version_and_metadata(self) -> None:
        lock = Pylock.model_validate(
            {"lock-version": "1.0", "created-by": "uv", "packages": [_index_package()]}
        )
        assert lock.lock_version == "1.0"
        assert lock.created_by == "uv"
        assert lock.packages[0].kind == "index"

    @pytest.mark.parametrize(
        ("version", "match"),
        [
            pytest.param("2.0", "Unsupported pylock.toml lock-version", id="major_too_new"),
            pytest.param("0.9", "Unsupported pylock.toml lock-version", id="major_too_old"),
            pytest.param("not-a-version", "invalid lock-version", id="invalid"),
        ],
    )
    def test_rejected_lock_versions(self, version: str, match: str) -> None:
        with pytest.raises(PackageRejected, match=match):
            Pylock.model_validate({"lock-version": version, "created-by": "x", "packages": []})

    def test_newer_minor_version_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            lock = Pylock.model_validate({"lock-version": "1.1", "created-by": "x", "packages": []})
        assert lock.lock_version == "1.1"
        assert "newer than 1.0" in caplog.text

    def test_missing_lock_version_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Pylock.model_validate({"created-by": "x", "packages": []})

    def test_created_by_is_optional(self) -> None:
        """created-by is not required; some generators omit it."""
        lock = Pylock.model_validate({"lock-version": "1.0", "packages": []})
        assert lock.created_by is None

    def test_from_file(self, rooted_tmp_path: RootedPath) -> None:
        lockfile = rooted_tmp_path.join_within_root("pylock.toml")
        lockfile.path.write_text(
            "\n".join(
                [
                    'lock-version = "1.0"',
                    'created-by = "uv"',
                    "[[packages]]",
                    'name = "click"',
                    'version = "8.1.7"',
                    "[packages.sdist]",
                    'url = "https://example.org/click-8.1.7.tar.gz"',
                    "[packages.sdist.hashes]",
                    f'sha256 = "{SHA}"',
                    "",
                ]
            )
        )
        lock = Pylock.from_file(lockfile)
        assert lock.packages[0].name == "click"
        assert lock.packages[0].kind == "index"

    def test_from_file_invalid_toml(self, rooted_tmp_path: RootedPath) -> None:
        lockfile = rooted_tmp_path.join_within_root("pylock.toml")
        lockfile.path.write_text("this = = not valid toml")
        with pytest.raises(InvalidLockfileFormat):
            Pylock.from_file(lockfile)

    def test_from_file_schema_error(self, rooted_tmp_path: RootedPath) -> None:
        lockfile = rooted_tmp_path.join_within_root("pylock.toml")
        lockfile.path.write_text('created-by = "uv"\n')
        with pytest.raises(InvalidLockfileFormat):
            Pylock.from_file(lockfile)
