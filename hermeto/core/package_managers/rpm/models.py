# https://github.com/rpm-software-management/libpkgmanifest/blob/main/schemas/manifest.json

from typing import Literal, NewType, Optional

from pydantic import BaseModel, model_validator


class Repository(BaseModel):
    id: str
    metalink: Optional[str] = None
    baseurl: Optional[str] = None
    mirrorlist: Optional[str] = None

    @model_validator(mode="after")
    def validate_repository(self) -> "Repository":
        if not (self.metalink or self.baseurl or self.mirrorlist):
            raise ValueError(
                "At least one of metalink, baseurl or mirrorlist must be set for a repository."
            )

        return self


Architecture = NewType("Architecture", str)


class Package(BaseModel):
    name: str
    repo_id: str
    checksum: str
    size: int
    evr: str
    srpm: Optional[str] = None
    location: Optional[str] = None
    module: Optional[str] = None
    parent_archs: Optional[list[Architecture]] = None


class Data(BaseModel):
    repositories: list[Repository]
    packages: dict[Architecture, list[Package]]


class RPMManifest(BaseModel):
    document: Literal["rpm-package-manifest"]
    version: str
    data: Data
