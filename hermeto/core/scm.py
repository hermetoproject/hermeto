# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import re
import tarfile
import tempfile
from os import PathLike
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import ParseResult, SplitResult, urlparse, urlsplit

import git
from git.exc import BadName, GitCommandError, InvalidGitRepositoryError, NoSuchPathError

from hermeto import APP_NAME
from hermeto.core.errors import (
    FetchError,
    GitError,
    GitInvalidRevisionError,
    GitRemoteNotFoundError,
    NotAGitRepo,
    UnsupportedFeature,
)
from hermeto.core.type_aliases import StrPath

log = logging.getLogger(__name__)


class GitCommandWrapper(git.Git):
    """Git command wrapper that converts GitCommandError to GitError."""

    def execute(self, command: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute git command with unified error handling."""
        try:
            return super().execute(command, *args, **kwargs)
        except GitCommandError as ex:
            raise GitError(
                f"Git command failed: {ex}",
                stderr=ex.stderr,
                stdout=ex.stdout,
            ) from ex


class GitHEAD(git.HEAD):
    """HEAD reference wrapper with unified error handling."""

    @property
    def commit(self) -> git.Commit:
        """Get HEAD commit with error handling."""
        try:
            return super().commit
        except ValueError as ex:
            # Reference doesn't exist or parsing failed
            raise GitInvalidRevisionError(f"Failed to access HEAD commit: {ex}") from ex
        except TypeError as ex:
            # Reference points to non-commit object
            raise GitInvalidRevisionError(f"HEAD does not point to a commit: {ex}") from ex

    @commit.setter
    def commit(self, commit: git.Commit | git.SymbolicReference | str) -> None:
        """Set HEAD commit with error handling."""
        try:
            self.set_commit(commit)
        except ValueError as ex:
            raise GitInvalidRevisionError(f"Failed to set HEAD commit: {ex}") from ex

    @property  # type: ignore[override]
    def reference(self) -> git.SymbolicReference:
        """Get the reference we point to with error handling."""
        try:
            return super().reference
        except ValueError as ex:
            raise GitInvalidRevisionError(f"Failed to access HEAD reference: {ex}") from ex
        except TypeError as ex:
            # HEAD is detached (points to commit, not reference)
            raise GitInvalidRevisionError(f"HEAD is detached: {ex}") from ex

    @reference.setter
    def reference(self, ref: git.Commit | git.SymbolicReference | str) -> None:
        """Set the reference we point to with error handling."""
        try:
            self.set_reference(ref)
        except ValueError as ex:
            raise GitInvalidRevisionError(f"Failed to set HEAD reference: {ex}") from ex
        except TypeError as ex:
            raise GitInvalidRevisionError(f"HEAD can only point to commits: {ex}") from ex

    def reset(self, **kwargs: Any) -> "GitHEAD":  # type: ignore[override]
        """Reset HEAD with error handling."""
        try:
            super().reset(**kwargs)
            return GitHEAD(self.repo, "HEAD")
        except ValueError as ex:
            raise GitInvalidRevisionError(f"Failed to reset HEAD: {ex}") from ex
        except GitCommandError as ex:
            raise GitError(
                f"Git reset command failed: {ex}",
                stderr=ex.stderr,
            ) from ex


class GitRepo(git.Repo):
    """Git repository wrapper with unified error handling."""

    GitCommandWrapperType = GitCommandWrapper

    def __init__(self, path: str | PathLike[str], *args: Any, **kwargs: Any) -> None:
        """Initialize git repository with unified error handling."""
        try:
            super().__init__(path, *args, **kwargs)
        except (InvalidGitRepositoryError, NoSuchPathError):
            raise NotAGitRepo(
                f"The provided path {path} cannot be processed as a valid git repository.",
                solution=(
                    "Please ensure that the path is correct and that it is a valid git repository."
                ),
            )

    @classmethod
    def clone_from(  # type: ignore[override]
        cls, url: str | PathLike[str], to_path: str | PathLike[str], **kwargs: Any
    ) -> "GitRepo":
        """Clone repository and return GitRepo with error handling."""
        try:
            git.Repo.clone_from(url, to_path, **kwargs)
            return cls(to_path)
        except GitCommandError as ex:
            log.warning(
                "Failed cloning git repository from %s, exception: %s, exception-msg: %s",
                url,
                type(ex).__name__,
                str(ex),
            )
            raise GitError(
                f"Failed to clone repository from {url}",
                stderr=ex.stderr,
            ) from ex

    @property
    def head(self) -> GitHEAD:
        """Get repository HEAD reference with unified error handling."""
        try:
            return GitHEAD(self, "HEAD")
        except ValueError as ex:
            raise GitInvalidRevisionError(f"Failed to access HEAD reference: {ex}") from ex

    def commit(self, rev: str | None = None) -> git.Commit:
        """Get commit object by revision with error handling."""
        try:
            return super().commit(rev)
        except (ValueError, BadName) as ex:
            raise GitInvalidRevisionError(
                f"Invalid revision '{rev}': {ex}",
            ) from ex

    def remote(self, name: str = "origin") -> git.Remote:
        """Get remote object by name with error handling."""
        try:
            return super().remote(name)
        except ValueError as ex:
            raise GitRemoteNotFoundError(
                f"Git remote '{name}' does not exist in this repository"
            ) from ex


class RepoID(NamedTuple):
    """The properties which uniquely identify a repository at a specific commit."""

    origin_url: str
    commit_id: str

    @property
    def parsed_origin_url(self) -> SplitResult:
        """Get the url as a urllib.parse.SplitResult."""
        return urlsplit(self.origin_url)

    def as_vcs_url_qualifier(self) -> str:
        """Turn this RepoID into a 'vcs_url' qualifier as defined by the purl spec.

        See https://github.com/package-url/purl-spec/blob/master/PURL-SPECIFICATION.rst#known-qualifiers-keyvalue-pairs
        """
        return f"git+{self.origin_url}@{self.commit_id}"


def get_repo_id(repo: StrPath | GitRepo | git.Repo) -> RepoID:
    """Get the RepoID for a git.Repo object or a git directory.

    If the remote url is an scp-style [user@]host:path, convert it into ssh://[user@]host/path.

    See `man git-clone` (GIT URLS) for some of the url formats that git supports.
    """
    if isinstance(repo, (str, os.PathLike)):
        repo = GitRepo(repo, search_parent_directories=True)
    elif isinstance(repo, git.Repo) and not isinstance(repo, GitRepo):
        repo = GitRepo(repo.working_dir)

    try:
        origin = repo.remote("origin")
    except GitRemoteNotFoundError:
        raise UnsupportedFeature(
            f"{APP_NAME} cannot process repositories that don't have an 'origin' remote",
            solution=(
                "Repositories cloned via git clone should always have one.\n"
                "Otherwise, please `git remote add origin` with a url that reflects the origin."
            ),
        )

    url = _canonicalize_origin_url(origin.url)
    commit_id = repo.head.commit.hexsha
    return RepoID(url, commit_id)


def _find_submodule_containing_path(repo: GitRepo, target_path: Path) -> git.Submodule | None:
    """Find the submodule containing the target path, if any.

    :param repo: Git repository to search in
    :param target_path: Path to find containing submodule for
    :return: submodule containing the target_path or None if no submodule contains it
    """
    for submodule in repo.submodules:
        submodule_path = Path(repo.working_dir, submodule.path)
        if target_path.is_relative_to(submodule_path):
            return submodule
    return None


def _get_submodule_repo(submodule: git.Submodule) -> GitRepo:
    """Get the repository for a submodule with initialization validation.

    :param submodule: Git submodule to access
    :return: Git repository for the submodule
    :raises NotAGitRepo: if submodule is not initialized
    """
    try:
        base_repo = submodule.module()
        return GitRepo(base_repo.working_dir)
    except InvalidGitRepositoryError:
        raise NotAGitRepo(
            f"Submodule '{submodule.path}' is not initialized",
            solution=f"Run 'git submodule update --init --recursive {submodule.path}' to initialize it",
        )


def get_repo_for_path(repo_root: Path, target_path: Path) -> tuple[GitRepo, Path]:
    """
    Get the appropriate git.Repo and relative path for a target path.

    Handles nested submodules by iteratively finding the deepest submodule
    containing the target path.

    :param repo_root: Root of the main repository
    :param target_path: Path to operate on
    :return: Tuple of (repo, relative_path)
    :raises NotAGitRepo: if target is in an uninitialized submodule
    """
    if not target_path.is_absolute():
        target_path = repo_root / target_path

    current_repo = GitRepo(repo_root)

    while (submodule := _find_submodule_containing_path(current_repo, target_path)) is not None:
        current_repo = _get_submodule_repo(submodule)

    relative_path = target_path.relative_to(current_repo.working_dir)
    return current_repo, relative_path


def _canonicalize_origin_url(url: str) -> str:
    if "://" in url:
        parsed: ParseResult = urlparse(url)
        cleaned_netloc = parsed.netloc.replace(
            f"{parsed.username}:{parsed.password}@",
            "",
        )
        return parsed._replace(netloc=cleaned_netloc).geturl()
    # scp-style is "only recognized if there are no slashes before the first colon"
    elif re.match("^[^/]*:", url):
        parts = url.split("@", 1)
        # replace the ':' in the host:path part with a '/'
        # and strip leading '/' from the path, if any
        parts[-1] = re.sub(r":/*", "/", parts[-1], count=1)
        return "ssh://" + "@".join(parts)
    else:
        raise UnsupportedFeature(
            f"Could not canonicalize repository origin url: {url}", solution=None
        )


def clone_as_tarball(url: str, ref: str, to_path: Path) -> None:
    """Clone a git repository, check out the specified revision and create a compressed tarball.

    The repository content will be under the app/ directory in the tarball.

    :param url: the URL of the repository
    :param ref: the revision to check out
    :param to_path: create the tarball at this path
    """
    list_url = [url]
    # Fallback to `https` if cloning source via ssh fails
    if "ssh://" in url:
        list_url.append(url.replace("ssh://", "https://"))

    with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
        for url in list_url:
            log.debug("Cloning the Git repository from %s", url)
            try:
                repo = GitRepo.clone_from(
                    url,
                    temp_dir,
                    no_checkout=True,
                    filter="blob:none",
                    # Don't allow git to prompt for a username if we don't have access
                    env={"GIT_TERMINAL_PROMPT": "0"},
                )
            except GitError as ex:
                log.warning(
                    "Failed cloning the Git repository from %s, ref: %s, exception: %s, exception-msg: %s",
                    url,
                    ref,
                    type(ex).__name__,
                    str(ex),
                )
                continue

            _reset_git_head(repo, ref)

            with tarfile.open(to_path, mode="w:gz") as archive:
                archive.add(repo.working_dir, "app")

            return

    raise FetchError("Failed cloning the Git repository")


def _reset_git_head(repo: GitRepo, ref: str) -> None:
    try:
        repo.head.reference = repo.commit(ref)  # type: ignore # 'reference' is a weird property
        repo.head.reset(index=True, working_tree=True)
    except GitError as ex:
        log.exception(
            "Failed on checking out the Git ref %s, %s",
            ref,
            f"{type(ex).__name__}: {ex.friendly_msg()}",
        )
        # Not necessarily a FetchError, but the checkout *does* also fetch stuff
        #   (because we clone with --filter=blob:none)
        raise FetchError(
            "Failed on checking out the Git repository. Please verify the supplied reference "
            f'of "{ref}" is valid.'
        )
