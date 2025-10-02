"""Pydantic models for Hugging Face lockfile validation."""

import re
from typing import Literal, Optional

from packageurl import PackageURL
from pydantic import BaseModel, ConfigDict, field_validator

from hermeto.core.models.sbom import Component, ExternalReference

# Git commit hash: 40 hex characters
COMMIT_HASH_PATTERN = re.compile(r"^[a-f0-9]{40}$")


class LockfileMetadata(BaseModel):
    """Metadata section of the lockfile."""

    version: Literal["1.0"]
    model_config = ConfigDict(extra="forbid")


class HuggingFaceModel(BaseModel):
    """
    A single model or dataset entry in the lockfile.

    :param repository: Repository identifier (e.g., "gpt2" or "microsoft/deberta-v3-base")
    :param revision: Git commit hash for the specific version
    :param type: Type of repository - "model" or "dataset"
    :param include_patterns: Optional list of glob patterns to filter files (e.g., ["*.safetensors"])
    """

    repository: str
    revision: str
    type: Literal["model", "dataset"] = "model"
    include_patterns: Optional[list[str]] = None
    model_config = ConfigDict(extra="forbid")

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        """Validate that revision is a valid Git commit hash."""
        if not COMMIT_HASH_PATTERN.match(value):
            raise ValueError(
                f"Revision must be a 40-character Git commit hash, got '{value}'. "
                "You can find the commit hash on the HuggingFace model page or using the huggingface_hub library."
            )
        return value

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        """Validate repository format."""
        if not value or value.strip() != value:
            raise ValueError(
                "Repository name cannot be empty or contain leading/trailing whitespace"
            )
        # Repository can be either "name" or "namespace/name"
        parts = value.split("/")
        if len(parts) > 2:
            raise ValueError(
                f"Repository must be in format 'name' or 'namespace/name', got '{value}'"
            )
        return value

    @property
    def namespace(self) -> str:
        """Extract namespace from repository, or return empty string for default namespace."""
        parts = self.repository.split("/")
        if len(parts) == 2:
            return parts[0]
        return ""

    @property
    def name(self) -> str:
        """Extract name from repository."""
        parts = self.repository.split("/")
        return parts[-1]

    @property
    def purl_name(self) -> str:
        """Get the name component for the PURL."""
        # For PURL, if there's no namespace, use the name directly
        return self.name

    @property
    def purl_namespace(self) -> Optional[str]:
        """Get the namespace component for the PURL, or None if no namespace."""
        namespace = self.namespace
        return namespace if namespace else None

    def get_sbom_component(self, download_url: str) -> Component:
        """
        Generate an SBOM component for this model/dataset.

        :param download_url: The base URL for the repository on HuggingFace
        :return: SBOM Component
        """
        purl_kwargs = {
            "type": "huggingface",
            "name": self.purl_name,
            "version": self.revision.lower(),  # PURL spec requires lowercase version
        }
        if self.purl_namespace:
            purl_kwargs["namespace"] = self.purl_namespace

        purl = PackageURL(**purl_kwargs).to_string()

        return Component(
            name=self.repository,
            version=self.revision,
            purl=purl,
            type="library",
            external_references=[
                ExternalReference(
                    url=download_url,
                    type="distribution",
                )
            ],
        )


class HuggingFaceLockfile(BaseModel):
    """Hugging Face lockfile format."""

    metadata: LockfileMetadata
    models: list[HuggingFaceModel]
    model_config = ConfigDict(extra="forbid")
