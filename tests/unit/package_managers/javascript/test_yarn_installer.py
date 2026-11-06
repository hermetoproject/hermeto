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
    PathOutsideRoot,
)
from hermeto.core.package_managers.javascript import yarn_installer as installer
from hermeto.core.package_managers.javascript.yarn_installer import (
    _extract_classic,
    _yarn_cache_dir,
    install_yarn,
)


def _make_tarball(entries: dict[str, bytes]) -> BytesIO:
    """Build an in-memory .tar.gz with the given name -> content map."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, BytesIO(data))
    buf.seek(0)
    return buf


class TestExtractClassic:
    def test_extracts_full_tree(self, tmp_path: Path) -> None:
        buf = _make_tarball(
            {
                "package/bin/yarn.js": b"classic-entry",
                "package/lib/cli.js": b"classic-lib",
            }
        )
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            _extract_classic(tf, tmp_path, Version.parse("1.22.22"))

        assert (tmp_path / "bin" / "yarn.js").read_bytes() == b"classic-entry"
        assert (tmp_path / "lib" / "cli.js").read_bytes() == b"classic-lib"

    @pytest.mark.parametrize(
        "member_name, member_type",
        [
            ("outside/module.js", tarfile.REGTYPE),
            ("README.md", tarfile.REGTYPE),
            ("outside", tarfile.DIRTYPE),
            ("package", tarfile.REGTYPE),
            ("yarn-v1.22.22", tarfile.REGTYPE),
        ],
    )
    def test_rejects_unexpected_archive_layout(
        self, tmp_path: Path, member_name: str, member_type: bytes
    ) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            member = tarfile.TarInfo(member_name)
            member.type = member_type
            tf.addfile(member)
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            with pytest.raises(PackageManagerError):
                _extract_classic(tf, tmp_path, Version.parse("1.22.22"))

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        "path_template",
        [
            "package/../outside/{target_name}",
            "package/{outside}/{target_name}",
            "package/linked/{target_name}",
        ],
        ids=["parent", "absolute", "symlink"],
    )
    @pytest.mark.parametrize(
        "member_type, target_name",
        [(tarfile.REGTYPE, "protected"), (tarfile.DIRTYPE, "new-directory")],
        ids=["file", "directory"],
    )
    def test_rejects_paths_outside_destination(
        self, tmp_path: Path, path_template: str, member_type: bytes, target_name: str
    ) -> None:
        dest = tmp_path / "destination"
        dest.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "protected"
        sentinel.write_bytes(b"unchanged")
        (dest / "linked").symlink_to(outside, target_is_directory=True)

        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            member = tarfile.TarInfo(path_template.format(outside=outside, target_name=target_name))
            member.type = member_type
            tf.addfile(member)
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            with pytest.raises(PathOutsideRoot):
                _extract_classic(tf, dest, Version.parse("1.22.22"))

        assert sentinel.read_bytes() == b"unchanged"
        assert list(outside.iterdir()) == [sentinel]

    @pytest.mark.parametrize(
        "member_type",
        [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE],
    )
    @pytest.mark.parametrize("member_name", ["package", "package/special"])
    def test_rejects_special_entries(
        self, tmp_path: Path, member_type: bytes, member_name: str
    ) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            member = tarfile.TarInfo(member_name)
            member.type = member_type
            member.linkname = "../outside"
            tf.addfile(member)
            tf.addfile(tarfile.TarInfo("package/bin/yarn.js"))
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            with pytest.raises(PackageManagerError):
                _extract_classic(tf, tmp_path, Version.parse("1.22.22"))

        assert list(tmp_path.iterdir()) == []

    def test_extracts_directories(self, tmp_path: Path) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name in ("package/", "package/lib/nested"):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                tf.addfile(member)
            tf.addfile(tarfile.TarInfo("package/lib/nested/module.js"))
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            _extract_classic(tf, tmp_path, Version.parse("1.22.22"))

        assert (tmp_path / "lib/nested/module.js").is_file()

    def test_accepts_versioned_root_directory(self, tmp_path: Path) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            member = tarfile.TarInfo("yarn-v1.22.19")
            member.type = tarfile.DIRTYPE
            tf.addfile(member)
            tf.addfile(tarfile.TarInfo("yarn-v1.22.19/bin/yarn.js"))
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            _extract_classic(tf, tmp_path, Version.parse("1.22.19"))

        assert (tmp_path / "bin/yarn.js").is_file()


@pytest.fixture
def yarn_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "yarn"
    monkeypatch.setattr(installer, "_yarn_cache_dir", lambda: cache)
    return cache


class TestStagedInstallation:
    def test_special_entry_prevents_caching_complete_classic_release(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name in ("package/bin/yarn.js", "package/lib/cli.js"):
                tf.addfile(tarfile.TarInfo(name))
            member = tarfile.TarInfo("package/link")
            member.type = tarfile.SYMTYPE
            member.linkname = "bin/yarn.js"
            tf.addfile(member)
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(buf.getvalue())
        )

        with pytest.raises(PackageManagerError):
            install_yarn(Version.parse("1.22.22"))

        assert list(yarn_cache.iterdir()) == []

    def test_installs_classic_from_versioned_archive_root(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = _make_tarball(
            {
                "yarn-v1.22.19/bin/yarn.js": b"entry",
                "yarn-v1.22.19/lib/cli.js": b"library",
            }
        ).getvalue()
        digest = hashlib.sha224(content).hexdigest()
        version = Version.parse(f"1.22.19+sha224.{digest}")
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(content)
        )

        entry = install_yarn(version)

        assert entry == yarn_cache / str(version) / "bin/yarn.js"
        assert entry.read_bytes() == b"entry"
        assert (entry.parent.parent / "lib/cli.js").read_bytes() == b"library"

    @pytest.mark.parametrize(
        "version, archive_root, unexpected_entry",
        [
            ("1.22.22", "package", "outside/module.js"),
            ("1.22.19", "yarn-v1.22.19", "yarn-v1.22.18/unexpected.js"),
        ],
    )
    def test_unexpected_entry_prevents_caching_complete_classic_release(
        self,
        yarn_cache: Path,
        monkeypatch: pytest.MonkeyPatch,
        version: str,
        archive_root: str,
        unexpected_entry: str,
    ) -> None:
        content = _make_tarball(
            {
                f"{archive_root}/bin/yarn.js": b"entry",
                f"{archive_root}/lib/cli.js": b"library",
                unexpected_entry: b"unexpected content",
            }
        ).getvalue()
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(content)
        )

        with pytest.raises(PackageManagerError):
            install_yarn(Version.parse(version))

        assert list(yarn_cache.iterdir()) == []

    def test_versioned_archive_root_cannot_escape_staging_directory(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = _make_tarball({"yarn-v1.22.19/../escaped.js": b"unsafe"}).getvalue()
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(content)
        )

        with pytest.raises(PathOutsideRoot):
            install_yarn(Version.parse("1.22.19"))

        assert list(yarn_cache.iterdir()) == []

    def test_publishes_complete_classic_installation(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version = "1.22.22"
        buf = _make_tarball({"package/bin/yarn.js": b"entry", "package/lib/cli.js": b"library"})

        def download(_url: str, path: Path) -> None:
            path.write_bytes(buf.getvalue())

        monkeypatch.setattr(installer, "download_binary_file", download)

        result = install_yarn(Version.parse(version))

        assert result == yarn_cache / version / "bin/yarn.js"
        assert result.read_bytes() == b"entry"
        assert list(yarn_cache.iterdir()) == [yarn_cache / version]
        assert not list((yarn_cache / version).rglob("*.tgz"))
        assert (yarn_cache / version / "lib/cli.js").read_bytes() == b"library"

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

    def test_missing_entry_is_not_cached(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = _make_tarball({"package/README.md": b"no entry point"})
        monkeypatch.setattr(
            installer, "download_binary_file", lambda _url, path: path.write_bytes(buf.getvalue())
        )

        with pytest.raises(PackageManagerError):
            install_yarn(Version.parse("1.22.22"))

        assert list(yarn_cache.iterdir()) == []

    @pytest.mark.parametrize(
        "entries",
        [
            pytest.param({"package/bin/yarn.js": b"entry"}, id="missing-library"),
            pytest.param(
                {
                    "package/bin/yarn.js": b"entry",
                    "package/lib/cli.js/child": b"not a library file",
                },
                id="library-is-directory",
            ),
        ],
    )
    def test_classic_library_must_be_a_file_before_caching(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch, entries: dict[str, bytes]
    ) -> None:
        content = _make_tarball(entries).getvalue()
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(content)
        )

        with pytest.raises(PackageManagerError):
            install_yarn(Version.parse("1.22.22"))

        assert list(yarn_cache.iterdir()) == []

    def test_failed_extraction_is_not_cached_and_can_retry(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archives = iter(
            [
                _make_tarball(
                    {"package/bin/yarn.js": b"incomplete", "package/../escaped.js": b"unsafe"}
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

        with pytest.raises(PathOutsideRoot):
            install_yarn(version)

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

    @pytest.mark.parametrize("existing_file", ["bin/yarn.js", "lib/cli.js"])
    def test_rejects_incomplete_cached_classic(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch, existing_file: str
    ) -> None:
        version = Version.parse("1.22.22")
        dest = yarn_cache / str(version)
        cached_file = dest / existing_file
        cached_file.parent.mkdir(parents=True)
        cached_file.write_bytes(b"unchanged")
        download = mock.Mock()
        monkeypatch.setattr(installer, "download_binary_file", download)

        with pytest.raises(PackageManagerError):
            install_yarn(version)

        download.assert_not_called()
        assert cached_file.read_bytes() == b"unchanged"
        assert list(yarn_cache.iterdir()) == [dest]

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

        with mock.patch.object(installer.shutil, "copyfileobj", side_effect=error):
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

    @pytest.mark.parametrize("existing_file", ["bin/yarn.js", "lib/cli.js"])
    def test_rejects_incomplete_concurrent_classic(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch, existing_file: str
    ) -> None:
        version = Version.parse("1.22.22")
        dest = yarn_cache / str(version)
        cached_file = dest / existing_file
        content = _make_tarball(
            {"package/bin/yarn.js": b"entry", "package/lib/cli.js": b"library"}
        ).getvalue()

        def download(url: str, path: Path) -> None:
            path.write_bytes(content)
            cached_file.parent.mkdir(parents=True)
            cached_file.write_bytes(b"unchanged")

        monkeypatch.setattr(installer, "download_binary_file", download)

        with pytest.raises(PackageManagerError):
            install_yarn(version)

        assert cached_file.read_bytes() == b"unchanged"
        assert list(dest.rglob("*.js")) == [cached_file]
        assert list(yarn_cache.iterdir()) == [dest]

    @pytest.mark.parametrize("yarn_artifact", ["1.22.22", "4.6.0", "4.6.0-rc.1"], indirect=True)
    def test_preserves_existing_incomplete_cache(
        self,
        yarn_cache: Path,
        yarn_artifact: tuple[Version, bytes],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        version, content = yarn_artifact
        dest = yarn_cache / str(version)
        dest.mkdir(parents=True)
        sentinel = dest / "existing-file"
        sentinel.write_bytes(b"unchanged")
        monkeypatch.setattr(
            installer, "download_binary_file", lambda url, path: path.write_bytes(content)
        )

        with pytest.raises(PackageManagerError):
            install_yarn(version)

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

    def test_missing_berry_entry_is_not_cached(
        self, yarn_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(installer, "download_binary_file", mock.Mock())

        with pytest.raises(PackageManagerError):
            install_yarn(Version.parse("4.6.0"))

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
        monkeypatch.setattr(installer, "_extract_classic", extract)

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
