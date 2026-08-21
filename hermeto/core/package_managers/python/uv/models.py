# SPDX-License-Identifier: GPL-3.0-only
from functools import cached_property
from typing import Any, Literal, get_args
from urllib.parse import ParseResult, urlparse

import pydantic

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
