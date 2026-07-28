# SPDX-License-Identifier: GPL-3.0-only
import pytest

from hermeto.core.errors import FetchError
from hermeto.core.package_managers.oci.models import OciPlatform
from hermeto.core.package_managers.oci.oci_client import (
    extract_blob_descriptors,
    parse_www_authenticate,
    select_platform,
)

VALID_DIGEST = "sha256:" + "a" * 64


class TestParseWwwAuthenticate:
    def test_standard_bearer_challenge(self) -> None:
        header = (
            'Bearer realm="https://auth.docker.io/token",'
            'service="registry.docker.io",'
            'scope="repository:library/alpine:pull"'
        )
        realm, service, scope = parse_www_authenticate(header, "library/alpine")
        assert realm == "https://auth.docker.io/token"
        assert service == "registry.docker.io"
        assert scope == "repository:library/alpine:pull"

    def test_missing_scope_uses_default(self) -> None:
        header = 'Bearer realm="https://auth.example.com/token",service="example.com"'
        realm, _, scope = parse_www_authenticate(header, "org/repo")
        assert realm == "https://auth.example.com/token"
        assert scope == "repository:org/repo:pull"

    def test_missing_service(self) -> None:
        header = 'Bearer realm="https://auth.example.com/token"'
        _, service, _ = parse_www_authenticate(header, "org/repo")
        assert service == ""

    def test_case_insensitive_bearer(self) -> None:
        header = 'bearer realm="https://auth.example.com/token"'
        realm, _, _ = parse_www_authenticate(header, "org/repo")
        assert realm == "https://auth.example.com/token"

    def test_unsupported_scheme_raises(self) -> None:
        with pytest.raises(FetchError, match="Unsupported WWW-Authenticate"):
            parse_www_authenticate('Basic realm="example"', "org/repo")

    def test_missing_realm_raises(self) -> None:
        with pytest.raises(FetchError, match="missing the 'realm'"):
            parse_www_authenticate('Bearer service="foo"', "org/repo")


class TestSelectPlatform:
    def _make_index(self, platforms: list[dict[str, str]]) -> dict:
        return {
            "manifests": [
                {
                    "digest": f"sha256:{'0' * 63}{i}",
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": p,
                }
                for i, p in enumerate(platforms)
            ]
        }

    def test_selects_matching_platform(self) -> None:
        index = self._make_index(
            [
                {"os": "linux", "architecture": "amd64"},
                {"os": "linux", "architecture": "arm64"},
            ]
        )
        platform = OciPlatform(os="linux", architecture="arm64")
        digest, media_type = select_platform(index, platform, "test/repo")

        assert digest == f"sha256:{'0' * 63}1"
        assert media_type == "application/vnd.oci.image.manifest.v1+json"

    def test_no_matching_platform_raises(self) -> None:
        index = self._make_index(
            [
                {"os": "linux", "architecture": "amd64"},
            ]
        )
        platform = OciPlatform(os="linux", architecture="s390x")

        with pytest.raises(FetchError, match="No manifest found for platform"):
            select_platform(index, platform, "test/repo")

    def test_error_lists_available_platforms(self) -> None:
        index = self._make_index(
            [
                {"os": "linux", "architecture": "amd64"},
                {"os": "linux", "architecture": "arm64"},
            ]
        )
        platform = OciPlatform(os="windows", architecture="amd64")

        with pytest.raises(FetchError, match=r"linux/amd64.*linux/arm64"):
            select_platform(index, platform, "test/repo")

    def test_empty_manifests_raises(self) -> None:
        index = {"manifests": []}
        platform = OciPlatform(os="linux", architecture="amd64")

        with pytest.raises(FetchError, match="No manifest found"):
            select_platform(index, platform, "test/repo")


class TestExtractBlobDescriptors:
    def test_extracts_config_and_layers(self) -> None:
        manifest = {
            "config": {
                "digest": "sha256:config",
                "size": 100,
                "mediaType": "application/vnd.oci.image.config.v1+json",
            },
            "layers": [
                {
                    "digest": "sha256:layer1",
                    "size": 1000,
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                },
                {
                    "digest": "sha256:layer2",
                    "size": 2000,
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                },
            ],
        }
        descriptors = extract_blob_descriptors(manifest)
        assert len(descriptors) == 3
        assert descriptors[0]["digest"] == "sha256:config"
        assert descriptors[1]["digest"] == "sha256:layer1"
        assert descriptors[2]["digest"] == "sha256:layer2"

    def test_no_config(self) -> None:
        manifest = {
            "layers": [
                {"digest": "sha256:layer1", "size": 1000},
            ],
        }
        descriptors = extract_blob_descriptors(manifest)
        assert len(descriptors) == 1

    def test_no_layers(self) -> None:
        manifest = {
            "config": {"digest": "sha256:config", "size": 100},
        }
        descriptors = extract_blob_descriptors(manifest)
        assert len(descriptors) == 1

    def test_empty_manifest(self) -> None:
        descriptors = extract_blob_descriptors({})
        assert len(descriptors) == 0
