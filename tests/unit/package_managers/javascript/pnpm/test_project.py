# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Any

import pytest

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound, UnsupportedFeature
from hermeto.core.package_managers.javascript.pnpm.project import (
    PnpmLock,
    PnpmPackage,
    parse_packages,
)


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

    @pytest.mark.parametrize(
        "id, expected",
        [
            ("foo@1.0.0", True),
            ("@scope/bar@2.3.4", True),
            ("@jsr/foo@1.0.0", True),
            ("foo@1.0.0-rc.1+build.5", True),
            ("repo@https://codeload.github.com/org/repo/tar.gz/abc123", False),
            ("foo@git+ssh://git@github.com/org/repo.git#abc123", False),
        ],
    )
    def test_pnpm_package_registry_shaped(self, id: str, expected: bool) -> None:
        # registry_shaped reads only the key, so the other fields are irrelevant here.
        package = PnpmPackage(id, "", "x", "1.0.0", "https://example.com/x.tgz")
        assert package.registry_shaped is expected
