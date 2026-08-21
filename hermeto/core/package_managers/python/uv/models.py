# SPDX-License-Identifier: GPL-3.0-only
from functools import cached_property
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import ParseResult, urlparse

import pydantic

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import PackageRejected

PackageSourceKind = Literal[
    "registry",
    "git",
    "url",
    "path",
    "directory",
    "editable",
    "virtual",
]

# Sources that live in the project/workspace tree; they have no remote artifact
LocalSourceKind = Literal["path", "directory", "editable", "virtual"]

_SOURCE_KINDS: tuple[str, ...] = get_args(PackageSourceKind)

# The keys uv locates a wheel by; it writes exactly one of them per wheel
_WHEEL_LOCATIONS: tuple[str, ...] = ("url", "path", "filename")


class PackageSourceRegistry(pydantic.BaseModel):
    """A package resolved from a package index.

    ``location`` is the index URL, or a directory path for a local index.
    """

    kind: Literal["registry"]
    location: str

    @pydantic.model_validator(mode="after")
    def _records_valid_index(self) -> "PackageSourceRegistry":
        """Reject a registry source with an empty index.

        uv always records the index a registry package was resolved from (a URL,
        or a path for local indexes)
        """
        if not self.location:
            raise ValueError(
                "registry source must not be empty, expected an index URL or a local index path"
            )
        return self


class PackageSourceGit(pydantic.BaseModel):
    """A package cloned from a git repository at a pinned commit."""

    kind: Literal["git"]
    location: str

    @cached_property
    def _parsed_url(self) -> ParseResult:
        """The location as a ParseResult."""
        return urlparse(self.location)

    @cached_property
    def clone_url(self) -> str:
        """The clonable URL of a git source.

        uv records git sources as ``https://host/repo?<ref-type>=<ref>#<commit>``;
        the query and fragment are uv metadata, not part of the repository URL.
        """
        return self._parsed_url._replace(query="", fragment="").geturl()

    def model_post_init(self, _: Any) -> None:
        """Validate derived properties, so a source that pins no commit fails at parse time.

        pydantic calls this after __init__ and model_construct, with every field set.
        """
        self.commit

    @cached_property
    def commit(self) -> str:
        """The resolved commit of a git source (recorded in the URL fragment).

        :raises PackageRejected: if the source pins no commit, which uv itself
            never produces - the lockfile must have been altered.
        """
        commit = self._parsed_url.fragment
        if not commit:
            raise PackageRejected(
                reason=f"git source {self.location!r} in uv.lock does not pin a commit",
                solution="The lockfile looks corrupted. Regenerate it with `uv lock`.",
            )
        return commit


class PackageSourceUrl(pydantic.BaseModel):
    """A package downloaded from a direct URL.

    ``location`` is the URL of the one file the package resolves to.
    """

    kind: Literal["url"]
    location: str


class PackageSourceLocal(pydantic.BaseModel):
    """A package whose files are already in the project tree.

    All four kinds share one model because nothing is fetched for any of them;
    ``kind`` is kept for the error messages that name the exact source key.
    """

    kind: LocalSourceKind
    location: str


# uv writes a source as a single-key table; UvPackage._normalize_source rewrites
# that key into ``kind``, which is what tells these four apart.
PackageSource = PackageSourceRegistry | PackageSourceGit | PackageSourceUrl | PackageSourceLocal


class PackageArtifact(pydantic.BaseModel, extra="ignore"):
    """Fields the sdist and the wheels of a uv.lock ``[[package]]`` have in common.

    An artifact resolved from a remote index records an absolute ``url``; ``hash``
    is present only when the index publishes checksums, and ``size`` appears only for
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

    @cached_property
    def checksum_info(self) -> ChecksumInfo | None:
        """Return the recorded checksum as a ChecksumInfo, or None if absent."""
        if self.hash is None:
            return None
        return ChecksumInfo.from_hash(self.hash)

    def get_target_filename(self, source: PackageSource) -> str:
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


class ArtifactSdist(PackageArtifact):
    """The ``sdist`` of a uv.lock ``[[package]]``.

    An sdist is never named by a ``filename`` -- a package has at most one.
    When the package's ``source`` is itself a ``url`` or a ``path``, the sdist
    records only ``hash``: the file's location is already in ``source``, so uv
    does not repeat it here.
    """

    @pydantic.model_validator(mode="after")
    def _require_any_identity(self) -> "ArtifactSdist":
        """Reject an sdist that records no ``url``, ``path`` or ``hash``.

        A registry sdist records ``url`` or ``path``, and carries a hash only if
        the index published one. A url or path sdist is the other way round: the
        hash is mandatory and neither of the other two is written. An sdist with
        none of the three can be neither found nor verified.
        """
        if not any((self.url, self.path, self.hash)):
            raise ValueError("sdist must have at least one of 'url', 'path' or 'hash'")
        return self


class ArtifactWheel(PackageArtifact):
    """One entry of the ``wheels`` array of a uv.lock ``[[package]]``.

    A wheel adds ``filename``, which uv writes when the package is pinned to a
    ``.whl`` on disk: a relative path cannot be stored in ``url``, so it records
    the bare file name and resolves it against ``source``.
    """

    filename: str | None = None

    @pydantic.model_validator(mode="after")
    def _require_one_location(self) -> "ArtifactWheel":
        """Reject a wheel that does not record exactly one of url, path or filename.

        Stricter than uv, which reads the three in the order url, path, filename
        and ignores the rest, so ``uv lock --check`` accepts an entry carrying
        several. Hermeto cannot: ``get_target_filename`` prefers ``filename``, so
        such a wheel would be downloaded from its url, stored under an unrelated
        name, and the rewritten lockfile pointed at that name. Refusing beats
        guessing -- and relaxing this later means following uv's order instead.
        """
        recorded = {key: value for key in _WHEEL_LOCATIONS if (value := getattr(self, key))}
        if len(recorded) != 1:
            raise ValueError(
                f"wheel must contain exactly one of {_WHEEL_LOCATIONS}, got {recorded}"
            )
        return self

    def get_target_filename(self, source: PackageSource) -> str:
        """Use the recorded name; a wheel always records one of the three."""
        return self.filename or super().get_target_filename(source)
