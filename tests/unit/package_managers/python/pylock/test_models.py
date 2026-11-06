# SPDX-License-Identifier: GPL-3.0-only
from typing import Any

import pytest
from pydantic import ValidationError

from hermeto.core.errors import PackageRejected
from hermeto.core.package_managers.python.pylock.models import Pylock, PylockPackage
from tests.common_utils import GIT_REF

SHA = "0" * 64


class TestPylockPackage:
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
        with pytest.raises((PackageRejected, ValidationError)):
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
    @pytest.mark.parametrize(
        "version",
        [
            pytest.param("2.0", id="major_too_new"),
            pytest.param("0.9", id="major_too_old"),
            pytest.param("not-a-version", id="invalid"),
        ],
    )
    def test_rejected_lock_versions(self, version: str) -> None:
        with pytest.raises((PackageRejected, ValidationError)):
            Pylock.model_validate({"lock-version": version, "created-by": "x", "packages": []})
