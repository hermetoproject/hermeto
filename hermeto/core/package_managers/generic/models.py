# SPDX-License-Identifier: GPL-3.0-only
import os
import re
from abc import ABC, abstractmethod
from base64 import b64encode
from collections import Counter
from collections.abc import Sequence
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urljoin, urlparse

from packageurl import PackageURL
from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    PlainSerializer,
    field_validator,
    model_validator,
)
from pydantic_core.core_schema import ValidationInfo
from typing_extensions import override

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import PackageManagerError
from hermeto.core.models.sbom import Component, ExternalReference
from hermeto.core.rooted_path import RootedPath

CHECKSUM_FORMAT = re.compile(r"^[a-zA-Z0-9]+:[a-zA-Z0-9]+$")
ENV_VAR_PATTERN = re.compile(r"(?<!\\)\$(?:(\w+)|\{(\w+)\})")


class AbstractAuth(BaseModel, ABC):
    """Abstract base class for artifact authentication."""

    model_config = ConfigDict(extra="forbid")

    @abstractmethod
    def to_headers(self) -> dict[str, str]:
        """Return the authentication headers."""

    @model_validator(mode="after")
    def validate_headers(self) -> "AbstractAuth":
        """Validate that the headers are not empty."""
        if not self.to_headers():
            raise ValueError("Headers cannot be empty")
        return self

    @field_validator("*")
    @classmethod
    def _expand_env_vars_in_fields(cls, value: Any) -> Any:
        """Expand environment variables in the fields."""

        if not isinstance(value, str):
            return value

        def get_env_var(match: re.Match[str]) -> str:
            var_name = match.group(1) or match.group(2)
            if var_name in os.environ:
                return os.environ[var_name]
            raise ValueError(f"Environment variable {var_name} is not set")

        return re.sub(ENV_VAR_PATTERN, get_env_var, value).replace("\\$", "$")


class BasicAuth(AbstractAuth):
    """Defines format of the basic auth section in the lockfile."""

    username: str
    password: str

    @override
    def to_headers(self) -> dict[str, str]:
        encoded = b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}


class BearerAuth(AbstractAuth):
    """Defines format of the bearer auth section in the lockfile."""

    header: Optional[str] = None
    value: str

    @override
    def to_headers(self) -> dict[str, str]:
        return {self.header or "Authorization": self.value}


class LockfileArtifactAuth(BaseModel):
    """Defines format of the auth section in the lockfile."""

    basic: Optional[BasicAuth] = None
    bearer: Optional[BearerAuth] = None
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _check_mutually_exclusive(cls, values: dict) -> dict:
        if sum(1 for v in values.values() if v is not None) != 1:
            raise ValueError("Exactly one of the auth types must be set")
        return values

    def get_headers(self) -> dict[str, str]:
        """Return the headers for the artifact."""
        auth = self.basic or self.bearer
        return auth.to_headers() if auth else {}


class LockfileMetadata(BaseModel):
    """Defines format of the metadata section in the lockfile."""

    version: Literal["1.0"]
    model_config = ConfigDict(extra="forbid")


class LockfileMetadataV2(BaseModel):
    """Defines format of the metadata section in the lockfile, version 2.0."""

    version: Literal["2.0"]
    model_config = ConfigDict(extra="forbid")


class LockfileArtifactBase(BaseModel, ABC):
    """
    Base class for artifacts in the lockfile.

    :param filename: The target path to save the artifact to. Subpath of the deps/generic folder.
    :param checksum: Checksum of the artifact in the format "algorithm:hash"
    """

    filename: str = ""
    checksum: str
    model_config = ConfigDict(extra="forbid")

    @field_validator("checksum")
    @classmethod
    def checksum_format(cls, value: str) -> str:
        """
        Validate that the provided checksum string is in the format "algorithm:hash".

        :param value: the checksums dict to validate
        :return: the validated checksum dict
        """
        if not CHECKSUM_FORMAT.match(value):
            raise ValueError(f"Checksum must be in the format 'algorithm:hash' (got '{value}')")
        return value

    @abstractmethod
    def resolve_filename(self) -> str:
        """Resolve the filename of the artifact."""

    @abstractmethod
    def get_sbom_component(self) -> Component:
        """Return an SBOM component representation of the artifact."""

    @property
    def formatted_checksum(self) -> ChecksumInfo:
        """Return the checksum as a ChecksumInfo object."""
        algorithm, digest = self.checksum.split(":", 1)
        return ChecksumInfo(algorithm, digest)

    @model_validator(mode="after")
    def set_filename(self, info: ValidationInfo) -> "LockfileArtifactBase":
        """Set the target path if not provided and resolve it into an absolute path."""
        self.filename = self.resolve_filename()

        # needs to have output_dir context in order to be able to resolve the target path
        # and so that it can be used to check for conflicts with other artifacts
        if not info.context or "output_dir" not in info.context:
            raise PackageManagerError(
                "The `LockfileArtifact` class needs to be called with `output_dir` in the context"
            )
        output_dir: RootedPath = info.context["output_dir"]
        self.filename = str(output_dir.join_within_root(self.filename).path.resolve())

        return self


class LockfileArtifactUrl(LockfileArtifactBase):
    """
    Defines format of a single artifact in the lockfile.

    :param download_url: The URL to download the artifact from.
    """

    download_url: Annotated[AnyUrl, PlainSerializer(str, return_type=str)]

    def resolve_filename(self) -> str:
        """Resolve the filename of the artifact."""
        if not self.filename:
            url_path = urlparse(str(self.download_url)).path
            return Path(url_path).name
        return self.filename

    def get_sbom_component(self) -> Component:
        """Return an SBOM component representation of the artifact."""
        name = Path(self.filename).name
        url = str(self.download_url)
        component = Component(
            name=name,
            purl=PackageURL(
                type="generic",
                name=name,
                qualifiers={
                    "download_url": url,
                    "checksum": self.checksum,
                },
            ).to_string(),
            type="file",
            external_references=[ExternalReference(url=url, type="distribution")],
        )
        return component


class LockfileArtifactUrlV2(LockfileArtifactUrl):
    """V2 URL artifact that supports optional authentication."""

    auth: Optional[LockfileArtifactAuth] = None


class LockfileArtifactMavenAttributes(BaseModel):
    """Attributes for a Maven artifact in the lockfile."""

    repository_url: Annotated[AnyUrl, PlainSerializer(str, return_type=str)]
    group_id: str
    artifact_id: str
    version: str
    classifier: str = ""
    type: str = "jar"

    @cached_property
    def extension(self) -> str:
        """Return the extension of the artifact."""
        type_to_extension = {
            "pom": "pom",
            "jar": "jar",
            "maven-plugin": "jar",
            "ear": "ear",
            "ejb": "jar",
            "ejb-client": "jar",
            "javadoc": "jar",
            "javadoc-source": "jar",
            "rar": "rar",
            "test-jar": "jar",
            "war": "war",
        }
        return type_to_extension.get(self.type, self.type)


class LockfileArtifactMaven(LockfileArtifactBase):
    """Defines format of a Maven artifact in the lockfile."""

    type: Literal["maven"]
    attributes: LockfileArtifactMavenAttributes

    @cached_property
    def filename_from_attributes(self) -> str:
        """Return the filename of the artifact."""
        artifact_id = self.attributes.artifact_id
        version = self.attributes.version

        filename = f"{artifact_id}-{version}"
        if self.attributes.classifier:
            filename += f"-{self.attributes.classifier}"

        return f"{filename}.{self.attributes.extension}"

    @cached_property
    def download_url(self) -> str:
        """Return the download URL of the artifact."""
        group_id = self.attributes.group_id.replace(".", "/")
        artifact_id = self.attributes.artifact_id
        version = self.attributes.version

        url_path = f"{group_id}/{artifact_id}/{version}/{self.filename_from_attributes}"

        # ensure repository url has a slash in the end, otherwise the last part will
        # be replaced by the url_path
        repo_url = str(self.attributes.repository_url)
        if not repo_url.endswith("/"):
            repo_url += "/"
        return urljoin(repo_url, url_path)

    def resolve_filename(self) -> str:
        """Resolve the filename of the artifact."""
        return self.filename if self.filename else self.filename_from_attributes

    def get_sbom_component(self) -> Component:
        """Return an SBOM component representation of the artifact."""
        purl_qualifiers = {
            "type": self.attributes.type,
            "repository_url": str(self.attributes.repository_url),
            "checksum": self.checksum,
        }
        if self.attributes.classifier:
            purl_qualifiers["classifier"] = self.attributes.classifier

        return Component(
            name=self.attributes.artifact_id,
            version=self.attributes.version,
            purl=PackageURL(
                type="maven",
                name=self.attributes.artifact_id,
                namespace=self.attributes.group_id,
                version=self.attributes.version,
                qualifiers=purl_qualifiers,
            ).to_string(),
            type="library",
            external_references=[ExternalReference(url=self.download_url, type="distribution")],
        )


class LockfileArtifactMavenV2(LockfileArtifactMaven):
    """V2 Maven artifact that supports optional authentication."""

    auth: Optional[LockfileArtifactAuth] = None


def _validate_no_artifact_conflicts(
    artifacts: Sequence[LockfileArtifactUrl | LockfileArtifactMaven],
) -> None:
    """Validate that all artifacts have unique filenames and download_urls."""
    urls = Counter(a.download_url for a in artifacts)
    filenames = Counter(a.filename for a in artifacts)
    duplicate_urls = [str(u) for u, count in urls.most_common() if count > 1]
    duplicate_filenames = [t for t, count in filenames.most_common() if count > 1]
    if duplicate_urls or duplicate_filenames:
        raise ValueError(
            (f"Duplicate download_urls: {duplicate_urls}\n" if duplicate_urls else "")
            + (f"Duplicate filenames: {duplicate_filenames}" if duplicate_filenames else "")
        )


class GenericLockfileV1(BaseModel):
    """Defines format of our generic lockfile, version 1.0."""

    metadata: LockfileMetadata
    artifacts: list[LockfileArtifactUrl | LockfileArtifactMaven]
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def no_artifact_conflicts(self) -> "GenericLockfileV1":
        """Validate that all artifacts have unique filenames and download_urls."""
        _validate_no_artifact_conflicts(self.artifacts)
        return self


class GenericLockfileV2(BaseModel):
    """Defines format of our generic lockfile, version 2.0."""

    metadata: LockfileMetadataV2
    artifacts: list[LockfileArtifactUrlV2 | LockfileArtifactMavenV2]
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def no_artifact_conflicts(self) -> "GenericLockfileV2":
        """Validate that all artifacts have unique filenames and download_urls."""
        _validate_no_artifact_conflicts(self.artifacts)
        return self
