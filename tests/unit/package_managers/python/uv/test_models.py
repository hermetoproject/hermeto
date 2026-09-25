# SPDX-License-Identifier: GPL-3.0-only
import textwrap
from typing import Any

import pydantic
import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import (
    InvalidLockfileFormat,
    LockfileNotFound,
    MissingChecksum,
    PackageRejected,
    UnexpectedFormat,
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
from tests.common_utils import GIT_REF

SDIST = ArtifactSdist(url="https://example.org/example-1.0.0.tar.gz", hash="sha256:1234")
UNHASHED_SDIST = ArtifactSdist(url="https://example.org/example-1.0.0.tar.gz")
WHEEL = ArtifactWheel(url="https://example.org/example-1.0.0-py3-none-any.whl", hash="sha256:5678")
URL_SOURCE = "https://example.org/downloads/example-1.0.0.tar.gz"


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
        "raw",
        [
            pytest.param({}, id="no_source_key"),
            pytest.param({"bogus": "https://example.org"}, id="unknown_source_key"),
            pytest.param({"bogus": "x", "subdirectory": "sub"}, id="no_key_is_a_kind_key"),
            pytest.param(
                {"registry": "https://pypi.org/simple", "git": "https://github.com/org/repo#abc"},
                id="multiple_source_keys",
            ),
            pytest.param(
                {"registry": "https://pypi.org/simple", "git": "https://h/r#abc", "extra": 1},
                id="reports_only_the_kind_keys",
            ),
        ],
    )
    def test_rejects_invalid_mappings(self, raw: dict[str, Any]) -> None:
        with pytest.raises(pydantic.ValidationError):
            validate_source(raw)


class TestPackageSourceRegistry:
    def test_rejects_empty_index(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            PackageSourceRegistry(kind="registry", location="")

    @pytest.mark.parametrize(
        "index_url, expected_purl",
        [
            pytest.param(
                "https://pypi.org/simple",
                "pkg:pypi/example@1.0.0",
                id="default_pypi_index_needs_no_repository_url",
            ),
            pytest.param(
                "https://example.com/simple",
                "pkg:pypi/example@1.0.0?repository_url=https://example.com/simple",
                id="custom_index_is_recorded_as_repository_url",
            ),
        ],
    )
    def test_purl(self, index_url: str, expected_purl: str) -> None:
        source = PackageSourceRegistry(kind="registry", location=index_url)
        assert source.purl("example", "1.0.0") == expected_purl

    @pytest.mark.parametrize(
        "artifacts, expected",
        [
            pytest.param([SDIST], False, id="index_published_a_hash"),
            pytest.param([UNHASHED_SDIST], True, id="index_published_no_hash"),
            pytest.param([], True, id="nothing_fetched"),
        ],
    )
    def test_records_no_checksum(self, artifacts: list[PackageArtifact], expected: bool) -> None:
        source = PackageSourceRegistry(kind="registry", location="https://pypi.org/simple")
        assert source.records_no_checksum(artifacts) is expected


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
        with pytest.raises(PackageRejected):
            PackageSourceGit(kind="git", location="https://github.com/org/repo?rev=main")

    def test_purl(self) -> None:
        source = PackageSourceGit(
            kind="git", location=f"https://github.com/org/repo?rev=main#{GIT_REF}"
        )
        assert source.purl("example", "1.0.0") == (
            f"pkg:pypi/example@1.0.0?vcs_url=git%2Bhttps://github.com/org/repo%40{GIT_REF}"
        )

    def test_records_no_checksum(self) -> None:
        """uv.lock never pins a checksum for a clone, whatever artifacts it records."""
        source = PackageSourceGit(kind="git", location=f"https://github.com/org/repo#{GIT_REF}")
        assert source.records_no_checksum([SDIST]) is True


class TestPackageSourceUrl:
    def test_purl(self) -> None:
        source = PackageSourceUrl(kind="url", location=URL_SOURCE)
        assert source.purl("example", "1.0.0", "sha256:abcd") == (
            f"pkg:pypi/example@1.0.0?checksum=sha256:abcd&download_url={URL_SOURCE}"
        )

    @pytest.mark.parametrize(
        "artifacts, expected",
        [
            pytest.param([SDIST], False, id="hash_recorded"),
            pytest.param([UNHASHED_SDIST], True, id="no_hash_recorded"),
            pytest.param([], True, id="nothing_fetched"),
        ],
    )
    def test_records_no_checksum(self, artifacts: list[PackageArtifact], expected: bool) -> None:
        """The last two are unreachable through UvPackage: artifacts_to_download rejects them
        first. The guard is kept so a missing hash can never go unmarked, so test it directly.
        """
        source = PackageSourceUrl(kind="url", location=URL_SOURCE)
        assert source.records_no_checksum(artifacts) is expected


class TestPackageSourceLocal:
    """The local purl is reached through UvPackage.purl, the way production builds it."""

    @pytest.mark.parametrize(
        "package, vcs_qualifiers, expected_purl",
        [
            pytest.param(
                make_package(PackageSourceLocal(kind="directory", location="libs/vendored-lib")),
                {"vcs_url": f"git+https://github.com/acme/monorepo@{GIT_REF}"},
                f"pkg:pypi/example@1.0.0?vcs_url=git%2Bhttps://github.com/acme/monorepo%40{GIT_REF}"
                "#libs/vendored-lib",
                id="repo_vcs_url_plus_subpath_to_the_dependency",
            ),
            pytest.param(
                make_package(PackageSourceLocal(kind="directory", location="libs/vendored-lib")),
                None,
                "pkg:pypi/example@1.0.0#libs/vendored-lib",
                id="permissive_mode_without_a_git_repo_omits_vcs_url",
            ),
            pytest.param(
                make_package(PackageSourceLocal(kind="editable", location=".")),
                None,
                "pkg:pypi/example@1.0.0",
                id="dependency_at_the_project_root_gets_no_subpath",
            ),
            pytest.param(
                UvPackage(
                    name="ws-root",
                    source=PackageSourceLocal(kind="editable", location="packages/member"),
                ),
                None,
                "pkg:pypi/ws-root#packages/member",
                id="dynamic_version_locks_without_one_so_purl_omits_it",
            ),
        ],
    )
    def test_purl(
        self,
        package: UvPackage,
        vcs_qualifiers: dict[str, str] | None,
        expected_purl: str,
        rooted_tmp_path: RootedPath,
    ) -> None:
        assert package.purl(rooted_tmp_path, vcs_qualifiers) == expected_purl

    def test_purl_escapes_repo_root(self, rooted_tmp_path: RootedPath) -> None:
        package = make_package(PackageSourceLocal(kind="directory", location="../outside-the-repo"))

        with pytest.raises(PackageRejected):
            package.purl(rooted_tmp_path, None)


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
                PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
                "pkg-1.0.tar.gz",
                id="registry_sdist_from_url",
            ),
            pytest.param(
                ArtifactSdist(path="pkg-1.0.tar.gz"),
                PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
                "pkg-1.0.tar.gz",
                id="sdist_from_path",
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
        source = PackageSourceRegistry(kind="registry", location="https://pypi.org/simple")
        with pytest.raises(PackageRejected):
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
        ArtifactSdist.model_validate(raw)

    def test_rejects_empty_identity(self) -> None:
        with pytest.raises(pydantic.ValidationError):
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
        ArtifactWheel.model_validate(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param({"hash": "sha256:1234"}, id="no_location"),
            pytest.param(
                {"url": "https://example.org/pkg-1.0-py3-none-any.whl", "filename": "other.whl"},
                id="url_and_filename",
            ),
            pytest.param({"path": "pkg.whl", "filename": "other.whl"}, id="path_and_filename"),
        ],
    )
    def test_rejects_wrong_number_of_locations(self, raw: dict[str, Any]) -> None:
        with pytest.raises(pydantic.ValidationError):
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
                PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
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
        package = make_package(
            PackageSourceUrl(kind="url", location=URL_SOURCE), sdist=sdist, wheels=wheels
        )
        assert package.sole_artifact == expected

    def test_artifacts_to_download_registry_sdist(self) -> None:
        """the sdist has a URL, so it should be downloaded"""
        package = make_package(
            PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
            sdist=SDIST,
            wheels=[WHEEL],
        )
        assert package.artifacts_to_download == [SDIST]

    @pytest.mark.parametrize(
        "package, expected_error",
        [
            pytest.param(
                make_package(
                    PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
                    wheels=[WHEEL],
                ),
                PackageRejected,
                id="registry_package_publishes_only_wheels",
            ),
            pytest.param(
                make_package(
                    PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
                    sdist=ArtifactSdist(hash="sha256:1234"),
                ),
                UnexpectedFormat,
                id="registry_sdist_records_no_download_url",
            ),
            pytest.param(
                make_package(
                    PackageSourceUrl(
                        kind="url", location="https://example.org/example-1.0.0.tar.gz"
                    ),
                    sdist=ArtifactSdist(url="https://example.org/example-1.0.0.tar.gz"),
                ),
                MissingChecksum,
                id="url_sdist_records_no_hash",
            ),
        ],
    )
    def test_artifacts_to_download_rejects(
        self, package: UvPackage, expected_error: type[Exception]
    ) -> None:
        with pytest.raises(expected_error):
            _ = package.artifacts_to_download

    @pytest.mark.parametrize(
        "sdist, wheels, expected",
        [
            pytest.param(
                ArtifactSdist(hash="sha256:1234"),
                [],
                ArtifactSdist(url=URL_SOURCE, hash="sha256:1234"),
                id="hash_recorded_under_sdist",
            ),
            pytest.param(
                None,
                [
                    ArtifactWheel(
                        url="https://example.org/downloads/example-1.0.0-py3-none-any.whl",
                        hash="sha256:1234",
                    )
                ],
                ArtifactWheel(url=URL_SOURCE, hash="sha256:1234"),
                id="hash_recorded_under_wheels",
            ),
        ],
    )
    def test_artifacts_to_download_returns_one_artifact_for_url_sources(
        self, sdist: ArtifactSdist | None, wheels: list[ArtifactWheel], expected: PackageArtifact
    ) -> None:
        """the url source(sdist/wheel) should be downloaded"""
        package = make_package(
            PackageSourceUrl(kind="url", location=URL_SOURCE), sdist=sdist, wheels=wheels
        )
        assert package.artifacts_to_download == [expected]

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param(PackageSourceLocal(kind="path", location="../pkg.tar.gz"), id="path"),
            pytest.param(PackageSourceLocal(kind="directory", location="subdir"), id="directory"),
            pytest.param(PackageSourceLocal(kind="editable", location="."), id="editable"),
            pytest.param(PackageSourceLocal(kind="virtual", location="."), id="virtual"),
        ],
    )
    def test_artifacts_to_download_skips_local_sources(self, source: PackageSource) -> None:
        """local sources should not be downloaded"""
        assert make_package(source).artifacts_to_download == []


class TestUvLock:
    def test_from_file(self, rooted_tmp_path: RootedPath) -> None:
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
        with pytest.raises(InvalidLockfileFormat):
            UvLock.from_file(rooted_tmp_path)

    def test_from_file_unsupported_version(self, rooted_tmp_path: RootedPath) -> None:
        write_uv_lock(rooted_tmp_path, "version = 2")
        with pytest.raises(InvalidLockfileFormat):
            UvLock.from_file(rooted_tmp_path)

    def test_from_file_invalid_registry_index(self, rooted_tmp_path: RootedPath) -> None:
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
        with pytest.raises(InvalidLockfileFormat):
            UvLock.from_file(rooted_tmp_path)

    def test_from_file_invalid_structure(self, rooted_tmp_path: RootedPath) -> None:
        write_uv_lock(
            rooted_tmp_path,
            """
            version = 1

            [[package]]
            name = "example"
            version = "1.0.0"
            """,
        )
        with pytest.raises(InvalidLockfileFormat):
            UvLock.from_file(rooted_tmp_path)
