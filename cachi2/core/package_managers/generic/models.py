import re
from abc import ABC, abstractmethod
from collections import Counter
from functools import cached_property
from pathlib import Path
from typing import Literal, Union
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
from typing_extensions import Annotated

from cachi2.core.checksum import ChecksumInfo
from cachi2.core.errors import PackageManagerError
from cachi2.core.models.sbom import Component, ExternalReference
from cachi2.core.rooted_path import RootedPath

CHECKSUM_FORMAT = re.compile(r"^[a-zA-Z0-9]+:[a-zA-Z0-9]+$")


class LockfileMetadata(BaseModel):
    """Defines format of the metadata section in the lockfile."""

    version: Literal["1.0"]
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


class GenericLockfileV1(BaseModel):
    """Defines format of the cachi2 generic lockfile, version 1.0."""

    metadata: LockfileMetadata
    artifacts: list[Union[LockfileArtifactUrl, LockfileArtifactMaven]]
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def no_artifact_conflicts(self) -> "GenericLockfileV1":
        """Validate that all artifacts have unique filenames and download_urls."""
        urls = Counter(a.download_url for a in self.artifacts)
        filenames = Counter(a.filename for a in self.artifacts)
        duplicate_urls = [str(u) for u, count in urls.most_common() if count > 1]
        duplicate_filenames = [t for t, count in filenames.most_common() if count > 1]
        if duplicate_urls or duplicate_filenames:
            raise ValueError(
                (f"Duplicate download_urls: {duplicate_urls}\n" if duplicate_urls else "")
                + (f"Duplicate filenames: {duplicate_filenames}" if duplicate_filenames else "")
            )

        return self
