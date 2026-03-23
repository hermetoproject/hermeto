# SPDX-License-Identifier: GPL-3.0-only
import os
import re
from abc import ABC, abstractmethod
from collections import Counter
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal
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

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import PackageManagerError
from hermeto.core.models.sbom import Component, ExternalReference
from hermeto.core.rooted_path import RootedPath

CHECKSUM_FORMAT = re.compile(r"^[a-zA-Z0-9]+:[a-zA-Z0-9]+$")
ENV_VAR_PATTERN = re.compile(
    r"\$(?:(?P<bare>[A-Za-z_][A-Za-z0-9_]*)"
    r"|\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\})"
)

# Auth types supported by the generic backend. Update this list when adding
# new auth types so that error messages stay accurate.
_SUPPORTED_AUTH_TYPES = ["bearer"]


def _get_var_name(match: re.Match) -> str:
    """Extract the variable name from a regex match of ENV_VAR_PATTERN.

    Handles both ``$VAR`` and ``${VAR}`` syntax. Any ``$`` not followed by a
    valid identifier or braced identifier is silently ignored by the pattern.
    """
    return match.group("bare") or match.group("braced")


def resolve_env_var(value: str) -> str:
    """Resolve environment variable placeholders in a string.

    Replaces shell-like ``$VAR`` and ``${VAR}`` references with the
    corresponding environment variable values. Any ``$`` not followed
    by a valid identifier or braced identifier is left as-is.

    :param value: the string that may contain ``$VAR`` or ``${VAR}`` placeholders
    :return: the string with all placeholders resolved
    :raises PackageManagerError: if any referenced environment variable is not set
    """
    matches = list(ENV_VAR_PATTERN.finditer(value))
    if not matches:
        return value

    missing_vars = sorted({_get_var_name(m) for m in matches if _get_var_name(m) not in os.environ})
    if missing_vars:
        raise PackageManagerError(
            f"Required environment variable(s) not set: {', '.join(missing_vars)}"
        )

    return ENV_VAR_PATTERN.sub(lambda m: os.environ[_get_var_name(m)], value)


def _resolve_auth_headers(auth: dict | None) -> dict[str, str]:
    """Resolve authentication headers from a raw auth config dict.

    Reads the auth type from the top-level key and resolves the corresponding
    headers. The ``url`` and ``method`` parameters are currently unused but
    are accepted for forward compatibility with signing schemes such as AWS
    SigV4 that require them to compute a per-request signature.

    :param auth: the raw auth dict from the lockfile, or ``None``
    :param url: the URL being requested, used by signing schemes (e.g. AWS SigV4)
    :param method: the HTTP method being used, used by signing schemes
    :return: a dict mapping HTTP header names to their resolved values,
             or an empty dict if ``auth`` is ``None``
    :raises PackageManagerError: if the auth type is unrecognised, required
        fields are missing, or referenced environment variables are not set
    """
    if auth is None:
        return {}

    if len(auth) != 1:
        raise PackageManagerError(
            f"Exactly one auth type must be specified "
            f"(one of: {_SUPPORTED_AUTH_TYPES}), got: {list(auth) or 'none'}"
        )

    auth_type, config = next(iter(auth.items()))

    if auth_type == "bearer":
        header = config.get("header", "Authorization")
        value = config.get("value")
        if not value:
            raise PackageManagerError("Bearer auth requires a 'value' field")
        return {header: resolve_env_var(value)}

    raise PackageManagerError(
        f"Unrecognised auth type '{auth_type}'. Valid types are: {_SUPPORTED_AUTH_TYPES}"
    )


class LockfileMetadata(BaseModel):
    """Defines format of the metadata section in the lockfile."""

    version: Literal["1.0", "2.0"]
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
    :param auth: Optional authentication configuration for this artifact.
        The top-level key identifies the auth type (e.g. ``bearer``); the
        value is a dict of fields specific to that type.
    """

    download_url: Annotated[AnyUrl, PlainSerializer(str, return_type=str)]
    auth: dict | None = None

    def resolve_filename(self) -> str:
        """Resolve the filename of the artifact."""
        if not self.filename:
            url_path = urlparse(str(self.download_url)).path
            return Path(url_path).name
        return self.filename

    def resolve_auth_headers(self) -> dict[str, str]:
        """Resolve the authentication headers for this artifact.

        Delegates to :func:`resolve_auth_headers`, passing through the URL
        and method for auth schemes that require them (e.g. AWS SigV4).
        Returns an empty dict if no auth is configured.

        :return: a dict mapping HTTP header names to their resolved values,
                 or an empty dict if no auth is configured
        :raises PackageManagerError: if a referenced environment variable is not set
        """
        return _resolve_auth_headers(self.auth)

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
    """Defines format of our generic lockfile, versions 1.0 and 2.0."""

    metadata: LockfileMetadata
    artifacts: list[LockfileArtifactUrl | LockfileArtifactMaven]
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

    @model_validator(mode="after")
    def no_auth_in_v1(self) -> "GenericLockfileV1":
        """Validate that auth is not used in v1.0 lockfiles."""
        if self.metadata.version == "1.0":
            for artifact in self.artifacts:
                if isinstance(artifact, LockfileArtifactUrl) and artifact.auth is not None:
                    raise ValueError(
                        "The 'auth' field is not supported in lockfile version 1.0. "
                        "Please upgrade to version 2.0 to use authentication."
                    )
        return self
