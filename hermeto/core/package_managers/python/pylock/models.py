# SPDX-License-Identifier: GPL-3.0-only
"""Models for parsing PEP 751 ``pylock.toml`` lockfiles.

The models mirror the `pylock.toml specification
<https://packaging.python.org/en/latest/specifications/pylock-toml/>`_ and are
tool-agnostic: any Python backend can consume them. Only the fields Hermeto acts
on are declared; unknown fields are ignored so that newer minor revisions of the
format keep parsing. Sources Hermeto cannot fetch hermetically (local paths and
foreign local directories) are rejected during validation; the root project's own
``directory`` entry (``path = "."``) is accepted so the backend can skip it.
"""

import logging
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import pydantic
import tomlkit
import tomlkit.exceptions
from packaging.version import InvalidVersion, Version
from typing_extensions import Self

from hermeto.core.errors import InvalidLockfileFormat, PackageRejected
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

SUPPORTED_LOCK_VERSION_MAJOR = 1

GIT_REF = re.compile(r"^[a-fA-F0-9]{40}$")


class _RemoteSource(pydantic.BaseModel):
    """A source fetched from a remote URL.

    Local sources (a ``path`` key or a ``file://`` URL) are rejected here because
    Hermeto downloads only what a lockfile declares as remotely fetchable.
    """

    url: str
    path: str | None = None

    @pydantic.field_validator("path", mode="before")
    @classmethod
    def _reject_local_path(cls, value: Any) -> Any:
        raise PackageRejected(
            f"pylock.toml declares a local path source ({value!r}), which Hermeto cannot fetch.",
            solution="Regenerate the lockfile so every dependency resolves to a remote URL.",
        )

    @pydantic.field_validator("url", mode="before")
    @classmethod
    def _reject_local_url(cls, value: str) -> str:
        if urlparse(value).scheme == "file":
            raise PackageRejected(
                f"pylock.toml declares a local file:// URL ({value!r}), which Hermeto cannot fetch.",
                solution="Regenerate the lockfile so every dependency resolves to a remote URL.",
            )
        return value


class _RemoteArtifact(_RemoteSource):
    """A downloadable artifact (archive, sdist, or wheel), verified by its hashes."""

    hashes: dict[str, str] = pydantic.Field(default_factory=dict, validate_default=True)

    @pydantic.field_validator("hashes")
    @classmethod
    def _require_hashes(cls, hashes: dict[str, str]) -> dict[str, str]:
        if not hashes:
            raise PackageRejected(
                "pylock.toml declares an artifact without hashes, which Hermeto cannot verify.",
                solution=(
                    "Regenerate the lockfile; PEP 751 requires hashes for archive, sdist, "
                    "and wheel files."
                ),
            )
        return hashes

    @pydantic.field_validator("url")
    @classmethod
    def _reject_invalid_url(cls, url: str) -> str:
        URL_SCHEMES = {"http", "https", "ftp"}
        if urlparse(url).scheme not in URL_SCHEMES:
            raise PackageRejected(
                f"pylock.toml declares an invalid URL '{url}'; only {', '.join(URL_SCHEMES)} are supported.",
                solution="Regenerate the lockfile so every dependency resolves to a remote URL.",
            )
        return url


class _SdistArtifact(_RemoteArtifact):
    """The source distribution of an index package."""


class _WheelArtifact(_RemoteArtifact):
    """A built wheel of an index package."""


class _ArchiveArtifact(_RemoteArtifact):
    """A dependency published as a direct-URL archive."""


class _VCSSource(_RemoteSource):
    """A dependency pinned to a commit in a version-control repository."""

    type: str
    commit_id: str = pydantic.Field(alias="commit-id")

    @pydantic.field_validator("type")
    @classmethod
    def _only_git(cls, vcs_type: str) -> str:
        if vcs_type != "git":
            raise PackageRejected(
                f"pylock.toml declares an unsupported VCS type '{vcs_type}'; only git is supported.",
                solution="Use a git repository or a released artifact instead.",
            )
        return vcs_type

    @pydantic.field_validator("commit_id")
    @classmethod
    def _reject_invalid_commit_id(cls, commit_id: str) -> str:
        if not GIT_REF.match(commit_id):
            raise PackageRejected(
                f"Git reference '{commit_id}' is invalid; it must be a full 40-character hexadecimal commit hash.",
                solution="Please specify the full commit hash for git URLs.",
            )
        return commit_id

    @pydantic.field_validator("url")
    @classmethod
    def _reject_invalid_vcs_url(cls, url: str) -> str:
        URL_SCHEMES = {"http", "https", "ftp"}
        VCS_SCHEMES = {
            "git",
            "git+ftp",
            "git+http",
            "git+https",
        }
        ALLOWED_SCHEMES = (*VCS_SCHEMES, *URL_SCHEMES)
        if urlparse(url).scheme not in ALLOWED_SCHEMES:
            raise PackageRejected(
                f"pylock.toml declares an invalid VCS URL '{url}'; only {', '.join(ALLOWED_SCHEMES)} are supported.",
                solution="Use a valid VCS URL.",
            )
        return url


class PylockPackage(pydantic.BaseModel):
    """A single ``[[packages]]`` entry in a pylock.toml lockfile."""

    name: str
    version: str | None = None
    index: str | None = None
    vcs: _VCSSource | None = None
    archive: _ArchiveArtifact | None = None
    directory: dict[str, Any] | None = None
    sdist: _SdistArtifact | None = None
    wheels: list[_WheelArtifact] | None = None

    @pydantic.model_validator(mode="after")
    def _validate_single_supported_source(self) -> Self:
        present = []
        if self.vcs is not None:
            present.append("vcs")
        if self.archive is not None:
            present.append("archive")
        if self.sdist is not None or self.wheels is not None:
            present.append("index")

        if self.directory is not None:
            if present:
                raise PackageRejected(
                    f"Package '{self.name}' declares conflicting sources: "
                    f"{', '.join(['directory', *present])}.",
                    solution="Each package must declare exactly one source.",
                )
            # The lockfile's own "." directory is already the main component.
            path = self.directory.get("path")
            if path is None or Path(path) != Path("."):
                raise PackageRejected(
                    f"Package '{self.name}' is a local directory dependency, "
                    "which Hermeto cannot fetch.",
                    solution="Depend on a released version from an index or a URL instead.",
                )
            return self

        if len(present) > 1:
            raise PackageRejected(
                f"Package '{self.name}' declares conflicting sources: {', '.join(present)}.",
                solution="Each package must declare exactly one source.",
            )
        if not present:
            raise PackageRejected(
                f"Package '{self.name}' declares no source.",
                solution="Regenerate the lockfile so every package declares a source.",
            )
        if present == ["index"] and self.version is None:
            raise PackageRejected(
                f"Index package '{self.name}' does not record a version.",
                solution="Regenerate the lockfile so index packages record their version.",
            )
        return self

    @property
    def kind(self) -> Literal["vcs", "archive", "index", "directory"]:
        """Return which kind of source this package is fetched from.

        ``directory`` is the root project's own entry (``path = "."``); it is
        skipped, not fetched. ``metadata`` means the package has no fetchable
        source; it is reported in the SBOM but not downloaded.
        """
        if self.vcs is not None:
            return "vcs"
        if self.archive is not None:
            return "archive"
        if self.sdist is not None or self.wheels is not None:
            return "index"
        return "directory"


class Pylock(pydantic.BaseModel):
    """A parsed PEP 751 ``pylock.toml`` lockfile."""

    lock_version: str = pydantic.Field(alias="lock-version")
    # Optional because generators may omit it.
    created_by: str | None = pydantic.Field(default=None, alias="created-by")
    packages: list[PylockPackage] = []

    @pydantic.field_validator("lock_version")
    @classmethod
    def _validate_lock_version(cls, value: str) -> str:
        try:
            version = Version(value)
        except InvalidVersion:
            raise PackageRejected(
                f"pylock.toml has an invalid lock-version '{value}'.",
                solution="Regenerate the lockfile with a tool that follows PEP 751.",
            )

        if version.major != SUPPORTED_LOCK_VERSION_MAJOR:
            raise PackageRejected(
                f"Unsupported pylock.toml lock-version '{value}'; Hermeto supports "
                f"{SUPPORTED_LOCK_VERSION_MAJOR}.x lockfiles.",
                solution="Regenerate the lockfile with a compatible tool or upgrade Hermeto.",
            )
        if version > Version("1.0"):
            log.warning(
                "pylock.toml lock-version %s is newer than 1.0; parsing with Hermeto's 1.0 "
                "support and ignoring any unrecognized fields.",
                value,
            )
        return value

    @classmethod
    def from_file(cls, path: RootedPath) -> "Pylock":
        """Parse and validate a pylock.toml file at ``path``."""
        try:
            data = tomlkit.parse(path.path.read_text()).unwrap()
        except tomlkit.exceptions.ParseError as e:
            raise InvalidLockfileFormat(path.path, f"not valid TOML: {e}")

        try:
            return cls.model_validate(data)
        except pydantic.ValidationError as e:
            raise InvalidLockfileFormat(path.path, str(e))
