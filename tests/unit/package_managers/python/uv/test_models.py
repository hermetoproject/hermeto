# SPDX-License-Identifier: GPL-3.0-only
from typing import Any

import pydantic
import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import PackageRejected
from hermeto.core.package_managers.python.uv.models import (
    ArtifactSdist,
    ArtifactWheel,
    PackageArtifact,
    PackageSource,
    PackageSourceGit,
    PackageSourceLocal,
    PackageSourceRegistry,
    PackageSourceUrl,
)


class TestPackageSourceRegistry:
    def test_rejects_empty_index(self) -> None:
        with pytest.raises(pydantic.ValidationError):
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
        with pytest.raises(PackageRejected):
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
