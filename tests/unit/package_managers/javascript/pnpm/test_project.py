# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Any

import pytest

from hermeto.core.errors import (
    InvalidLockfileFormat,
    LockfileNotFound,
    PathOutsideRoot,
    UnsupportedFeature,
)
from hermeto.core.package_managers.javascript.pnpm.project import PnpmLock, parse_packages
from hermeto.core.rooted_path import RootedPath


class TestPnpmLock:
    def test_from_dir_fails_with_missing_pnpm_lock(self, rooted_tmp_path: RootedPath) -> None:
        with pytest.raises(LockfileNotFound):
            PnpmLock.from_dir(rooted_tmp_path)

    @pytest.mark.parametrize(
        "target",
        [
            pytest.param("/outside/pnpm-lock.yaml", id="absolute"),
            pytest.param("../pnpm-lock.yaml", id="relative"),
        ],
    )
    def test_from_dir_rejects_symlink_outside_source(
        self, rooted_tmp_path: RootedPath, target: str
    ) -> None:
        (rooted_tmp_path.path / "pnpm-lock.yaml").symlink_to(target)

        with pytest.raises(PathOutsideRoot):
            PnpmLock.from_dir(rooted_tmp_path)

    def test_from_file_fails_with_invalid_yaml(self, tmp_path: Path) -> None:
        lockfile = tmp_path / "pnpm-lock.yaml"
        lockfile.write_text(":")
        with pytest.raises(InvalidLockfileFormat):
            PnpmLock.from_file(lockfile)

    def test_from_file_uses_last_document(self, tmp_path: Path) -> None:
        # pnpm 11+ prepends an "env" document; the project graph is the last document.
        lockfile = tmp_path / "pnpm-lock.yaml"
        lockfile.write_text(
            "lockfileVersion: '9.0'\n"
            "packageManagerDependencies:\n"
            "  pnpm: 12.11.2\n"
            "---\n"
            "lockfileVersion: '9.0'\n"
            "packages:\n"
            "  a@1.0.0:\n"
            "    resolution: {integrity: sha512-abc}\n"
        )
        pnpm_lock = PnpmLock.from_file(lockfile)
        assert "a@1.0.0" in pnpm_lock.packages
        assert "packageManagerDependencies" not in pnpm_lock

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
