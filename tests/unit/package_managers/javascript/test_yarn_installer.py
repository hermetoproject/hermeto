# SPDX-License-Identifier: GPL-3.0-only
import errno
import hashlib
import tarfile
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest
from semver import Version

from hermeto.core.errors import (
    ChecksumVerificationFailed,
    FetchError,
    InvalidChecksum,
    PackageManagerError,
)
from hermeto.core.package_managers.javascript import yarn_installer as installer
from hermeto.core.package_managers.javascript.yarn_installer import (
    _yarn_cache_dir,
    install_yarn,
)


def _make_tarball(entries: dict[str, bytes], directories: tuple[str, ...] = ()) -> BytesIO:
    """Build an in-memory .tar.gz with the given files and directories."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in directories:
            info = tarfile.TarInfo(name=name)
            info.type = tarfile.DIRTYPE
            tf.addfile(info)
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, BytesIO(data))
    buf.seek(0)
    return buf


@pytest.fixture
def yarn_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "yarn"
    monkeypatch.setattr(installer, "_yarn_cache_dir", lambda: cache)
    return cache


class TestStagedInstallation:
    @pytest.mark.parametrize("archive_root", ["package", "yarn-v1.22.22"])
    def test_publishes_classic_release_with_links_and_extra_files(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch, archive_root: str
    ) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name, data in {
                "NOTICE": b"outside the release tree",
                f"{archive_root}/bin/yarn.js": b"entry",
                f"{archive_root}/lib/cli.js": b"library",
            }.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                tf.addfile(member, BytesIO(data))

            symlink = tarfile.TarInfo(f"{archive_root}/bin/yarn")
            symlink.type = tarfile.SYMTYPE
            symlink.linkname = "yarn.js"
            tf.addfile(symlink)

            hardlink = tarfile.TarInfo(f"{archive_root}/lib/cli-copy.js")
            hardlink.type = tarfile.LNKTYPE
            hardlink.linkname = f"{archive_root}/lib/cli.js"
            tf.addfile(hardlink)

        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(buf.getvalue())
        )

        entry = install_yarn(Version.parse("1.22.22"))
        release_dir = entry.parent.parent

        assert entry == yarn_cache / "1.22.22/bin/yarn.js"
        assert (release_dir / "bin/yarn").is_symlink()
        assert (release_dir / "bin/yarn").read_bytes() == b"entry"
        assert (release_dir / "lib/cli-copy.js").read_bytes() == b"library"
        assert not (release_dir / "NOTICE").exists()
        assert list(yarn_cache.iterdir()) == [release_dir]

    @pytest.mark.parametrize(
        "release, archive_root", [("1.22.22", "package"), ("1.22.19", "yarn-v1.22.19")]
    )
    def test_publishes_complete_classic_installation(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch, release: str, archive_root: str
    ) -> None:
        content = _make_tarball(
            {
                f"{archive_root}/bin/yarn.js": b"entry",
                f"{archive_root}/lib/cli.js": b"library",
                f"{archive_root}/lib/nested/module.js": b"module",
            },
            directories=(archive_root, f"{archive_root}/lib/nested", f"{archive_root}/empty"),
        ).getvalue()
        digest = hashlib.sha224(content).hexdigest()
        version = Version.parse(f"{release}+sha224.{digest}")
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(content)
        )

        entry = install_yarn(version)

        assert entry == yarn_cache / str(version) / "bin/yarn.js"
        assert entry.read_bytes() == b"entry"
        assert (entry.parent.parent / "lib/cli.js").read_bytes() == b"library"
        assert (entry.parent.parent / "lib/nested/module.js").read_bytes() == b"module"
        assert (entry.parent.parent / "empty").is_dir()
        assert list(yarn_cache.iterdir()) == [yarn_cache / str(version)]
        assert not list((yarn_cache / str(version)).rglob("*.tgz"))

    def test_publishes_complete_berry_installation(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def download(_url: str, path: Path) -> None:
            path.write_bytes(b"berry-js-content")

        monkeypatch.setattr(installer, "download_binary_file", download)

        result = install_yarn(Version.parse("4.6.0"))

        assert result == yarn_cache / "4.6.0" / "yarn.js"
        assert result.read_bytes() == b"berry-js-content"
        assert list(yarn_cache.iterdir()) == [yarn_cache / "4.6.0"]

    def test_download_failure_cleans_staging_directory(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def download(url: str, path: Path) -> None:
            path.write_bytes(b"incomplete download")
            raise OSError("download interrupted")

        monkeypatch.setattr(installer, "download_binary_file", download)

        with pytest.raises(PackageManagerError):
            install_yarn(Version.parse("1.22.22"))

        assert list(yarn_cache.iterdir()) == []

    def test_caches_classic_release_without_checking_individual_files(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = _make_tarball({"package/README.md": b"no entry point"})
        monkeypatch.setattr(
            installer, "download_binary_file", lambda _url, path: path.write_bytes(buf.getvalue())
        )

        entry = install_yarn(Version.parse("1.22.22"))

        release_dir = yarn_cache / "1.22.22"
        assert entry == release_dir / "bin/yarn.js"
        assert not entry.exists()
        assert (release_dir / "README.md").read_bytes() == b"no entry point"
        assert list(yarn_cache.iterdir()) == [release_dir]

    def test_failed_extraction_is_not_cached_and_can_retry(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archives = iter(
            [
                _make_tarball(
                    {
                        "package/bin/yarn.js": b"incomplete",
                        "package/lib/cli.js": b"library",
                        "package/../../escaped.js": b"unsafe",
                    }
                ),
                _make_tarball(
                    {"package/bin/yarn.js": b"complete", "package/lib/cli.js": b"library"}
                ),
            ]
        )
        download = mock.Mock(
            side_effect=lambda url, path: path.write_bytes(next(archives).getvalue())
        )
        monkeypatch.setattr(installer, "download_binary_file", download)
        version = Version.parse("1.22.22")

        with pytest.raises(PackageManagerError) as exc_info:
            install_yarn(version)

        assert isinstance(exc_info.value.__cause__, tarfile.FilterError)
        assert list(yarn_cache.iterdir()) == []

        result = install_yarn(version)

        assert download.call_count == 2
        assert result.read_bytes() == b"complete"
        assert (result.parent.parent / "lib/cli.js").read_bytes() == b"library"
        assert list(yarn_cache.iterdir()) == [yarn_cache / str(version)]


class TestInstallYarn:
    @pytest.mark.parametrize("error", [PermissionError(), FileExistsError()])
    def test_cache_directory_creation_failure(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
    ) -> None:
        download = mock.Mock()
        monkeypatch.setattr(installer, "download_binary_file", download)

        with mock.patch.object(Path, "mkdir", side_effect=error):
            with pytest.raises(PackageManagerError) as exc_info:
                install_yarn(Version.parse("4.6.0"))

        assert exc_info.value.__cause__ is error
        download.assert_not_called()
        assert not yarn_cache.exists()

    def test_returns_cached_berry(self, yarn_cache: Path) -> None:
        version = Version.parse("4.6.0")
        dest = yarn_cache / str(version)
        dest.mkdir(parents=True)
        (dest / "yarn.js").write_text("cached")

        result = install_yarn(version)

        assert result == dest / "yarn.js"

    def test_returns_cached_classic(self, yarn_cache: Path) -> None:
        version = Version.parse("1.22.22")
        dest = yarn_cache / str(version) / "bin"
        dest.mkdir(parents=True)
        (dest / "yarn.js").write_text("cached-classic")
        (dest.parent / "lib").mkdir()
        (dest.parent / "lib/cli.js").write_text("cached-library")

        result = install_yarn(version)

        assert result == dest / "yarn.js"

    def test_raises_on_download_failure(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def download(_url: str, path: Path) -> None:
            raise OSError("network error")

        monkeypatch.setattr(installer, "download_binary_file", download)

        with pytest.raises(PackageManagerError):
            install_yarn(Version.parse("4.6.0"))


class TestYarnCacheDir:
    @mock.patch("hermeto.core.package_managers.javascript.yarn_installer.get_cache_dir")
    def test_returns_yarn_subdir(self, mock_cache: mock.Mock, tmp_path: Path) -> None:
        mock_cache.return_value = tmp_path
        assert _yarn_cache_dir() == tmp_path / "yarn"


@pytest.fixture
def yarn_artifact(request: pytest.FixtureRequest) -> tuple[Version, bytes]:
    release = Version.parse(request.param)
    if release.major == 1:
        content = _make_tarball(
            {"package/bin/yarn.js": b"entry", "package/lib/cli.js": b"library"}
        ).getvalue()
    else:
        content = b"entry"
    return release, content


class TestSharedInstallationLifecycle:
    @pytest.mark.parametrize("operation", ["TemporaryDirectory", "_prepare_artifact_path"])
    def test_failed_staging_setup_is_not_cached(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch, operation: str
    ) -> None:
        error = PermissionError(errno.EACCES, "Permission denied")
        download = mock.Mock()
        monkeypatch.setattr(installer, "download_binary_file", download)

        with mock.patch.object(installer, operation, side_effect=error):
            with pytest.raises(PackageManagerError) as exc_info:
                install_yarn(Version.parse("4.6.0"))

        assert exc_info.value.__cause__ is error
        download.assert_not_called()
        assert list(yarn_cache.iterdir()) == []

    def test_failed_extraction_write_cleans_staging_directory(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = _make_tarball(
            {"package/bin/yarn.js": b"entry", "package/lib/cli.js": b"library"}
        ).getvalue()
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(content)
        )
        error = OSError(errno.ENOSPC, "No space left on device")

        with mock.patch.object(tarfile.TarFile, "extractall", side_effect=error):
            with pytest.raises(PackageManagerError) as exc_info:
                install_yarn(Version.parse("1.22.22"))

        assert exc_info.value.__cause__ is error
        assert list(yarn_cache.iterdir()) == []

    @pytest.mark.parametrize("yarn_artifact", ["1.22.22", "4.6.0"], indirect=True)
    def test_relative_cache_home_returns_absolute_entry_point(
        self,
        tmp_path: Path,
        yarn_artifact: tuple[Version, bytes],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        version, content = yarn_artifact
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("XDG_CACHE_HOME", "relative-cache")
        download = mock.Mock(side_effect=lambda url, path: path.write_bytes(content))
        monkeypatch.setattr(installer, "download_binary_file", download)

        entry = install_yarn(version)

        assert entry.is_absolute()
        assert entry.is_relative_to(tmp_path / "relative-cache")
        assert install_yarn(version) == entry
        download.assert_called_once()

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)

        assert entry.read_bytes() == b"entry"

    @pytest.mark.parametrize("yarn_artifact", ["1.22.22", "4.6.0", "4.6.0-rc.1"], indirect=True)
    def test_failed_download_is_not_cached_and_can_retry(
        self,
        yarn_cache: Path,
        yarn_artifact: tuple[Version, bytes],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        version, content = yarn_artifact
        error = FetchError("Download interrupted")

        def interrupted_download(url: str, path: Path) -> None:
            path.write_bytes(b"partial download")
            raise error

        download = mock.Mock(side_effect=interrupted_download)
        monkeypatch.setattr(installer, "download_binary_file", download)

        with pytest.raises(FetchError) as exc_info:
            install_yarn(version)

        assert exc_info.value is error
        assert list(yarn_cache.iterdir()) == []

        download.side_effect = lambda url, path: path.write_bytes(content)
        entry = install_yarn(version)

        assert entry.read_bytes() == b"entry"
        assert download.call_count == 2
        assert list(yarn_cache.iterdir()) == [yarn_cache / str(version)]

    def test_invalid_classic_tarball_is_not_cached(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(b"not a tarball")
        )

        with pytest.raises(PackageManagerError) as exc_info:
            install_yarn(Version.parse("1.22.22"))

        assert isinstance(exc_info.value.__cause__, tarfile.ReadError)
        assert list(yarn_cache.iterdir()) == []

    @pytest.mark.parametrize("yarn_artifact", ["1.22.22", "4.6.0", "4.6.0-rc.1"], indirect=True)
    def test_uses_completed_concurrent_installation(
        self,
        yarn_cache: Path,
        yarn_artifact: tuple[Version, bytes],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        version, content = yarn_artifact
        dest = yarn_cache / str(version)
        entry = dest / ("bin/yarn.js" if version.major == 1 else "yarn.js")

        def download(url: str, path: Path) -> None:
            path.write_bytes(content)
            # Simulate another process completing installation while this one downloads.
            entry.parent.mkdir(parents=True)
            entry.write_bytes(b"already installed")
            if version.major == 1:
                (dest / "lib").mkdir()
                (dest / "lib/cli.js").write_bytes(b"existing library")

        monkeypatch.setattr(installer, "download_binary_file", download)

        assert install_yarn(version) == entry
        assert entry.read_bytes() == b"already installed"
        assert list(yarn_cache.iterdir()) == [dest]
        if version.major == 1:
            assert (dest / "lib/cli.js").read_bytes() == b"existing library"

    @pytest.mark.parametrize("version", ["1.22.22", "4.6.0", "4.6.0-rc.1"])
    def test_reuses_existing_cache_without_checking_individual_files(
        self,
        yarn_cache: Path,
        version: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        release = Version.parse(version)
        dest = yarn_cache / version
        dest.mkdir(parents=True)
        sentinel = dest / "existing-file"
        sentinel.write_bytes(b"unchanged")
        download = mock.Mock()
        monkeypatch.setattr(installer, "download_binary_file", download)

        entry = install_yarn(release)

        assert entry == dest / ("bin/yarn.js" if release.major == 1 else "yarn.js")
        download.assert_not_called()
        assert list(yarn_cache.iterdir()) == [dest]
        assert list(dest.iterdir()) == [sentinel]
        assert sentinel.read_bytes() == b"unchanged"

    @pytest.mark.parametrize("yarn_artifact", ["1.22.22", "4.6.0", "4.6.0-rc.1"], indirect=True)
    def test_failed_publish_cleans_staging_directory(
        self,
        yarn_cache: Path,
        yarn_artifact: tuple[Version, bytes],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        version, content = yarn_artifact
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(content)
        )
        error = PermissionError(errno.EACCES, "Permission denied")

        with mock.patch.object(Path, "rename", side_effect=error):
            with pytest.raises(PackageManagerError) as exc_info:
                install_yarn(version)

        assert exc_info.value.__cause__ is error
        assert list(yarn_cache.iterdir()) == []


class TestInstallYarnWithChecksum:
    @pytest.mark.parametrize("algorithm", ["sha1", "sha224", "sha512"])
    @pytest.mark.parametrize("yarn_artifact", ["1.22.22", "4.6.0", "4.6.0-rc.1"], indirect=True)
    def test_downloads_release_url_and_verifies_artifact(
        self,
        yarn_cache: Path,
        yarn_artifact: tuple[Version, bytes],
        monkeypatch: pytest.MonkeyPatch,
        algorithm: str,
    ) -> None:
        release, content = yarn_artifact
        digest = hashlib.new(algorithm, content).hexdigest()
        version = release.replace(build=f"{algorithm}.{digest}")
        download = mock.Mock(side_effect=lambda url, path: path.write_bytes(content))
        monkeypatch.setattr(installer, "download_binary_file", download)

        result = install_yarn(version)

        url_template = installer.CLASSIC_TARBALL_URL if release.major == 1 else installer.BERRY_URL
        download.assert_called_once_with(url_template.format(version=release), mock.ANY)
        assert result.read_bytes() == b"entry"
        assert result.is_relative_to(yarn_cache / str(version))
        assert list(yarn_cache.iterdir()) == [yarn_cache / str(version)]

    @pytest.mark.parametrize("yarn_artifact", ["1.22.22", "4.6.0", "4.6.0-rc.1"], indirect=True)
    def test_checksum_mismatch_does_not_extract_or_cache(
        self,
        yarn_cache: Path,
        yarn_artifact: tuple[Version, bytes],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        release, content = yarn_artifact
        digest = hashlib.sha224(b"different artifact").hexdigest()
        version = release.replace(build=f"sha224.{digest}")
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(content)
        )
        extract = mock.Mock()
        monkeypatch.setattr(tarfile.TarFile, "extractall", extract)

        with pytest.raises(ChecksumVerificationFailed):
            install_yarn(version)

        extract.assert_not_called()
        assert list(yarn_cache.iterdir()) == []

    @pytest.mark.parametrize("yarn_artifact", ["1.22.22", "4.6.0", "4.6.0-rc.1"], indirect=True)
    def test_cache_reuse_requires_the_same_checksum(
        self,
        yarn_cache: Path,
        yarn_artifact: tuple[Version, bytes],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        release, content = yarn_artifact
        digest = hashlib.sha224(content).hexdigest()
        version = release.replace(build=f"sha224.{digest}")
        download = mock.Mock(side_effect=lambda url, path: path.write_bytes(content))
        monkeypatch.setattr(installer, "download_binary_file", download)

        unverified_entry = install_yarn(release)
        verified_entry = install_yarn(version)

        assert unverified_entry != verified_entry
        assert download.call_count == 2
        assert install_yarn(version) == verified_entry
        assert download.call_count == 2

        other_digest = hashlib.sha224(b"different artifact").hexdigest()
        with pytest.raises(ChecksumVerificationFailed):
            install_yarn(release.replace(build=f"sha224.{other_digest}"))

        assert download.call_count == 3
        assert unverified_entry.read_bytes() == verified_entry.read_bytes() == b"entry"
        assert set(yarn_cache.iterdir()) == {yarn_cache / str(release), yarn_cache / str(version)}

    @pytest.mark.parametrize(
        "build",
        [
            "sha224",
            "sha224.abc",
            "sha224." + "g" * 56,
            "sha224." + "a" * 58,
            "unknown." + "a" * 56,
            "build.1",
        ],
    )
    def test_rejects_invalid_checksum_before_downloading(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch, build: str
    ) -> None:
        download = mock.Mock()
        monkeypatch.setattr(installer, "download_binary_file", download)

        with pytest.raises(InvalidChecksum):
            install_yarn(Version.parse("4.6.0").replace(build=build))

        download.assert_not_called()
        assert not yarn_cache.exists()
