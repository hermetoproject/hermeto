# SPDX-License-Identifier: GPL-3.0-only
import re
import textwrap
from typing import Any

import pydantic
import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import (
    InvalidLockfileFormat,
    LockfileNotFound,
    PackageRejected,
)
from hermeto.core.package_managers.python.uv.models import (
    ArtifactSdist,
    ArtifactWheel,
    PackageArtifact,
    PackageSource,
    PackageSourceGit,
    PackageSourceLocal,
    PackageSourceRegistry,
    PackageSourceUrl,
    UvLock,
    UvPackage,
)
from hermeto.core.rooted_path import RootedPath

URL_SOURCE = "https://example.org/downloads/example-1.0.0.tar.gz"

PYPI_SOURCE = PackageSourceRegistry(kind="registry", location="https://pypi.org/simple")
DIRECT_URL_SOURCE = PackageSourceUrl(kind="url", location=URL_SOURCE)


def write_uv_lock(rooted_path: RootedPath, content: str) -> None:
    (rooted_path.path / "uv.lock").write_text(textwrap.dedent(content))


def make_package(
    source: PackageSource,
    sdist: ArtifactSdist | None = None,
    wheels: list[ArtifactWheel] | None = None,
) -> UvPackage:
    return UvPackage(
        name="example", version="1.0.0", source=source, sdist=sdist, wheels=wheels or []
    )


def validate_source(raw: dict[str, Any]) -> PackageSource:
    """Validate a raw uv.lock source table the only way production does, through UvPackage."""
    return UvPackage.model_validate({"name": "example", "source": raw}).source


class TestPackageSourceNormalization:
    @pytest.mark.parametrize(
        "raw, expected_model, expected_kind, expected_location",
        [
            pytest.param(
                {"registry": "https://pypi.org/simple"},
                PackageSourceRegistry,
                "registry",
                "https://pypi.org/simple",
                id="registry",
            ),
            pytest.param(
                {"registry": "http://internal-mirror:8080/simple"},
                PackageSourceRegistry,
                "registry",
                "http://internal-mirror:8080/simple",
                id="registry_http_custom_index",
            ),
            pytest.param(
                {"git": "https://github.com/org/repo?rev=main#0123abcd"},
                PackageSourceGit,
                "git",
                "https://github.com/org/repo?rev=main#0123abcd",
                id="git",
            ),
            pytest.param(
                {"url": "https://example.org/pkg-1.0.tar.gz"},
                PackageSourceUrl,
                "url",
                "https://example.org/pkg-1.0.tar.gz",
                id="url",
            ),
            pytest.param(
                {"path": "../local/pkg.tar.gz"},
                PackageSourceLocal,
                "path",
                "../local/pkg.tar.gz",
                id="path",
            ),
            pytest.param(
                {"directory": "subdir"}, PackageSourceLocal, "directory", "subdir", id="directory"
            ),
            pytest.param({"editable": "."}, PackageSourceLocal, "editable", ".", id="editable"),
            pytest.param({"virtual": "."}, PackageSourceLocal, "virtual", ".", id="virtual"),
            pytest.param(
                {"git": "https://github.com/org/repo#0123abcd", "subdirectory": "packages/sub"},
                PackageSourceGit,
                "git",
                "https://github.com/org/repo#0123abcd",
                id="extra_keys_are_ignored",
            ),
            pytest.param(
                {"kind": "registry", "location": "https://pypi.org/simple"},
                PackageSourceRegistry,
                "registry",
                "https://pypi.org/simple",
                id="already_normalized_passthrough",
            ),
        ],
    )
    def test_normalizes_uv_source_table(
        self,
        raw: dict[str, Any],
        expected_model: type[PackageSource],
        expected_kind: str,
        expected_location: str,
    ) -> None:
        source = validate_source(raw)
        assert isinstance(source, expected_model)
        assert source.kind == expected_kind
        assert source.location == expected_location

    @pytest.mark.parametrize(
        "raw, expected_got",
        [
            pytest.param({}, "got []", id="no_source_key"),
            pytest.param({"bogus": "https://example.org"}, "got []", id="unknown_source_key"),
            pytest.param(
                {"bogus": "x", "subdirectory": "sub"}, "got []", id="no_key_is_a_kind_key"
            ),
            pytest.param(
                {"registry": "https://pypi.org/simple", "git": "https://github.com/org/repo#abc"},
                "got ['registry', 'git']",
                id="multiple_source_keys",
            ),
            pytest.param(
                {"registry": "https://pypi.org/simple", "git": "https://h/r#abc", "extra": 1},
                "got ['registry', 'git']",
                id="reports_only_the_kind_keys",
            ),
        ],
    )
    def test_rejects_invalid_mappings(self, raw: dict[str, Any], expected_got: str) -> None:
        """The message reports the kind keys uv could have written, not the whole table."""
        with pytest.raises(
            pydantic.ValidationError,
            match=rf"source must contain exactly one of .*{re.escape(expected_got)}",
        ):
            validate_source(raw)


class TestPackageSourceRegistry:
    def test_rejects_empty_index(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="registry source must not be empty"):
            PackageSourceRegistry(kind="registry", location="")


class TestPackageSourceGit:
    @pytest.mark.parametrize(
        "location, expected",
        [
            pytest.param(
                "https://github.com/org/repo?tag=v1.0#0123abcd",
                "https://github.com/org/repo",
                id="query_and_fragment_stripped",
            ),
            pytest.param(
                "https://github.com/org/repo#0123abcd",
                "https://github.com/org/repo",
                id="fragment_only",
            ),
        ],
    )
    def test_clone_url(self, location: str, expected: str) -> None:
        assert PackageSourceGit(kind="git", location=location).clone_url == expected

    def test_commit(self) -> None:
        source = PackageSourceGit(
            kind="git", location="https://github.com/org/repo?rev=main#0123abcd"
        )
        assert source.commit == "0123abcd"

    def test_missing_commit(self) -> None:
        with pytest.raises(PackageRejected, match="does not pin a commit"):
            PackageSourceGit(kind="git", location="https://github.com/org/repo?rev=main")


class TestPackageArtifact:
    @pytest.mark.parametrize(
        "recorded_hash, expected",
        [
            pytest.param("sha256:1234", ChecksumInfo("sha256", "1234"), id="hash_recorded"),
            pytest.param(None, None, id="no_hash_recorded"),
        ],
    )
    def test_checksum_info(self, recorded_hash: str | None, expected: ChecksumInfo | None) -> None:
        artifact = PackageArtifact(url="https://example.org/pkg-1.0.tar.gz", hash=recorded_hash)
        assert artifact.checksum_info == expected

    @pytest.mark.parametrize(
        "artifact, source, expected",
        [
            pytest.param(
                ArtifactSdist(url="https://example.org/pkg-1.0.tar.gz"),
                PYPI_SOURCE,
                "pkg-1.0.tar.gz",
                id="registry_sdist_from_url",
            ),
            pytest.param(
                ArtifactSdist(path="pkg-1.0.tar.gz"),
                PYPI_SOURCE,
                "pkg-1.0.tar.gz",
                id="registry_sdist_from_path",
            ),
            pytest.param(
                ArtifactSdist(url="https://example.org/pkg-1.0.tar.gz", path="other-1.0.tar.gz"),
                PYPI_SOURCE,
                "pkg-1.0.tar.gz",
                id="registry_sdist_url_wins_over_path",
            ),
            pytest.param(
                ArtifactSdist(hash="sha256:1234"),
                PackageSourceUrl(kind="url", location="https://example.org/pkg-1.0.tar.gz"),
                "pkg-1.0.tar.gz",
                id="bare_hash_sdist_falls_back_to_source",
            ),
        ],
    )
    def test_get_target_filename(
        self, artifact: PackageArtifact, source: PackageSource, expected: str
    ) -> None:
        assert artifact.get_target_filename(source) == expected

    def test_get_target_filename_no_name_in_url(self) -> None:
        artifact = ArtifactSdist(url="https://example.org/")
        source = PYPI_SOURCE
        with pytest.raises(PackageRejected, match="Cannot determine a file name"):
            artifact.get_target_filename(source)


class TestArtifactSdist:
    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param({"url": "https://example.org/pkg-1.0.tar.gz"}, id="url_only"),
            pytest.param({"path": "pkg-1.0.tar.gz"}, id="path_only"),
            pytest.param({"hash": "sha256:1234"}, id="hash_only"),
        ],
    )
    def test_accepts_any_identity(self, raw: dict[str, Any]) -> None:
        sdist = ArtifactSdist.model_validate(raw)
        assert sdist.model_dump(exclude_none=True) == raw

    def test_rejects_empty_identity(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="at least one of"):
            ArtifactSdist.model_validate({"size": 42})


class TestArtifactWheel:
    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param({"url": "https://example.org/pkg-1.0-py3-none-any.whl"}, id="url_only"),
            pytest.param({"path": "pkg-1.0-py3-none-any.whl"}, id="path_only"),
            pytest.param({"filename": "pkg-1.0-py3-none-any.whl"}, id="filename_only"),
        ],
    )
    def test_accepts_one_location(self, raw: dict[str, Any]) -> None:
        wheel = ArtifactWheel.model_validate(raw)
        assert wheel.model_dump(exclude_none=True) == raw

    @pytest.mark.parametrize(
        "raw, expected_got",
        [
            pytest.param({"hash": "sha256:1234"}, "got {}", id="no_location"),
            pytest.param(
                {"url": "https://example.org/pkg-1.0-py3-none-any.whl", "filename": "other.whl"},
                "got {'url': 'https://example.org/pkg-1.0-py3-none-any.whl', "
                "'filename': 'other.whl'}",
                id="url_and_filename",
            ),
            pytest.param(
                {"path": "pkg.whl", "filename": "other.whl"},
                "got {'path': 'pkg.whl', 'filename': 'other.whl'}",
                id="path_and_filename",
            ),
        ],
    )
    def test_rejects_wrong_number_of_locations(
        self, raw: dict[str, Any], expected_got: str
    ) -> None:
        """The message names the locations recorded, so the offending entry is identifiable."""
        with pytest.raises(
            pydantic.ValidationError,
            match=rf"wheel must contain exactly one of .*{re.escape(expected_got)}",
        ):
            ArtifactWheel.model_validate(raw)

    @pytest.mark.parametrize(
        "artifact, source, expected",
        [
            pytest.param(
                ArtifactWheel(filename="pkg-1.0-py3-none-any.whl"),
                PackageSourceLocal(kind="path", location="dist/pkg-1.0-py3-none-any.whl"),
                "pkg-1.0-py3-none-any.whl",
                id="path_wheel_from_filename",
            ),
            pytest.param(
                ArtifactWheel(url="https://example.org/downloads/pkg-1.0-py3-none-any.whl?token=x"),
                PYPI_SOURCE,
                "pkg-1.0-py3-none-any.whl",
                id="wheel_url_query_stripped",
            ),
        ],
    )
    def test_get_target_filename(
        self, artifact: PackageArtifact, source: PackageSource, expected: str
    ) -> None:
        assert artifact.get_target_filename(source) == expected


class TestUvPackage:
    @pytest.mark.parametrize(
        "sdist, wheels, expected",
        [
            pytest.param(
                ArtifactSdist(hash="sha256:1234"), [], ArtifactSdist(hash="sha256:1234"), id="sdist"
            ),
            pytest.param(
                None,
                [ArtifactWheel(filename="pkg-1.0-py3-none-any.whl")],
                ArtifactWheel(filename="pkg-1.0-py3-none-any.whl"),
                id="single_wheel",
            ),
            pytest.param(None, [], None, id="neither"),
        ],
    )
    def test_sole_artifact(
        self,
        sdist: ArtifactSdist | None,
        wheels: list[ArtifactWheel],
        expected: PackageArtifact | None,
    ) -> None:
        package = make_package(DIRECT_URL_SOURCE, sdist=sdist, wheels=wheels)
        assert package.sole_artifact == expected


class TestUvLock:
    def test_from_file(self, rooted_tmp_path: RootedPath) -> None:
        """Parse a whole document. Every key below earns its place.

        ``revision``, ``requires-python`` and ``[options]`` are unmodelled, so
        ``extra="ignore"`` has to drop them instead of rejecting the file; ``size`` is
        modelled, so it has to survive; and ``[[package]]`` has to land in ``packages``
        through the alias. Deleting any of them stops testing something.
        """
        write_uv_lock(
            rooted_tmp_path,
            """
            version = 1
            revision = 2
            requires-python = ">=3.9"

            [options]
            exclude-newer = "2024-01-01T00:00:00Z"

            [[package]]
            name = "example"
            version = "1.0.0"
            source = { registry = "https://pypi.org/simple" }
            sdist = { url = "https://example.org/example-1.0.0.tar.gz", hash = "sha256:1234", size = 100 }
            wheels = [
                { url = "https://example.org/example-1.0.0-py3-none-any.whl", hash = "sha256:5678" },
            ]

            [[package]]
            name = "local-pkg"
            version = "0.1.0"
            source = { editable = "." }
            """,
        )

        lock = UvLock.from_file(rooted_tmp_path)

        assert lock.version == 1
        assert len(lock.packages) == 2

        example = lock.packages[0]
        assert example.name == "example"
        assert example.version == "1.0.0"
        assert example.source.kind == "registry"
        assert example.sdist == ArtifactSdist(
            url="https://example.org/example-1.0.0.tar.gz", hash="sha256:1234", size=100
        )
        assert example.wheels == [
            ArtifactWheel(
                url="https://example.org/example-1.0.0-py3-none-any.whl", hash="sha256:5678"
            )
        ]

        local = lock.packages[1]
        assert isinstance(local.source, PackageSourceLocal)

    def test_from_file_dynamic_version(self, rooted_tmp_path: RootedPath) -> None:
        """uv omits `version` for a source tree that declares `dynamic = ["version"]`."""
        write_uv_lock(
            rooted_tmp_path,
            """
            version = 1

            [[package]]
            name = "ws-root"
            source = { editable = "." }

            [[package]]
            name = "member"
            version = "2.0.0"
            source = { editable = "packages/member" }
            """,
        )

        lock = UvLock.from_file(rooted_tmp_path)

        assert lock.packages[0].version is None
        assert lock.packages[1].version == "2.0.0"

    def test_from_file_missing(self, rooted_tmp_path: RootedPath) -> None:
        with pytest.raises(LockfileNotFound):
            UvLock.from_file(rooted_tmp_path)

    def test_from_file_invalid_toml(self, rooted_tmp_path: RootedPath) -> None:
        write_uv_lock(rooted_tmp_path, "version = [not toml")
        with pytest.raises(InvalidLockfileFormat, match="Invalid TOML syntax"):
            UvLock.from_file(rooted_tmp_path)

    def test_from_file_unsupported_version(self, rooted_tmp_path: RootedPath) -> None:
        write_uv_lock(rooted_tmp_path, "version = 2")
        with pytest.raises(InvalidLockfileFormat, match="unsupported uv.lock version: 2"):
            UvLock.from_file(rooted_tmp_path)

    def test_from_file_error_names_the_offending_package(self, rooted_tmp_path: RootedPath) -> None:
        """from_toml reports where in the document validation failed, not just why."""
        write_uv_lock(
            rooted_tmp_path,
            """
            version = 1

            [[package]]
            name = "example"
            version = "1.0.0"
            source = { registry = "" }
            """,
        )
        with pytest.raises(
            InvalidLockfileFormat, match=r"package\.0\.source.*registry source must not be empty"
        ):
            UvLock.from_file(rooted_tmp_path)

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(
                """
                version = 1

                [[package]]
                name = "example"
                version = "1.0.0"
                """,
                id="package_without_a_source",
            ),
            pytest.param(
                """
                revision = 2

                [[package]]
                name = "example"
                version = "1.0.0"
                source = { registry = "https://pypi.org/simple" }
                """,
                id="lockfile_without_a_version",
            ),
        ],
    )
    def test_from_file_invalid_structure(self, content: str, rooted_tmp_path: RootedPath) -> None:
        write_uv_lock(rooted_tmp_path, content)
        with pytest.raises(InvalidLockfileFormat):
            UvLock.from_file(rooted_tmp_path)
