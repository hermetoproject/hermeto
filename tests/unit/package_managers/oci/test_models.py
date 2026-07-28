# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest
import yaml

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound
from hermeto.core.package_managers.oci.models import OciImage, OciLockfile, OciPlatform

VALID_DIGEST = "sha256:" + "a" * 64


def _write_lockfile(tmp_path: Path, data: dict) -> Path:
    lockfile_path = tmp_path / "oci-images.lock.yaml"
    lockfile_path.write_text(yaml.dump(data))
    return lockfile_path


def _minimal_lockfile_data(**overrides: object) -> dict:
    image = {
        "repository": "docker.io/library/alpine",
        "digest": VALID_DIGEST,
        "media_type": "application/vnd.oci.image.manifest.v1+json",
    }
    image.update(overrides)
    return {
        "metadata": {"version": "1.0"},
        "images": [image],
    }


class TestOciLockfileParsing:
    def test_minimal_valid_lockfile(self, tmp_path: Path) -> None:
        path = _write_lockfile(tmp_path, _minimal_lockfile_data())
        lockfile = OciLockfile.from_file(path)

        assert len(lockfile.images) == 1
        assert lockfile.images[0].repository == "docker.io/library/alpine"
        assert lockfile.images[0].digest == VALID_DIGEST

    def test_lockfile_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(LockfileNotFound):
            OciLockfile.from_file(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(": : : invalid yaml [[[")
        with pytest.raises(InvalidLockfileFormat):
            OciLockfile.from_file(path)

    def test_not_a_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(InvalidLockfileFormat, match="expected a YAML mapping"):
            OciLockfile.from_file(path)

    def test_missing_metadata(self, tmp_path: Path) -> None:
        data = {"images": []}
        path = _write_lockfile(tmp_path, data)
        with pytest.raises(InvalidLockfileFormat):
            OciLockfile.from_file(path)

    def test_wrong_metadata_version(self, tmp_path: Path) -> None:
        data = _minimal_lockfile_data()
        data["metadata"]["version"] = "2.0"
        path = _write_lockfile(tmp_path, data)
        with pytest.raises(InvalidLockfileFormat):
            OciLockfile.from_file(path)

    def test_empty_images_rejected(self, tmp_path: Path) -> None:
        data = {"metadata": {"version": "1.0"}, "images": []}
        path = _write_lockfile(tmp_path, data)
        with pytest.raises(InvalidLockfileFormat, match="At least one image"):
            OciLockfile.from_file(path)

    def test_full_lockfile_with_all_fields(self, tmp_path: Path) -> None:
        data = _minimal_lockfile_data(
            tag="3.19",
            platform={"os": "linux", "architecture": "arm64"},
        )
        path = _write_lockfile(tmp_path, data)
        lockfile = OciLockfile.from_file(path)

        image = lockfile.images[0]
        assert image.tag == "3.19"
        assert image.platform is not None
        assert image.platform.os == "linux"
        assert image.platform.architecture == "arm64"

    def test_multiple_images(self, tmp_path: Path) -> None:
        digest2 = "sha256:" + "b" * 64
        data = {
            "metadata": {"version": "1.0"},
            "images": [
                {
                    "repository": "docker.io/library/alpine",
                    "digest": VALID_DIGEST,
                    "media_type": "application/vnd.oci.image.manifest.v1+json",
                },
                {
                    "repository": "ghcr.io/org/tool",
                    "digest": digest2,
                    "media_type": "application/vnd.docker.distribution.manifest.v2+json",
                },
            ],
        }
        path = _write_lockfile(tmp_path, data)
        lockfile = OciLockfile.from_file(path)
        assert len(lockfile.images) == 2

    def test_duplicate_images_rejected(self, tmp_path: Path) -> None:
        data = {
            "metadata": {"version": "1.0"},
            "images": [
                {
                    "repository": "docker.io/library/alpine",
                    "digest": VALID_DIGEST,
                    "media_type": "application/vnd.oci.image.manifest.v1+json",
                },
                {
                    "repository": "docker.io/library/alpine",
                    "digest": VALID_DIGEST,
                    "media_type": "application/vnd.oci.image.manifest.v1+json",
                },
            ],
        }
        path = _write_lockfile(tmp_path, data)
        with pytest.raises(InvalidLockfileFormat, match="Duplicate image entry"):
            OciLockfile.from_file(path)

    def test_extra_fields_rejected(self, tmp_path: Path) -> None:
        data = _minimal_lockfile_data()
        data["images"][0]["unexpected_field"] = "value"
        path = _write_lockfile(tmp_path, data)
        with pytest.raises(InvalidLockfileFormat):
            OciLockfile.from_file(path)


class TestOciImageDigestValidation:
    def test_valid_sha256_digest(self) -> None:
        image = OciImage(
            repository="docker.io/library/alpine",
            digest=VALID_DIGEST,
        )
        assert image.digest == VALID_DIGEST

    def test_invalid_digest_format(self) -> None:
        with pytest.raises(ValueError, match="sha256:<64 hex chars>"):
            OciImage(repository="r", digest="sha256:tooshort")

    def test_invalid_digest_algorithm(self) -> None:
        with pytest.raises(ValueError, match="sha256:<64 hex chars>"):
            OciImage(repository="r", digest="md5:" + "a" * 32)

    def test_invalid_digest_no_colon(self) -> None:
        with pytest.raises(ValueError, match="sha256:<64 hex chars>"):
            OciImage(repository="r", digest="a" * 64)


class TestOciImageMediaTypeValidation:
    def test_oci_manifest(self) -> None:
        image = OciImage(
            repository="r",
            digest=VALID_DIGEST,
            media_type="application/vnd.oci.image.manifest.v1+json",
        )
        assert image.media_type == "application/vnd.oci.image.manifest.v1+json"

    def test_docker_v2_manifest(self) -> None:
        image = OciImage(
            repository="r",
            digest=VALID_DIGEST,
            media_type="application/vnd.docker.distribution.manifest.v2+json",
        )
        assert image.media_type == "application/vnd.docker.distribution.manifest.v2+json"

    def test_oci_index(self) -> None:
        image = OciImage(
            repository="r",
            digest=VALID_DIGEST,
            media_type="application/vnd.oci.image.index.v1+json",
        )
        assert image.media_type == "application/vnd.oci.image.index.v1+json"

    def test_unsupported_media_type(self) -> None:
        with pytest.raises(ValueError, match="Unsupported media type"):
            OciImage(
                repository="r",
                digest=VALID_DIGEST,
                media_type="application/octet-stream",
            )


class TestOciImageRegistryParsing:
    @pytest.mark.parametrize(
        ("repository", "expected_registry", "expected_repo_path"),
        [
            ("docker.io/library/alpine", "docker.io", "library/alpine"),
            ("registry.redhat.io/rhel9/rhel-bootc", "registry.redhat.io", "rhel9/rhel-bootc"),
            ("ghcr.io/org/repo", "ghcr.io", "org/repo"),
            ("localhost:5000/myimage", "localhost:5000", "myimage"),
            ("localhost/myimage", "localhost", "myimage"),
            ("alpine", "docker.io", "library/alpine"),
            ("myorg/myimage", "docker.io", "myorg/myimage"),
            ("my.registry.com:8080/ns/image", "my.registry.com:8080", "ns/image"),
        ],
    )
    def test_registry_and_repo_path(
        self, repository: str, expected_registry: str, expected_repo_path: str
    ) -> None:
        image = OciImage(repository=repository, digest=VALID_DIGEST)
        assert image.registry == expected_registry
        assert image.repo_path == expected_repo_path


class TestOciImageProperties:
    def test_digest_hex(self) -> None:
        image = OciImage(repository="r", digest=VALID_DIGEST)
        assert image.digest_hex == "a" * 64

    def test_sanitized_name(self) -> None:
        image = OciImage(
            repository="registry.redhat.io/rhel9/rhel-bootc",
            digest=VALID_DIGEST,
        )
        assert image.sanitized_name == f"registry.redhat.io_rhel9_rhel-bootc_{'a' * 12}"

    def test_api_host_docker_io(self) -> None:
        image = OciImage(repository="docker.io/library/alpine", digest=VALID_DIGEST)
        assert image.registry == "docker.io"
        assert image.api_host == "registry-1.docker.io"

    def test_api_host_other_registry(self) -> None:
        image = OciImage(repository="ghcr.io/org/repo", digest=VALID_DIGEST)
        assert image.registry == "ghcr.io"
        assert image.api_host == "ghcr.io"


class TestOciPlatform:
    def test_matches_exact(self) -> None:
        platform = OciPlatform(os="linux", architecture="amd64")
        assert platform.matches({"os": "linux", "architecture": "amd64"})

    def test_no_match_different_arch(self) -> None:
        platform = OciPlatform(os="linux", architecture="amd64")
        assert not platform.matches({"os": "linux", "architecture": "arm64"})

    def test_no_match_different_os(self) -> None:
        platform = OciPlatform(os="linux", architecture="amd64")
        assert not platform.matches({"os": "windows", "architecture": "amd64"})

    def test_default_os_is_linux(self) -> None:
        platform = OciPlatform(architecture="amd64")
        assert platform.os == "linux"
