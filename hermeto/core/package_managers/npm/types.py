# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import TypedDict

from hermeto.core.models.output import ProjectFile
from hermeto.core.models.sbom import ExternalReference

DEPENDENCY_TYPES = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)


class NpmComponentInfo(TypedDict):
    """Contains the data needed to generate an npm SBOM component."""

    name: str
    purl: str
    version: str | None
    dev: bool
    bundled: bool
    external_refs: list[ExternalReference] | None
    missing_hash_in_file: Path | None


class ResolvedNpmPackage(TypedDict):
    """Contains all of the data for a resolved npm package."""

    package: NpmComponentInfo
    dependencies: list[NpmComponentInfo]
    projectfiles: list[ProjectFile]
