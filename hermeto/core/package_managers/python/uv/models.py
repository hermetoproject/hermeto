# SPDX-License-Identifier: GPL-3.0-only
from collections.abc import Mapping
from functools import cached_property
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import ParseResult, urlparse

import pydantic
import pypi_simple
import tomlkit
from packageurl import PackageURL
from tomlkit.exceptions import ParseError
from typing_extensions import assert_never

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import (
    InvalidLockfileFormat,
    LockfileNotFound,
    MissingChecksum,
    PackageRejected,
    PathOutsideRoot,
    UnexpectedFormat,
)
from hermeto.core.rooted_path import RootedPath

SUPPORTED_LOCKFILE_VERSION = 1

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

    def purl(self, name: str, version: str | None, **kwargs: Any) -> str:  # noqa: ARG002
        """Get the purl for a registry-sourced package. Mirrors pip's PyPIPackage._make_purl."""
        qualifiers = None
        if self.location.rstrip("/") != pypi_simple.PYPI_SIMPLE_ENDPOINT.rstrip("/"):
            qualifiers = {"repository_url": self.location}
        return PackageURL(
            type="pypi", name=name, version=version, qualifiers=qualifiers
        ).to_string()

    def records_no_checksum(self, artifacts: "list[PackageArtifact]") -> bool:
        """Whether the artifacts fetched from the index carry no hash.

        Registry hashes are optional in uv.lock: uv records one only when the
        index published it.
        """
        return not artifacts or any(artifact.hash is None for artifact in artifacts)


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

    def purl(self, name: str, version: str | None, **kwargs: Any) -> str:  # noqa: ARG002
        """Get the purl for a git-sourced package."""
        return PackageURL(
            type="pypi",
            name=name,
            version=version,
            qualifiers={"vcs_url": f"git+{self.clone_url}@{self.commit}"},
        ).to_string()

    def records_no_checksum(self, artifacts: "list[PackageArtifact]") -> bool:  # noqa: ARG002
        """Whether uv.lock pins no checksum for the clone; it never does."""
        return True


class PackageSourceUrl(pydantic.BaseModel):
    """A package downloaded from a direct URL.

    ``location`` is the URL of the one file the package resolves to.
    """

    kind: Literal["url"]
    location: str

    def purl(
        self,
        name: str,
        version: str | None,
        checksum: str | None,
        **kwargs: Any,  # noqa: ARG002
    ) -> str:
        """Get the purl for a url-sourced package.

        The download URL is on the source itself; only the checksum comes from
        the one artifact uv records for it.
        """
        if checksum is None:
            # should not happen: artifacts_to_download already rejects a url-kind
            # artifact with no hash
            raise RuntimeError(f"artifact for {name}=={version} has no hash")
        return PackageURL(
            type="pypi",
            name=name,
            version=version,
            qualifiers={"download_url": self.location, "checksum": checksum},
        ).to_string()

    def records_no_checksum(self, artifacts: "list[PackageArtifact]") -> bool:
        """Whether the single artifact recorded for the URL carries no hash.

        uv requires one, and ``artifacts_to_download`` rejects a lockfile that
        lacks it; the check stays so a missing hash can never go unmarked.
        """
        return not artifacts or any(artifact.hash is None for artifact in artifacts)


class PackageSourceLocal(pydantic.BaseModel):
    """A package whose files are already in the project tree.

    All four kinds share one model because nothing is fetched for any of them;
    ``kind`` is kept for the error messages that name the exact source key.
    """

    kind: LocalSourceKind
    location: str

    def purl(
        self,
        name: str,
        version: str | None,
        package_dir: RootedPath,
        project_vcs_qualifiers: dict[str, str] | None,
        **kwargs: Any,  # noqa: ARG002
    ) -> str:
        """Get the purl for a dependency whose files already live in the repo tree.

        Covers the path/directory/editable/virtual sources. None of these are
        fetched, so their purl points at the containing repo (like the main
        project's purl) plus a subpath to where the dependency itself sits.

        :raises PackageRejected: if the source escapes the repository root.
        """
        try:
            dep_dir = package_dir.join_within_root(self.location)
        except PathOutsideRoot as e:
            raise PackageRejected(
                reason=(
                    f"{name}'s {self.kind} source "
                    f"{self.location!r} in uv.lock escapes the repository root"
                ),
                solution=(
                    "Hermeto can only fetch dependencies within the given source repository. "
                    "This lockfile was likely generated against a local dependency or index "
                    "that lives outside this repository on another machine."
                ),
            ) from e

        if dep_dir.subpath_from_root != Path("."):
            subpath = dep_dir.subpath_from_root.as_posix()
        else:
            subpath = None

        return PackageURL(
            type="pypi",
            name=name,
            version=version,
            qualifiers=project_vcs_qualifiers,
            subpath=subpath,
        ).to_string()

    def records_no_checksum(self, artifacts: "list[PackageArtifact]") -> bool:  # noqa: ARG002
        """Whether anything fetched for this source lacks a checksum; nothing is fetched."""
        return False


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


class UvPackage(pydantic.BaseModel, extra="ignore"):
    """A single ``[[package]]`` entry in uv.lock.

    ``name`` is always present, ``version`` is not: uv omits it for a directory,
    editable or virtual source whose project declares ``dynamic = ["version"]``.

    Dependency edges, ``metadata``, ``optional-dependencies`` and
    ``dev-dependencies`` are present in the file but not modelled, as they are
    not used for fetch and verification.
    """

    name: str
    version: str | None = None
    source: PackageSource = pydantic.Field(discriminator="kind")
    sdist: ArtifactSdist | None = None
    wheels: list[ArtifactWheel] = pydantic.Field(default_factory=list)

    @pydantic.field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, data: Any) -> Any:
        """Rewrite uv's single-key source table into the ``{kind, location}`` shape.

        uv names the kind with the key itself (``{ registry = "..." }``) while the
        union dispatches on a ``kind`` field: the key becomes ``kind``, its value
        becomes ``location``, and the table's other keys (e.g. a git
        ``subdirectory``) are dropped.

        Two inputs pass through instead: a source model built in code, which is not
        a mapping and which the union takes as it stands, and a mapping that already
        has ``kind``. Anything else that is not a mapping is left for pydantic to
        reject with its own error.
        """
        if not isinstance(data, Mapping) or "kind" in data:
            return data
        matched = [key for key in _SOURCE_KINDS if key in data]
        if len(matched) != 1:
            raise ValueError(f"source must contain exactly one of {_SOURCE_KINDS}, got {matched}")
        kind = matched[0]
        return {"kind": kind, "location": data[kind]}

    @property
    def sole_artifact(self) -> PackageArtifact | None:
        """The single distribution a url or path package records, or None if it records none."""
        if self.sdist is not None:
            return self.sdist
        return self.wheels[0] if self.wheels else None

    @cached_property
    def artifacts_to_download(self) -> list[PackageArtifact]:
        """Extract the remote artifacts to fetch for a package.

        Only sdists are fetched for now. Like pip's process_package_distributions,
        this is the single place where binary filters will decide the sdist/wheel
        split once they are supported. Local sources need no fetching and git
        sources are cloned rather than downloaded, so both yield nothing here.

        Cached so that the download phase and the SBOM both describe the same
        artifacts rather than deriving them separately.

        :raises PackageRejected: if a registry package publishes no sdist.
        :raises UnexpectedFormat: if a registry sdist records no download URL.
        :raises MissingChecksum: if a url package records no hash.
        """
        match self.source:
            case PackageSourceRegistry():
                # registry checksums are optional in uv.lock; a missing hash is
                # tolerated here and reported by the download phase
                if self.sdist is None:
                    # wheel-only packages lock fine and pass `uv lock --check`, but
                    # cannot be built from source under UV_NO_BINARY=true
                    raise PackageRejected(
                        reason=(
                            f"{self.name}=={self.version} has no sdist in uv.lock; "
                            "the package likely only publishes wheels"
                        ),
                    )
                if self.sdist.url is None:
                    # uv always records a URL for registry sdists; `uv lock --check`
                    # does not catch its absence, but `uv sync` would fail on it
                    raise UnexpectedFormat(
                        f"registry sdist for {self.name}=={self.version} has no URL in uv.lock",
                        solution="The lockfile looks corrupted. Regenerate it with `uv lock`.",
                    )
                return [self.sdist]
            case PackageSourceUrl():
                # a url source points at a single file, so uv records exactly one
                # distribution for it: the sdist, or a single wheel. Its hash is
                # mandatory, but the download URL lives only in the source itself.
                recorded = self.sole_artifact
                if recorded is None or recorded.hash is None:
                    raise MissingChecksum(
                        f"{self.name}=={self.version}",
                        solution=(
                            "uv requires a hash for URL dependencies, so this lockfile looks "
                            "corrupted. Regenerate it with `uv lock`."
                        ),
                    )
                # copying rather than rebuilding keeps the sdist/wheel type, which the
                # SBOM reads back to tell a source build from a binary one
                return [recorded.model_copy(update={"url": self.source.location})]
            case PackageSourceGit() | PackageSourceLocal():
                # a git source is cloned instead, and the rest are already in the tree
                return []
            case _:
                assert_never(self.source)

    def purl(self, package_dir: RootedPath, project_vcs_qualifiers: dict[str, str] | None) -> str:
        """Get the purl recording where this package came from.

        Each source kind builds its own; this only hands over what is not in the
        source itself. ``package_dir`` and the project's vcs qualifiers identify
        the repository a local dependency sits in, which uv.lock never records.

        :raises PackageRejected: if a local source escapes the repository root.
        """
        artifact = self.sole_artifact
        return self.source.purl(
            self.name,
            self.version,
            checksum=artifact.hash if artifact is not None else None,
            package_dir=package_dir,
            project_vcs_qualifiers=project_vcs_qualifiers,
        )

    @property
    def records_no_checksum(self) -> bool:
        """Whether uv.lock gives no checksum to verify this package against.

        What is fetched differs per source kind, so each one answers for itself.
        """
        return self.source.records_no_checksum(self.artifacts_to_download)


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
