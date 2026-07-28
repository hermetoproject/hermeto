# SPDX-License-Identifier: GPL-3.0-only
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound
from hermeto.core.package_managers.generic.models import LockfileArtifactAuth

DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")

OCI_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)

DEFAULT_LOCKFILE = "oci-images.lock.yaml"


class OciPlatform(BaseModel):
    """Platform selector for multi-arch image indexes."""

    model_config = ConfigDict(extra="forbid")

    os: str = "linux"
    architecture: str

    def matches(self, manifest_platform: dict[str, str]) -> bool:
        """Check whether this selector matches a platform entry from an image index."""
        return (
            manifest_platform.get("os") == self.os
            and manifest_platform.get("architecture") == self.architecture
        )


class OciImage(BaseModel):
    """A single OCI image entry in the lockfile."""

    model_config = ConfigDict(extra="forbid")

    repository: str
    digest: str
    media_type: str = "application/vnd.oci.image.manifest.v1+json"
    platform: OciPlatform | None = None
    tag: str | None = None
    auth: LockfileArtifactAuth | None = None

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError(
                f"Digest must be in the format 'sha256:<64 hex chars>' (got '{value}')"
            )
        return value

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, value: str) -> str:
        if value not in OCI_MEDIA_TYPES:
            raise ValueError(
                f"Unsupported media type '{value}'. "
                f"Supported types: {', '.join(sorted(OCI_MEDIA_TYPES))}"
            )
        return value

    @property
    def registry(self) -> str:
        """Extract the canonical registry host from the repository."""
        parts = self.repository.split("/", 1)
        if len(parts) == 1:
            return "docker.io"
        first = parts[0]
        if "." in first or ":" in first or first == "localhost":
            return first
        return "docker.io"

    @property
    def api_host(self) -> str:
        """Return the actual API hostname for HTTP requests."""
        registry = self.registry
        if registry == "docker.io":
            return "registry-1.docker.io"
        return registry

    @property
    def repo_path(self) -> str:
        """Extract the repository path (without registry) from the repository."""
        parts = self.repository.split("/", 1)
        if len(parts) == 1:
            return f"library/{parts[0]}"
        first = parts[0]
        if "." in first or ":" in first or first == "localhost":
            return parts[1]
        return self.repository

    @property
    def digest_hex(self) -> str:
        """Return just the hex portion of the digest."""
        return self.digest.split(":", 1)[1]

    @property
    def sanitized_name(self) -> str:
        """Return a filesystem-safe name derived from the repository and digest."""
        repo_part = self.repository.replace("/", "_").replace(":", "_")
        return f"{repo_part}_{self.digest_hex[:12]}"


class OciLockfileMetadata(BaseModel):
    """Metadata section of the OCI lockfile."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"]


class OciLockfile(BaseModel):
    """The top-level OCI images lockfile."""

    model_config = ConfigDict(extra="forbid")

    metadata: OciLockfileMetadata
    images: list[OciImage]

    @field_validator("images")
    @classmethod
    def _validate_images_non_empty(cls, value: list[OciImage]) -> list[OciImage]:
        if not value:
            raise ValueError("At least one image must be declared in the lockfile")
        return value

    @model_validator(mode="after")
    def _validate_no_duplicate_digests(self) -> "OciLockfile":
        seen: set[str] = set()
        for image in self.images:
            key = f"{image.repository}@{image.digest}"
            if key in seen:
                raise ValueError(f"Duplicate image entry: {key}")
            seen.add(key)
        return self

    @classmethod
    def from_file(cls, path: Path) -> "OciLockfile":
        """Parse an OCI lockfile from the given path."""
        if not path.is_file():
            if path.exists():
                raise InvalidLockfileFormat(
                    path,
                    f"expected a file but found a {path.stat().st_mode & 0o170000:#o} entry. "
                    "Please provide a readable YAML lockfile.",
                )
            raise LockfileNotFound(
                path,
                solution=(
                    "Please provide an OCI images lockfile. "
                    "See the documentation for the expected format."
                ),
            )

        try:
            with path.open() as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as e:
            raise InvalidLockfileFormat(path, str(e)) from e

        if not isinstance(data, dict):
            raise InvalidLockfileFormat(
                path,
                "expected a YAML mapping at the top level",
            )

        try:
            return cls.model_validate(data)
        except Exception as e:
            raise InvalidLockfileFormat(path, str(e)) from e
