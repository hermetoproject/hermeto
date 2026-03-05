# SPDX-License-Identifier: GPL-3.0-only
"""Common utilities shared between JavaScript package managers (npm, yarn)."""

import re
from functools import cached_property
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import (
    AnyUrl,
    BaseModel,
    TypeAdapter,
    UrlConstraints,
    ValidationError,
    field_validator,
)

from hermeto.core.errors import UnexpectedFormat
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball

_SCHEME_URL = Annotated[
    AnyUrl,
    UrlConstraints(allowed_schemes=["http", "https", "git", "ssh"]),
]
_SCHEME_URL_ADAPTER = TypeAdapter(_SCHEME_URL)
_SCP_URL_RE = re.compile(r"^([^/@]+@)?[^/:]+:[^/].*$")


class GitCloneUrl(BaseModel):
    """A git clone URL (scheme-based or SCP-style) with an optional ref."""

    url: str
    ref: str = ""

    def __init__(self, url: str, ref: str = "") -> None:
        """Allow ``GitCloneUrl(url, ref)`` positional construction."""
        try:
            super().__init__(url=url, ref=ref)
        except ValidationError as e:
            raise UnexpectedFormat(
                f"Cannot parse git URL: {url}",
                solution="Ensure the git dependency has a valid URL.",
            ) from e

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if "://" in value:
            return str(_SCHEME_URL_ADAPTER.validate_python(value))
        if not _SCP_URL_RE.fullmatch(value):
            raise ValueError(f"Invalid SCP-style git clone URL: {value}")
        return value

    @cached_property
    def host(self) -> str | None:
        """The host of the URL."""
        if "://" in self.url:
            return urlsplit(self.url).hostname
        return self.url.split("@", 1)[-1].partition(":")[0] or None

    @cached_property
    def path(self) -> str | None:
        """The path of the URL without a leading slash or ``.git`` suffix."""
        if "://" in self.url:
            raw_path = urlsplit(self.url).path
        else:
            raw_path = self.url.partition(":")[2]
        return raw_path.strip("/").removesuffix(".git") or None

    @cached_property
    def namespace(self) -> str | None:
        """The namespace of the URL."""
        return self.path.rpartition("/")[0] if self.path else None

    @cached_property
    def repo(self) -> str | None:
        """The repository name of the URL."""
        return self.path.rpartition("/")[2] if self.path else None

    def __str__(self) -> str:
        return self.url

    def __repr__(self) -> str:
        return (
            f"GitCloneUrl(url={self.url!r}, ref={self.ref!r}, host={self.host!r}, "
            f"namespace={self.namespace!r}, repo={self.repo!r})"
        )


def parse_git_clone_url(url: str, ref: str | None = None) -> GitCloneUrl:
    """
    Build a typed clone URL from a raw string.

    :param url: the raw git clone URL
    :param ref: the commit ref if not provided in the URL
    :return: the typed git clone URL
    :raises UnexpectedFormat: if the URL is invalid or host, namespace, or repo
        cannot be derived from it

    >>> parse_git_clone_url("https://github.com/example-org/example-repo.git")
    GitCloneUrl(url='https://github.com/example-org/example-repo.git', ref='', host='github.com', namespace='example-org', repo='example-repo')
    >>> parse_git_clone_url("git@github.com:example-org/example-repo.git")
    GitCloneUrl(url='git@github.com:example-org/example-repo.git', ref='', host='github.com', namespace='example-org', repo='example-repo')
    >>> parse_git_clone_url("git+ssh://git@github.com/org/repo.git#abc123")
    GitCloneUrl(url='ssh://git@github.com/org/repo.git', ref='abc123', host='github.com', namespace='org', repo='repo')
    >>> parse_git_clone_url("invalid-url")
    Traceback (most recent call last):
    ...
    hermeto.core.errors.UnexpectedFormat: ...
    """
    clean_url, _, fragment = url.partition("#")
    # if scheme is git+protocol://, keep only protocol://
    clean_url = clean_url.removeprefix("git+")
    ref = ref if ref is not None else fragment

    clone_url = GitCloneUrl(clean_url, ref.lower())

    if not clone_url.host or not clone_url.namespace or not clone_url.repo:
        raise UnexpectedFormat(
            f"Cannot parse git URL: {url}",
            solution="Ensure the git dependency has a valid URL.",
        )

    return clone_url


def clone_repo_pack_archive(
    clone_url: GitCloneUrl,
    deps_dir: RootedPath,
) -> RootedPath:
    """Clone a git repository at a specific ref and pack it as a tarball.

    The tarball path follows the convention:
        {deps_dir}/{host}/{namespace}/{repo}/{repo}-external-gitcommit-{ref}.tgz

    :param clone_url: a typed git clone URL including ref
    :param deps_dir: the directory under which tarballs will be placed
    :return: the RootedPath to the created tarball
    :raises UnexpectedFormat: if host, namespace, repo, or ref is missing
    """
    if not clone_url.host or not clone_url.namespace or not clone_url.repo or not clone_url.ref:
        raise UnexpectedFormat(
            f"Cannot parse git URL: {clone_url}",
            solution="Ensure the git dependency has a valid URL and commit ref.",
        )

    tarball_relpath = Path(
        clone_url.host,
        clone_url.namespace,
        clone_url.repo,
        f"{clone_url.repo}-external-gitcommit-{clone_url.ref}.tgz",
    )
    tarball_path = deps_dir.join_within_root(str(tarball_relpath))

    tarball_path.path.parent.mkdir(parents=True, exist_ok=True)
    clone_as_tarball(str(clone_url), clone_url.ref, tarball_path.path)

    return tarball_path
