"""Pydantic models for DVC lockfile validation."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DVCDep(BaseModel):
    """
    A DVC dependency entry.

    :param path: Path or URL to the dependency
    :param md5: MD5 checksum of the dependency
    :param size: Size in bytes
    :param hash: Hash algorithm name (alternative to md5)
    """

    path: str
    md5: Optional[str] = None
    size: Optional[int] = None
    hash: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @property
    def checksum_algorithm(self) -> Optional[str]:
        """Get the checksum algorithm name."""
        if self.hash:
            return self.hash
        if self.md5:
            return "md5"
        return None

    @property
    def checksum_value(self) -> Optional[str]:
        """Get the checksum value."""
        return self.md5

    @property
    def is_external_url(self) -> bool:
        """Check if this dependency is an external URL."""
        return self.path.startswith(("http://", "https://", "s3://", "gs://", "azure://"))


class DVCOut(BaseModel):
    """
    A DVC output entry.

    :param path: Path to the output file
    :param md5: MD5 checksum of the output
    :param size: Size in bytes
    :param hash: Hash algorithm name (alternative to md5)
    """

    path: str
    md5: Optional[str] = None
    size: Optional[int] = None
    hash: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class DVCStage(BaseModel):
    """
    A DVC pipeline stage.

    :param cmd: Command executed (optional, we don't use it)
    :param deps: List of dependencies
    :param outs: List of outputs
    """

    cmd: Optional[str] = None
    deps: Optional[list[DVCDep]] = None
    outs: Optional[list[DVCOut]] = None

    model_config = ConfigDict(extra="allow")


class DVCLockfile(BaseModel):
    """
    Root DVC lockfile structure (dvc.lock).

    :param schema_: DVC schema version (should be 2.0+)
    :param stages: Dictionary of stage name to stage definition
    """

    schema_: str = Field(alias="schema")
    stages: dict[str, DVCStage] = Field(default_factory=dict)

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )

    @field_validator("schema_")
    @classmethod
    def validate_schema(cls, value: str) -> str:
        """Validate DVC schema version is supported."""
        if not value.startswith("2."):
            raise ValueError(
                f"Unsupported DVC schema version: {value}. "
                "Only schema version 2.0+ is supported."
            )
        return value

    def get_all_external_deps(self) -> list[tuple[str, DVCDep]]:
        """
        Get all external URL dependencies across all stages.

        :return: List of (stage_name, dep) tuples for external dependencies
        """
        external_deps = []
        for stage_name, stage in self.stages.items():
            if not stage.deps:
                continue
            for dep in stage.deps:
                if dep.is_external_url:
                    external_deps.append((stage_name, dep))
        return external_deps
