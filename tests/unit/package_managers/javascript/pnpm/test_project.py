# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from hermeto.core.errors import LockfileNotFound, UnsupportedFeature
from hermeto.core.package_managers.javascript.pnpm.project import (
    PnpmLock,
    ensure_lockfile_version_is_supported,
    parse_packages,
)


class TestPnpmLock:
    def test_from_dir_with_missing_pnpm_lock(self, tmp_path: Path) -> None:
        with pytest.raises(LockfileNotFound):
            PnpmLock.from_dir(tmp_path)

    def test_ensure_lockfile_version_is_supported(self, tmp_path: Path) -> None:
        lockfile = PnpmLock(path=tmp_path / "pnpm-lock.yaml", data={"lockfileVersion": "9.0"})
        ensure_lockfile_version_is_supported(lockfile)

    def test_ensure_lockfile_version_is_unsupported_fails(self, tmp_path: Path) -> None:
        lockfile = PnpmLock(path=tmp_path / "pnpm-lock.yaml", data={"lockfileVersion": "10.0"})
        with pytest.raises(UnsupportedFeature):
            ensure_lockfile_version_is_supported(lockfile)

    def test_parse_packages_with_invalid_id_fails(self, tmp_path: Path) -> None:
        lockfile = PnpmLock(
            path=tmp_path / "pnpm-lock.yaml", data={"packages": {"yolo": {"resolution": {}}}}
        )
        with pytest.raises(ValueError, match="Invalid package id"):
            parse_packages(lockfile)

    def test_parse_packages_version_field_overrides_version_from_id(self, tmp_path: Path) -> None:
        lockfile = PnpmLock(
            path=tmp_path / "pnpm-lock.yaml",
            data={
                "packages": {
                    "repo@https://codeload.github.com/org/repo/tar.gz/abc123": {
                        "version": "1.0.0",
                        "resolution": {
                            "tarball": "https://codeload.github.com/org/repo/tar.gz/abc123"
                        },
                    }
                }
            },
        )
        packages = parse_packages(lockfile)
        assert packages[0].version == "1.0.0"
        assert packages[0].url == "https://codeload.github.com/org/repo/tar.gz/abc123"
