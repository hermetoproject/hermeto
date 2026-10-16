# SPDX-License-Identifier: GPL-3.0-only
import textwrap
from pathlib import Path
from typing import Any

import pytest

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound, UnsupportedFeature
from hermeto.core.package_managers.javascript.pnpm.project import PnpmLock, parse_packages


class TestPnpmLock:
    def test_from_dir_fails_with_missing_pnpm_lock(self, tmp_path: Path) -> None:
        with pytest.raises(LockfileNotFound):
            PnpmLock.from_dir(tmp_path)

    def test_from_file_fails_with_invalid_yaml(self, tmp_path: Path) -> None:
        lockfile = tmp_path / "pnpm-lock.yaml"
        lockfile.write_text(":")
        with pytest.raises(InvalidLockfileFormat):
            PnpmLock.from_file(lockfile)

    @pytest.mark.parametrize(
        "content",
        [
            # pnpm <= 11 writes a single-document lockfile.
            pytest.param(
                """
                lockfileVersion: '9.0'
                packages:
                  foo@1.0.0: {}
                """,
                id="single_document",
            ),
            # Since pnpm 12 the lockfile may be a YAML stream whose leading "env"
            # document must be skipped in favour of the project graph in the last one.
            pytest.param(
                """
                lockfileVersion: '9.0'
                packages:
                  '@pnpm/exe@12.0.0': {}
                ---
                lockfileVersion: '9.0'
                packages:
                  foo@1.0.0: {}
                """,
                id="multi_document",
            ),
        ],
    )
    def test_from_file_reads_project_graph_from_last_document(
        self, tmp_path: Path, content: str
    ) -> None:
        lockfile = tmp_path / "pnpm-lock.yaml"
        lockfile.write_text(textwrap.dedent(content))
        pnpm_lock = PnpmLock.from_file(lockfile)
        assert pnpm_lock.packages == {"foo@1.0.0": {}}

    @pytest.mark.parametrize(
        ("data", "exc"),
        [
            pytest.param(
                {"lockfileVersion": "1000.0"}, UnsupportedFeature, id="unsupported_version_str"
            ),
            pytest.param(
                {"lockfileVersion": 1000.0}, UnsupportedFeature, id="unsupported_version_float"
            ),
            pytest.param({}, InvalidLockfileFormat, id="missing_version"),
        ],
    )
    def test_pnpm_lock_version_validation_fails(
        self, tmp_path: Path, data: dict[str, Any], exc: type[Exception]
    ) -> None:
        with pytest.raises(exc):
            PnpmLock(path=tmp_path, data=data)

    def test_parse_packages_version_field_overrides_version_from_id(self, tmp_path: Path) -> None:
        lockfile = PnpmLock(
            path=tmp_path / "pnpm-lock.yaml",
            data={
                "lockfileVersion": "9.0",
                "packages": {
                    "repo@https://codeload.github.com/org/repo/tar.gz/abc123": {
                        "version": "1.0.0",
                        "resolution": {
                            "tarball": "https://codeload.github.com/org/repo/tar.gz/abc123"
                        },
                    }
                },
            },
        )
        packages = parse_packages(lockfile)
        assert packages[0].version == "1.0.0"
        assert packages[0].url == "https://codeload.github.com/org/repo/tar.gz/abc123"
