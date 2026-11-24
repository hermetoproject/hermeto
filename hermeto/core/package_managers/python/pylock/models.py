# SPDX-License-Identifier: GPL-3.0-only
"""Models for parsing PEP 751 ``pylock.toml`` lockfiles.

The models mirror the `pylock.toml specification
<https://packaging.python.org/en/latest/specifications/pylock-toml/>`_. Only the
fields Hermeto acts on are declared; unknown fields are ignored so newer minor
revisions of the format keep parsing.
"""

import logging
import re
from typing import Annotated, Any, Literal, TypeAlias
from urllib import parse as urlparse

import pydantic
import tomlkit
import tomlkit.exceptions
from packaging.version import Version
from typing_extensions import Self

from hermeto.core.errors import InvalidInput, InvalidLockfileFormat, PackageRejected
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

SUPPORTED_LOCK_VERSION_MAJOR = 1

GIT_REF = re.compile(r"^[a-fA-F0-9]{40}$")

URL_SCHEMES = ("http", "https", "ftp")
VCS_SCHEMES = ("git", "git+ftp", "git+http", "git+https")
ALLOWED_SCHEMES = VCS_SCHEMES + URL_SCHEMES

Algorithm: TypeAlias = str
HashValue: TypeAlias = str
Hashes: TypeAlias = dict[Algorithm, HashValue]

LockVersion = Annotated[Version, pydantic.PlainValidator(Version)]


class RemoteSourceUrl(pydantic.AnyUrl):
    """A URL that can be fetched from a remote source."""

    _constraints = pydantic.UrlConstraints(allowed_schemes=list(ALLOWED_SCHEMES + ("ssh",)))


def _index_url_must_not_contain_credentials(url: pydantic.AnyUrl) -> pydantic.AnyUrl:
    if url.username or url.password:
        raise ValueError("must not contain embedded credentials")
    return url


class IndexUrl(
    pydantic.RootModel[
        Annotated[
            pydantic.AnyUrl,
            pydantic.UrlConstraints(allowed_schemes=["http", "https"]),
            pydantic.AfterValidator(_index_url_must_not_contain_credentials),
        ]
    ]
):
    """A PyPI simple-index URL: HTTP(S) only, no embedded credentials."""

    def __str__(self) -> str:
        return str(self.root)

    @classmethod
    def from_config(cls, url: str, source: str) -> Self:
        """Parse an index URL from a configuration source.

        :param url: the index URL to validate
        :param source: human-readable origin for error messages (e.g. "--index-url",
            "PIP_INDEX_URL")
        :raises InvalidInput: if the URL is invalid
        """
        try:
            return cls.model_validate(url)
        except pydantic.ValidationError as exc:
            err_type = exc.errors()[0]["type"] if exc.errors() else ""
            solution = f"Set {source} to a valid HTTP(S) URL (e.g., https://pypi.org/simple/)"
            if err_type == "url_scheme":
                parsed = urlparse.urlparse(url)
                raise InvalidInput(
                    f"{source} must use http or https scheme, got: {parsed.scheme or 'none'}",
                    solution=solution,
                ) from None
            if err_type == "value_error":
                raise InvalidInput(
                    f"{source} must not contain embedded credentials",
                    solution="Use a .netrc file for authentication instead of embedding "
                    "credentials in the URL",
                ) from None
            raise InvalidInput(
                f"{source} must include a host",
                solution=solution,
            ) from None


class RemoteSource(pydantic.BaseModel):
    """A source fetched from a remote URL.

    Local sources (a ``path`` key or a ``file://`` URL) are rejected here because
    Hermeto downloads only what a lockfile declares as remotely fetchable.
    """

    url: RemoteSourceUrl

    # `path` could be present in a lockfile, but from Hermeto's perspective it should not.
    # It is typed to reflect this potential, however in practice it will always remain None.
    path: str | None = None

    @pydantic.field_validator("path", mode="before")
    @classmethod
    def _reject_local_path(cls, value: Any) -> Any:
        raise PackageRejected(
            f"pylock.toml declares a local path source ({value!r}), which Hermeto cannot fetch.",
            solution="Regenerate the lockfile so every dependency resolves to a remote URL.",
        )


class RemoteArtifact(RemoteSource):
    """A downloadable artifact (archive, sdist, or wheel), verified by its hashes."""

    hashes: Hashes = pydantic.Field(default_factory=Hashes, validate_default=True)

    @pydantic.field_validator("hashes")
    @classmethod
    def _require_hashes(cls, hashes: Hashes) -> Hashes:
        if not hashes:
            raise PackageRejected(
                "pylock.toml declares an artifact without hashes, which Hermeto cannot verify.",
                solution=(
                    "Regenerate the lockfile; PEP 751 requires hashes for archive, sdist, "
                    "and wheel files."
                ),
            )
        return hashes


class SdistArtifact(RemoteArtifact):
    """The source distribution of an index package."""


class WheelArtifact(RemoteArtifact):
    """A built wheel of an index package."""


class ArchiveArtifact(RemoteArtifact):
    """A dependency published as a direct-URL archive."""


class VCSSource(RemoteSource):
    """A dependency pinned to a commit in a version-control repository."""

    type: Literal["git"]
    commit_id: str = pydantic.Field(alias="commit-id", pattern=GIT_REF)


class DirectorySource(pydantic.BaseModel):
    """A dependency whose source tree is a local directory."""

    path: str


class PylockPackage(pydantic.BaseModel):
    """A single ``[[packages]]`` entry in a pylock.toml lockfile."""

    name: str
    version: str | None = None
    index: IndexUrl | None = None
    vcs: VCSSource | None = None
    archive: ArchiveArtifact | None = None
    directory: DirectorySource | None = None
    sdist: SdistArtifact | None = None
    wheels: list[WheelArtifact] | None = None

    def check_has_source_defined(self, present: list[str]) -> None:
        """Reject packages that declare no fetchable source."""
        if not present:
            raise PackageRejected(
                f"Package '{self.name}' declares no source.",
                solution="Regenerate the lockfile so every package declares a source.",
            )

    def check_source_is_unique(self, present: list[str]) -> None:
        """Reject packages that declare more than one source kind."""
        if len(present) > 1:
            raise PackageRejected(
                f"Package '{self.name}' declares conflicting sources: {', '.join(present)}.",
                solution="Each package must declare exactly one source.",
            )

    def check_index_source_has_version(self, present: list[str]) -> None:
        """Reject index packages that omit a recorded version."""
        if present == ["index"] and self.version is None:
            raise PackageRejected(
                f"Index package '{self.name}' does not record a version.",
                solution="Regenerate the lockfile so index packages record their version.",
            )

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
            self.check_source_is_unique(["directory", *present])
            return self

        self.check_source_is_unique(present)
        self.check_has_source_defined(present)
        self.check_index_source_has_version(present)
        return self

    @property
    def kind(self) -> Literal["vcs", "archive", "index", "directory"]:
        """Return which kind of source this package is fetched from.

        ``directory`` is a local source tree; the backend skips the package being
        processed and rejects every other local directory.
        """
        if self.vcs is not None:
            return "vcs"
        if self.archive is not None:
            return "archive"
        if self.sdist is not None or self.wheels is not None:
            return "index"
        return "directory"

    @property
    def wheel_artifacts(self) -> list[WheelArtifact]:
        """Return the wheel artifacts for this package."""
        return self.wheels or []


class Pylock(pydantic.BaseModel):
    """A parsed PEP 751 ``pylock.toml`` lockfile."""

    lock_version: LockVersion = pydantic.Field(alias="lock-version")
    # Optional because generators may omit it.
    created_by: str | None = pydantic.Field(default=None, alias="created-by")
    packages: list[PylockPackage] = []
    _document: tomlkit.TOMLDocument | None = pydantic.PrivateAttr(default=None)

    @property
    def document(self) -> tomlkit.TOMLDocument:
        """The source TOML document, retained for round-trip rewriting."""
        if self._document is None:
            raise RuntimeError("document is only available on a Pylock loaded via from_file")
        return self._document

    @pydantic.field_validator("lock_version")
    @classmethod
    def _validate_lock_version(cls, version: Version) -> Version:
        if version.major != SUPPORTED_LOCK_VERSION_MAJOR:
            raise PackageRejected(
                f"Unsupported pylock.toml lock-version '{version}'; Hermeto supports "
                f"{SUPPORTED_LOCK_VERSION_MAJOR}.x lockfiles.",
                solution="Regenerate the lockfile with a compatible tool or upgrade Hermeto.",
            )
        if version > Version("1.0"):
            log.warning(
                "pylock.toml lock-version %s is newer than 1.0; parsing with Hermeto's 1.0 "
                "support and ignoring any unrecognized fields.",
                version,
            )
        return version

    @classmethod
    def from_file(cls, path: RootedPath) -> "Pylock":
        """Parse and validate a pylock.toml file at ``path``."""
        try:
            document = tomlkit.parse(path.path.read_text())
        except tomlkit.exceptions.ParseError as e:
            raise InvalidLockfileFormat(path.path, f"not valid TOML: {e}")

        try:
            pylock = cls.model_validate(document.unwrap())
        except pydantic.ValidationError as e:
            raise InvalidLockfileFormat(path.path, str(e))

        pylock._document = document
        return pylock
