# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from unittest import mock

import aiohttp
import yaml

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.package_managers.javascript.pnpm.main import (
    _download_resolved_packages,
    _prepare_lockfile_for_hermetic_build,
    _resolve_pnpm_project,
)
from hermeto.core.package_managers.javascript.pnpm.project import PnpmLock, PnpmPackage
from hermeto.core.package_managers.npm import NPM_REGISTRY_URL
from tests.unit.test_checksum import SHA512_SRI

FAKE_PROXY_URL = "http://proxy.com/npm/registry"


@mock.patch(
    "hermeto.core.package_managers.javascript.pnpm.main._prepare_lockfile_for_hermetic_build"
)
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main._download_resolved_packages")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.parse_packages")
def test_resolve_pnpm_project_skips_local_packages(
    mock_parse_packages: mock.Mock,
    mock_download_resolved_packages: mock.Mock,
    mock_prepare_lockfile_for_hermetic_build: mock.Mock,
    tmp_path: Path,
) -> None:
    remote = PnpmPackage("a@1.0.0", "", "a", "1.0.0", f"{NPM_REGISTRY_URL}/a/-/a-1.0.0.tgz")
    local = PnpmPackage("b@1.0.0", "", "b", "1.0.0", "file:packages/b.tgz")
    mock_parse_packages.return_value = [remote, local]

    _resolve_pnpm_project(tmp_path, mock.Mock())
    mock_download_resolved_packages.assert_called_once_with([remote], tmp_path)
    mock_prepare_lockfile_for_hermetic_build.assert_called_once_with(mock.ANY, [remote])


def _mock_pnpm_config(url: str | None, login: str | None, password: str | None) -> mock.Mock:
    mock_config = mock.Mock()
    mock_config.pnpm.proxy_url = url
    mock_config.pnpm.proxy_login = login
    mock_config.pnpm.proxy_password = password
    return mock_config


@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.get_config")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.async_download_with_auth")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.must_match_any_checksum")
def test_download_resolved_packages_with_proxy_credentials(
    mock_must_match_any_checksum: mock.Mock,
    mock_async_download_with_auth: mock.Mock,
    mock_get_config: mock.Mock,
    tmp_path: Path,
) -> None:
    mock_get_config.return_value = _mock_pnpm_config(FAKE_PROXY_URL, "user", "password")

    pkg = PnpmPackage(
        "pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz", SHA512_SRI
    )
    _download_resolved_packages([pkg], tmp_path)

    mock_async_download_with_auth.assert_called_once_with(
        files_without_auth={},
        files_with_auth={f"{FAKE_PROXY_URL}/pkg/-/pkg-1.0.0.tgz": tmp_path / "pkg-1.0.0.tgz"},
        auth=aiohttp.BasicAuth("user", "password"),
    )
    mock_must_match_any_checksum.assert_called_once_with(
        file_path=tmp_path / "pkg-1.0.0.tgz",
        expected_checksums=[ChecksumInfo.from_sri(SHA512_SRI)],
    )


@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.get_config")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.async_download_with_auth")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.must_match_any_checksum")
def test_download_resolved_packages_without_proxy_credentials(
    mock_must_match_any_checksum: mock.Mock,
    mock_async_download_with_auth: mock.Mock,
    mock_get_config: mock.Mock,
    tmp_path: Path,
) -> None:
    mock_get_config.return_value = _mock_pnpm_config(FAKE_PROXY_URL, None, None)

    pkg = PnpmPackage(
        "pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz", SHA512_SRI
    )
    _download_resolved_packages([pkg], tmp_path)

    mock_async_download_with_auth.assert_called_once_with(
        files_without_auth={f"{FAKE_PROXY_URL}/pkg/-/pkg-1.0.0.tgz": tmp_path / "pkg-1.0.0.tgz"},
        files_with_auth={},
        auth=None,
    )
    mock_must_match_any_checksum.assert_called_once_with(
        file_path=tmp_path / "pkg-1.0.0.tgz",
        expected_checksums=[ChecksumInfo.from_sri(SHA512_SRI)],
    )


@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.get_config")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.async_download_with_auth")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.must_match_any_checksum")
def test_download_resolved_packages_without_proxy(
    mock_must_match_any_checksum: mock.Mock,
    mock_async_download_with_auth: mock.Mock,
    mock_get_config: mock.Mock,
    tmp_path: Path,
) -> None:
    mock_get_config.return_value = _mock_pnpm_config(None, None, None)

    pkg = PnpmPackage(
        "pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz", SHA512_SRI
    )
    _download_resolved_packages([pkg], tmp_path)

    mock_async_download_with_auth.assert_called_once_with(
        files_without_auth={f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz": tmp_path / "pkg-1.0.0.tgz"},
        files_with_auth={},
        auth=None,
    )
    mock_must_match_any_checksum.assert_called_once_with(
        file_path=tmp_path / "pkg-1.0.0.tgz",
        expected_checksums=[ChecksumInfo.from_sri(SHA512_SRI)],
    )


def test_prepare_lockfile_for_hermetic_build(tmp_path: Path) -> None:
    data = {
        "lockfileVersion": "9.0",
        "packages": {
            "a@1.0.0": {"resolution": {"integrity": "sha512-abc"}},
            "@scope/b@2.0.0": {"resolution": {"integrity": "sha512-def"}},
        },
    }
    lockfile = PnpmLock(path=tmp_path / "pnpm-lock.yaml", data=data)
    packages = [
        PnpmPackage(
            "a@1.0.0",
            "",
            "a",
            "1.0.0",
            f"{NPM_REGISTRY_URL}/a/-/a-1.0.0.tgz",
            "sha512-abc",
        ),
        PnpmPackage(
            "@scope/b@2.0.0",
            "scope",
            "b",
            "2.0.0",
            f"{NPM_REGISTRY_URL}/@scope/b/-/b-2.0.0.tgz",
            "sha512-def",
        ),
    ]

    project_file = _prepare_lockfile_for_hermetic_build(lockfile, packages)

    assert project_file.abspath == lockfile.path
    assert project_file.template == yaml.safe_dump(
        {
            "lockfileVersion": "9.0",
            "packages": {
                "a@1.0.0": {
                    "resolution": {
                        "integrity": "sha512-abc",
                        "tarball": "file://${output_dir}/deps/pnpm/a-1.0.0.tgz",
                    }
                },
                "@scope/b@2.0.0": {
                    "resolution": {
                        "integrity": "sha512-def",
                        "tarball": "file://${output_dir}/deps/pnpm/scope-b-2.0.0.tgz",
                    }
                },
            },
        },
        sort_keys=False,
    )

    # Verify that the original URLs are preserved.
    assert packages[0].url == f"{NPM_REGISTRY_URL}/a/-/a-1.0.0.tgz"
    assert packages[1].url == f"{NPM_REGISTRY_URL}/@scope/b/-/b-2.0.0.tgz"
