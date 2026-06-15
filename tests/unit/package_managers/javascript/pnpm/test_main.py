# SPDX-License-Identifier: GPL-3.0-only
import base64
import hashlib
import io
import json
import stat
import tarfile
from pathlib import Path
from unittest import mock

import aiohttp
import yaml

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.package_managers.javascript.npm import NPM_REGISTRY_URL
from hermeto.core.package_managers.javascript.pnpm.main import (
    _add_tarball_to_pnpm_store,
    _download_resolved_packages,
    _prepare_lockfile_for_hermetic_build,
    _resolve_pnpm_project,
    _sri_to_pnpm_store_key,
)
from hermeto.core.package_managers.javascript.pnpm.project import PnpmLock, PnpmPackage
from tests.unit.test_checksum import SHA512_SRI

FAKE_PROXY_URL = "http://proxy.com/npm/registry"


def _make_npm_tarball(path: Path, members: list[tuple[str, bytes, int]]) -> None:
    """Write a minimal npm-style ``.tgz`` (every entry under a top-level ``package/``)."""
    with tarfile.open(path, "w:gz") as tf:
        for name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            tf.addfile(info, io.BytesIO(data))


@mock.patch(
    "hermeto.core.package_managers.javascript.pnpm.main._prepare_lockfile_for_hermetic_build"
)
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main._add_tarball_to_pnpm_store")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main._download_resolved_packages")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.parse_packages")
def test_resolve_pnpm_project_skips_local_packages(
    mock_parse_packages: mock.Mock,
    mock_download_resolved_packages: mock.Mock,
    mock_add_tarball_to_pnpm_store: mock.Mock,
    mock_prepare_lockfile_for_hermetic_build: mock.Mock,
    tmp_path: Path,
) -> None:
    deps_dir = tmp_path / "deps"
    store_dir = tmp_path / "store"
    remote = PnpmPackage("a@1.0.0", "", "a", "1.0.0", f"{NPM_REGISTRY_URL}/a/-/a-1.0.0.tgz")
    local = PnpmPackage("b@1.0.0", "", "b", "1.0.0", "file:packages/b.tgz")
    mock_parse_packages.return_value = [remote, local]
    lockfile = mock.Mock()

    _resolve_pnpm_project(deps_dir, store_dir, lockfile)

    mock_download_resolved_packages.assert_called_once_with([remote], deps_dir)
    # The local "file:" package is never downloaded nor added to the store; the remote
    # one carries no integrity in this fixture, so it is not added to the store either.
    mock_add_tarball_to_pnpm_store.assert_not_called()
    # The remote package is registry-shaped, so it is served from the store rather than
    # rewritten: nothing is left for the file:// rewrite.
    mock_prepare_lockfile_for_hermetic_build.assert_called_once_with(lockfile, [])


@mock.patch(
    "hermeto.core.package_managers.javascript.pnpm.main._prepare_lockfile_for_hermetic_build"
)
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main._add_tarball_to_pnpm_store")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main._download_resolved_packages")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.parse_packages")
def test_resolve_pnpm_project_adds_remote_packages_to_store(
    mock_parse_packages: mock.Mock,
    mock_download_resolved_packages: mock.Mock,
    mock_add_tarball_to_pnpm_store: mock.Mock,
    mock_prepare_lockfile_for_hermetic_build: mock.Mock,
    tmp_path: Path,
) -> None:
    deps_dir = tmp_path / "deps"
    store_dir = tmp_path / "store"
    remote = PnpmPackage(
        "a@1.0.0", "", "a", "1.0.0", f"{NPM_REGISTRY_URL}/a/-/a-1.0.0.tgz", SHA512_SRI
    )
    mock_parse_packages.return_value = [remote]

    _resolve_pnpm_project(deps_dir, store_dir, mock.Mock())

    # A registry package with an integrity is unpacked into the offline store keyed by
    # that integrity, so "pnpm install --offline" can resolve it without a tarball URL.
    mock_add_tarball_to_pnpm_store.assert_called_once_with(
        tarball_path=deps_dir / remote.tarball_filename,
        store_dir=store_dir,
        name="a",
        scope="",
        version="1.0.0",
        pkg_integrity=SHA512_SRI,
    )


@mock.patch(
    "hermeto.core.package_managers.javascript.pnpm.main._prepare_lockfile_for_hermetic_build"
)
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main._add_tarball_to_pnpm_store")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main._download_resolved_packages")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.main.parse_packages")
def test_resolve_pnpm_project_rewrites_non_registry_packages(
    mock_parse_packages: mock.Mock,
    mock_download_resolved_packages: mock.Mock,
    mock_add_tarball_to_pnpm_store: mock.Mock,
    mock_prepare_lockfile_for_hermetic_build: mock.Mock,
    tmp_path: Path,
) -> None:
    deps_dir = tmp_path / "deps"
    store_dir = tmp_path / "store"
    registry = PnpmPackage(
        "a@1.0.0", "", "a", "1.0.0", f"{NPM_REGISTRY_URL}/a/-/a-1.0.0.tgz", SHA512_SRI
    )
    # A git/URL dependency: its lockfile key carries a URL, so pnpm treats it as
    # non-registry-shaped and it has no integrity field.
    git = PnpmPackage(
        "repo@https://codeload.github.com/org/repo/tar.gz/abc123",
        "",
        "repo",
        "1.0.0",
        "https://codeload.github.com/org/repo/tar.gz/abc123",
    )
    mock_parse_packages.return_value = [registry, git]
    lockfile = mock.Mock()

    _resolve_pnpm_project(deps_dir, store_dir, lockfile)

    # Only the registry package goes to the integrity-addressed store; the git/URL package
    # cannot be served from it and must not be added.
    mock_add_tarball_to_pnpm_store.assert_called_once_with(
        tarball_path=deps_dir / registry.tarball_filename,
        store_dir=store_dir,
        name="a",
        scope="",
        version="1.0.0",
        pkg_integrity=SHA512_SRI,
    )
    # The git/URL package keeps a file:// resolution rewrite (pnpm accepts that for
    # non-registry keys); the registry package, served from the store, is not rewritten.
    mock_prepare_lockfile_for_hermetic_build.assert_called_once_with(lockfile, [git])


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
        auth=aiohttp.encode_basic_auth("user", "password"),
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


def test_sri_to_pnpm_store_key() -> None:
    # pnpm derives the index location from the first 32 bytes of the SHA-512 digest:
    # 2 hex chars name the subdir, the next 62 form the filename prefix.
    raw = bytes(range(64))
    sri = "sha512-" + base64.b64encode(raw).decode()

    assert _sri_to_pnpm_store_key(sri) == (
        "00",
        "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
    )


def test_add_tarball_to_pnpm_store(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    tarball = tmp_path / "foo-1.0.0.tgz"
    manifest = b'{"name":"foo","version":"1.0.0","scripts":{"postinstall":"node-gyp rebuild"}}'
    cli = b"#!/usr/bin/env node\nconsole.log('hi');\n"
    index_js = b"module.exports = 1;\n"
    _make_npm_tarball(
        tarball,
        [
            ("package/package.json", manifest, 0o644),
            ("package/bin/cli.js", cli, 0o755),
            ("package/index.js", index_js, 0o644),
        ],
    )
    integrity = "sha512-" + base64.b64encode(bytes(range(64))).decode()

    _add_tarball_to_pnpm_store(
        tarball_path=tarball,
        store_dir=store_dir,
        name="foo",
        scope="",
        version="1.0.0",
        pkg_integrity=integrity,
    )

    files_dir = store_dir / "v10" / "files"

    # An executable file is stored under a "-exec" suffix at 0o750 on disk: pnpm
    # hardlinks the CAS file into node_modules, so the stored mode is what is run.
    cli_hex = hashlib.sha512(cli).hexdigest()
    cli_cas = files_dir / cli_hex[0:2] / (cli_hex[2:] + "-exec")
    assert cli_cas.read_bytes() == cli
    assert stat.S_IMODE(cli_cas.stat().st_mode) == 0o750

    # A plain file uses the bare hash at 0o640.
    index_hex = hashlib.sha512(index_js).hexdigest()
    index_cas = files_dir / index_hex[0:2] / index_hex[2:]
    assert index_cas.read_bytes() == index_js
    assert stat.S_IMODE(index_cas.stat().st_mode) == 0o640

    prefix, key = _sri_to_pnpm_store_key(integrity)
    index = json.loads((store_dir / "v10" / "index" / prefix / f"{key}-foo@1.0.0.json").read_text())

    assert index["name"] == "foo"
    assert index["version"] == "1.0.0"
    # A postinstall script means pnpm must build the package, so the index says so.
    assert index["requiresBuild"] is True
    assert set(index["files"]) == {"package.json", "bin/cli.js", "index.js"}
    assert index["files"]["bin/cli.js"]["mode"] == 0o755
    assert index["files"]["index.js"]["size"] == len(index_js)
    assert (
        index["files"]["bin/cli.js"]["integrity"]
        == "sha512-" + base64.b64encode(bytes.fromhex(cli_hex)).decode()
    )


def test_add_tarball_to_pnpm_store_scoped_package(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    tarball = tmp_path / "scope-b-2.0.0.tgz"
    _make_npm_tarball(
        tarball,
        [("package/package.json", b'{"name":"@scope/b","version":"2.0.0"}', 0o644)],
    )
    integrity = "sha512-" + base64.b64encode(bytes(range(64))).decode()

    _add_tarball_to_pnpm_store(
        tarball_path=tarball,
        store_dir=store_dir,
        name="b",
        scope="scope",
        version="2.0.0",
        pkg_integrity=integrity,
    )

    prefix, key = _sri_to_pnpm_store_key(integrity)
    index = json.loads(
        (store_dir / "v10" / "index" / prefix / f"{key}-@scope+b@2.0.0.json").read_text()
    )
    assert index["name"] == "@scope/b"
    # No install scripts and no node-gyp config: pnpm need not build this package.
    assert index["requiresBuild"] is False


def test_add_tarball_to_pnpm_store_requires_build_from_gyp(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    tarball = tmp_path / "native-1.0.0.tgz"
    _make_npm_tarball(
        tarball,
        [
            ("package/package.json", b'{"name":"native","version":"1.0.0"}', 0o644),
            ("package/binding.gyp", b"{}", 0o644),
        ],
    )
    integrity = "sha512-" + base64.b64encode(bytes(range(64))).decode()

    _add_tarball_to_pnpm_store(
        tarball_path=tarball,
        store_dir=store_dir,
        name="native",
        scope="",
        version="1.0.0",
        pkg_integrity=integrity,
    )

    prefix, key = _sri_to_pnpm_store_key(integrity)
    index = json.loads(
        (store_dir / "v10" / "index" / prefix / f"{key}-native@1.0.0.json").read_text()
    )
    # A node-gyp config means the package builds a native addon at install time.
    assert index["requiresBuild"] is True


def test_add_tarball_to_pnpm_store_non_sha512_integrity(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    tarball = tmp_path / "legacy-1.0.0.tgz"
    _make_npm_tarball(
        tarball,
        [("package/package.json", b'{"name":"legacy","version":"1.0.0"}', 0o644)],
    )

    # A non-sha512 integrity must not crash; pnpm keys the store by the sha512 of the
    # tarball, so the index lands under that digest rather than the integrity hash.
    _add_tarball_to_pnpm_store(
        tarball_path=tarball,
        store_dir=store_dir,
        name="legacy",
        scope="",
        version="1.0.0",
        pkg_integrity="sha1-" + base64.b64encode(bytes(20)).decode(),
    )

    tarball_hex = hashlib.sha512(tarball.read_bytes()).hexdigest()
    index_path = (
        store_dir / "v10" / "index" / tarball_hex[0:2] / f"{tarball_hex[2:64]}-legacy@1.0.0.json"
    )
    assert index_path.is_file()


def test_add_tarball_to_pnpm_store_non_object_manifest(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    tarball = tmp_path / "weird-1.0.0.tgz"
    # A package.json whose JSON root is not an object must not abort the prefetch.
    _make_npm_tarball(tarball, [("package/package.json", b'["not", "an", "object"]', 0o644)])
    integrity = "sha512-" + base64.b64encode(bytes(range(64))).decode()

    _add_tarball_to_pnpm_store(
        tarball_path=tarball,
        store_dir=store_dir,
        name="weird",
        scope="",
        version="1.0.0",
        pkg_integrity=integrity,
    )

    prefix, key = _sri_to_pnpm_store_key(integrity)
    index = json.loads(
        (store_dir / "v10" / "index" / prefix / f"{key}-weird@1.0.0.json").read_text()
    )
    assert index["requiresBuild"] is False


def test_prepare_lockfile_for_hermetic_build(tmp_path: Path) -> None:
    git_id = "repo@https://codeload.github.com/org/repo/tar.gz/abc123"
    data = {
        "lockfileVersion": "9.0",
        "packages": {
            "a@1.0.0": {"resolution": {"integrity": "sha512-abc"}},
            git_id: {
                "resolution": {"tarball": "https://codeload.github.com/org/repo/tar.gz/abc123"}
            },
        },
    }
    lockfile = PnpmLock(path=tmp_path / "pnpm-lock.yaml", data=data)
    git = PnpmPackage(
        git_id, "", "repo", "1.0.0", "https://codeload.github.com/org/repo/tar.gz/abc123"
    )

    project_file = _prepare_lockfile_for_hermetic_build(lockfile, [git])

    assert project_file.abspath == lockfile.path
    # Only the non-registry (git/URL) package is rewritten to its downloaded tarball. The
    # registry package is served from the offline store and keeps its registry-style
    # resolution, which pnpm >=10.34.2 requires (a "tarball: file://" on it is rejected as
    # a resolution-shape mismatch).
    assert project_file.template == yaml.safe_dump(
        {
            "lockfileVersion": "9.0",
            "packages": {
                "a@1.0.0": {"resolution": {"integrity": "sha512-abc"}},
                git_id: {
                    "resolution": {"tarball": "file://${output_dir}/deps/pnpm/repo-1.0.0.tgz"}
                },
            },
        },
        sort_keys=False,
    )
