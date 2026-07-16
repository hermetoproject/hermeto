# SPDX-License-Identifier: GPL-3.0-only
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import urlparse

import pydantic
import tomlkit
from tomlkit.exceptions import ParseError

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import (
    InvalidLockfileFormat,
    LockfileNotFound,
    PackageRejected,
)
from hermeto.core.rooted_path import RootedPath

SUPPORTED_LOCKFILE_VERSION = 1

UvSourceKind = Literal[
    "registry",
    "git",
    "url",
    "path",
    "directory",
    "editable",
    "virtual",
]

_SOURCE_KINDS: tuple[str, ...] = get_args(UvSourceKind)

# Sources that live in the project/workspace tree; they have no remote artifact
_LOCAL_KINDS = frozenset({"path", "directory", "editable", "virtual"})

# The keys uv locates a wheel by; it writes exactly one of them per wheel
_WHEEL_LOCATIONS: tuple[str, ...] = ("url", "path", "filename")


class UvSource(pydantic.BaseModel):
    """The single resolved source of a uv.lock ``[[package]]``."""

    kind: UvSourceKind
    location: str

    @pydantic.model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        """Rewrite uv's single-key source (e.g. ``{"registry": ...}``) to ``{kind, location}``.

        Already-normalized input (``kind`` present) passes through; extra keys
        (e.g. a git ``subdirectory``) are ignored.
        """
        if not isinstance(data, Mapping) or "kind" in data:
            return data
        matched = [key for key in _SOURCE_KINDS if key in data]
        if len(matched) != 1:
            raise ValueError(
                f"source must contain exactly one of {_SOURCE_KINDS}, got {sorted(data)}"
            )
        kind = matched[0]
        return {"kind": kind, "location": data[kind]}

    @pydantic.model_validator(mode="after")
    def _registry_records_valid_index(self) -> "UvSource":
        """Reject a registry source with an empty index.

        uv always records the index a registry package was resolved from (a URL,
        or a path for local indexes)
        """
        if self.kind == "registry" and not self.location:
            raise ValueError(
                "registry source must not be empty, expected an index URL or a local index path"
            )
        return self

    @property
    def is_local(self) -> bool:
        """Whether the source lives in the project tree and so is not fetched."""
        return self.kind in _LOCAL_KINDS

    @property
    def git_clone_url(self) -> str:
        """The clonable URL of a git source.

        uv records git sources as ``https://host/repo?<ref-type>=<ref>#<commit>``;
        the query and fragment are uv metadata, not part of the repository URL.
        """
        return urlparse(self.location)._replace(query="", fragment="").geturl()

    def get_git_commit(self) -> str:
        """Return the resolved commit of a git source (recorded in the URL fragment).

        :raises PackageRejected: if the source pins no commit, which uv itself
            never produces - the lockfile must have been altered.
        """
        commit = urlparse(self.location).fragment
        if not commit:
            raise PackageRejected(
                reason=f"git source {self.location!r} in uv.lock does not pin a commit",
                solution="The lockfile looks corrupted. Regenerate it with `uv lock`.",
            )
        return commit


class UvArtifact(pydantic.BaseModel, extra="ignore"):
    """Fields the sdist and the wheels of a uv.lock ``[[package]]`` have in common.

    An artifact resolved from a remote index records an absolute ``url``; ``hash``
    follows only when the index publishes checksums, and ``size`` appears only for
    registry artifacts. ``path`` is parsed but never acted on: uv writes it for an
    artifact that already sits on disk, which hermeto does not resolve yet, and
    modelling the field keeps such a lockfile readable instead of rejected as
    malformed.

    Two different things in uv.lock are called ``path``. This one, on the sdist
    or a wheel, locates that single file. The other, ``source = { path = "..." }``,
    says the whole package comes from an archive file on disk.
    """

    url: str | None = None
    path: str | None = None
    hash: str | None = None
    size: int | None = None

    @property
    def checksum_info(self) -> ChecksumInfo | None:
        """Return the recorded checksum as a ChecksumInfo, or None if absent."""
        if self.hash is None:
            return None
        return ChecksumInfo.from_hash(self.hash)

    def get_target_filename(self, source: UvSource) -> str:
        """Get the name this artifact's file has in deps/uv.

        Downloads are saved under it, and the lockfile rewrite redirects the
        artifact's URL to ``deps/uv/<that name>``. Both go through this, so the
        rewritten URL always resolves to the file that was downloaded.

        For a url or path package the sdist is only ``{ hash = "..." }``, with no
        name in it anywhere -- the name is in ``source``, which is also where uv
        itself looks for the file.

        :raises PackageRejected: if no file name can be found in either.
        """
        location = self.url or self.path or source.location
        filename = Path(urlparse(location).path).name
        if not filename:
            raise PackageRejected(
                reason=f"Cannot determine a file name for {location!r} in uv.lock",
                solution="Regenerate the lockfile with `uv lock`.",
            )
        return filename


class UvSdist(UvArtifact):
    """The ``sdist`` of a uv.lock ``[[package]]``.

    An sdist is never named by a ``filename``. A url or path package records
    neither ``url`` nor ``path`` either -- just ``{ hash = "..." }`` -- because
    the file it refers to is already spelled out in ``source``.
    """

    @pydantic.model_validator(mode="after")
    def _require_any_identity(self) -> "UvSdist":
        """Reject an sdist that records no ``url``, ``path`` or ``hash``.

        A registry sdist records ``url`` or ``path``, and carries a hash only if
        the index published one. A url or path sdist is the other way round: the
        hash is mandatory and neither of the other two is written. An sdist with
        none of the three can be neither found nor verified.
        """
        if not any((self.url, self.path, self.hash)):
            raise ValueError("sdist must have at least one of 'url', 'path' or 'hash'")
        return self


class UvWheel(UvArtifact):
    """One entry of the ``wheels`` array of a uv.lock ``[[package]]``.

    A wheel adds ``filename``, which uv writes when the package is pinned to a
    ``.whl`` on disk: a relative path cannot be stored in ``url``, so it records
    the bare file name and resolves it against ``source``.
    """

    filename: str | None = None

    @pydantic.model_validator(mode="after")
    def _require_one_location(self) -> "UvWheel":
        """Reject a wheel that does not record exactly one of url, path or filename.

        Stricter than uv, which reads the three in the order url, path, filename
        and ignores the rest, so ``uv lock --check`` accepts an entry carrying
        several. Hermeto cannot: ``get_target_filename`` prefers ``filename``, so
        such a wheel would be downloaded from its url, stored under an unrelated
        name, and the rewritten lockfile pointed at that name. Refusing beats
        guessing -- and relaxing this later means following uv's order instead.
        """
        recorded = [key for key in _WHEEL_LOCATIONS if getattr(self, key)]
        if len(recorded) != 1:
            raise ValueError(
                f"wheel must contain exactly one of {_WHEEL_LOCATIONS}, got {recorded}"
            )
        return self

    def get_target_filename(self, source: UvSource) -> str:
        """Use the recorded name; a wheel always records one of the three."""
        return self.filename or super().get_target_filename(source)


class UvPackage(pydantic.BaseModel, extra="ignore"):
    """A single ``[[package]]`` entry in uv.lock.

    ``name`` is always present, ``version`` is not: uv omits it for a directory,
    editable or virtual source whose project declares ``dynamic = ["version"]``.

    Dependency edges, ``metadata``, ``optional-dependencies`` and
    ``dev-dependencies`` are present in the file but not modelled, as they are
    unused for fetch and verification.
    """

    name: str
    version: str | None = None
    source: UvSource
    sdist: UvSdist | None = None
    wheels: list[UvWheel] = pydantic.Field(default_factory=list)

    @property
    def sole_artifact(self) -> UvArtifact | None:
        """The single distribution a url or path package records, or None if it records none."""
        if self.sdist is not None:
            return self.sdist
        return self.wheels[0] if self.wheels else None


def load_lockfile_document(directory: RootedPath) -> tomlkit.TOMLDocument:
    """Find and load the raw uv.lock document from a directory.

    The raw document complements the deliberately lossy UvLock model: the
    lockfile rewrite edits it in place to preserve fields and formatting the
    model does not carry.

    :raises LockfileNotFound: if no uv.lock file is found in the directory.
    :raises InvalidLockfileFormat: if the file is not valid TOML.
    """
    path = directory.join_within_root("uv.lock")
    if not path.path.exists():
        raise LockfileNotFound(
            files=path.path,
            solution="Run `uv lock` in the project directory to generate uv.lock.",
        )

    with open(path) as f:
        try:
            return tomlkit.load(f)
        except ParseError as e:
            raise InvalidLockfileFormat(
                lockfile_path=path.path,
                err_details="Invalid TOML syntax.",
                solution="Regenerate the lockfile with `uv lock`.",
            ) from e


class UvLock(pydantic.BaseModel, extra="ignore", populate_by_name=True):
    """A parsed uv.lock file.

    Only ``version`` and ``packages`` are modelled. ``revision`` is ignored
    because revisions are forward/backward compatible within a major version;
    ``requires-python``, ``resolution-markers``, ``manifest`` and ``options``
    are ignored as they are not needed to fetch artifacts.
    """

    version: int
    # uv.lock spells this ``package``; expose it as ``packages`` here.
    packages: list[UvPackage] = pydantic.Field(default_factory=list, alias="package")

    @pydantic.field_validator("version")
    @classmethod
    def _supported_version(cls, version: int) -> int:
        if version != SUPPORTED_LOCKFILE_VERSION:
            raise ValueError(
                f"unsupported uv.lock version: {version} "
                f"(only version {SUPPORTED_LOCKFILE_VERSION} is supported)"
            )
        return version

    @classmethod
    def from_toml(cls, doc: tomlkit.TOMLDocument, lockfile_path: Path) -> "UvLock":
        """Validate an already-loaded uv.lock document.

        :raises InvalidLockfileFormat: if the document does not match the
            expected uv.lock structure/version.
        """
        try:
            return cls.model_validate(doc)
        except pydantic.ValidationError as e:
            first = e.errors()[0]
            raise InvalidLockfileFormat(
                lockfile_path=lockfile_path,
                err_details=f"{'.'.join(map(str, first['loc']))}: {first['msg']}",
                solution="Regenerate the lockfile with `uv lock` (matching your uv version).",
            ) from e

    @classmethod
    def from_file(cls, directory: RootedPath) -> "UvLock":
        """Find, load, parse and validate a uv.lock file from a directory.

        :raises InvalidLockfileFormat: if the file is not valid TOML, or does not
            match the expected uv.lock structure/version.
        :raises LockfileNotFound: if no uv.lock file is found in the directory.
        """
        path = directory.join_within_root("uv.lock")
        return cls.from_toml(load_lockfile_document(directory), path.path)
