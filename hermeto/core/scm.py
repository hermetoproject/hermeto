# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import ParseResult, SplitResult, urlparse, urlsplit

import git
from git.exc import InvalidGitRepositoryError, NoSuchPathError
from git.repo import Repo

from hermeto import APP_NAME
from hermeto.core.errors import FetchError, NotAGitRepo, UnsupportedFeature
from hermeto.core.type_aliases import StrPath

log = logging.getLogger(__name__)


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


def get_repo_id(repo: StrPath | Repo) -> RepoID:
    """Get the RepoID for a git.Repo object or a git directory.

    If the remote url is an scp-style [user@]host:path, convert it into ssh://[user@]host/path.

    See `man git-clone` (GIT URLS) for some of the url formats that git supports.
    """
    if isinstance(repo, (str, os.PathLike)):
        try:
            repo = Repo(repo, search_parent_directories=True)
        except (InvalidGitRepositoryError, NoSuchPathError):
            raise NotAGitRepo(
                f"The provided path {repo} cannot be processed as a valid git repository.",
                solution=(
                    "Please ensure that the path is correct and that it is a valid git repository."
                ),
            )

    try:
        origin = repo.remote("origin")
    except ValueError:
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


def _find_submodule_containing_path(repo: Repo, target_path: Path) -> git.Submodule | None:
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


def _get_submodule_repo(submodule: git.Submodule) -> Repo:
    """Get the repository for a submodule with initialization validation.

    :param submodule: Git submodule to access
    :return: Git repository for the submodule
    :raises NotAGitRepo: if submodule is not initialized
    """
    try:
        return submodule.module()
    except InvalidGitRepositoryError:
        raise NotAGitRepo(
            f"Submodule '{submodule.path}' is not initialized",
            solution=f"Run 'git submodule update --init --recursive {submodule.path}' to initialize it",
        )


def get_repo_for_path(repo_root: Path, target_path: Path) -> tuple[Repo, Path]:
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

    current_repo = Repo(repo_root)

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


def _clone_git_repo(
    url: str,
    to_path: Path,
    ref: str,
    branch: str | None = None,
    filter: str | None = None,
) -> Repo:
    """Clone a git repository with common options and error handling.

    Args:
        url: Git repository URL
        to_path: Destination path for cloning
        ref: Git reference to checkout
        branch: Optional branch to checkout
        filter: Git filter for partial clone (e.g., 'blob:none', 'tree:0', 'blob:limit=1m')

    Returns:
        Cloned git.Repo object

    Raises:
        FetchError: If cloning fails
    """
    list_url = [url]
    if "ssh://" in url:
        list_url.append(url.replace("ssh://", "https://"))

    # Don't allow git to prompt for a username if we don't have access
    env = {"GIT_TERMINAL_PROMPT": "0"}
    kwargs: dict[str, Any] = {"no_checkout": True}

    if filter is not None:
        kwargs["filter"] = filter

    for url in list_url:
        log.debug("Cloning git repository from %s", url)
        try:
            repo = Repo.clone_from(url, to_path, env=env, **kwargs)

            if branch is not None:
                repo.git.checkout(branch)

        except Exception as ex:
            log.warning(
                "Failed cloning git repository from %s, ref: %s, exception: %s, exception-msg: %s",
                url,
                ref,
                type(ex).__name__,
                str(ex),
            )
            continue

        # Reset to specific commit
        _reset_git_head(repo, ref)

        log.debug("Successfully cloned %s to %s", url, to_path)
        return repo

    raise FetchError("Failed cloning the Git repository")


def clone_as_tarball(url: str, ref: str, to_path: Path) -> None:
    """Clone a git repository, check out the specified revision and create a compressed tarball.

    The repository content will be under the app/ directory in the tarball.

    :param url: the URL of the repository
    :param ref: the revision to check out
    :param to_path: create the tarball at this path
    """
    with tempfile.TemporaryDirectory(prefix="cachito-") as temp_dir:
        repo = _clone_git_repo(url=url, to_path=Path(temp_dir), ref=ref, filter="blob:none")

        with tarfile.open(to_path, mode="w:gz") as archive:
            archive.add(repo.working_dir, "app")


def clone_git_dependency(url: str, ref: str, to_path: Path, branch: str | None = None) -> None:
    """Clone a git dependency.

    Args:
        url: Git repository URL
        ref: Git reference (full commit hash)
        to_path: Destination path for cloning
        branch: Optional branch to checkout

    Raises:
        FetchError: If cloning fails
    """

    log.debug("Cloning git repository from %s", url)

    _clone_git_repo(
        url=url,
        to_path=to_path,
        ref=ref,
        branch=branch,
    )


def _reset_git_head(repo: Repo, ref: str) -> None:
    try:
        repo.head.reference = repo.commit(ref)  # type: ignore # 'reference' is a weird property
        repo.head.reset(index=True, working_tree=True)
    except Exception as ex:
        log.exception(
            "Failed on checking out the Git ref %s, exception: %s",
            ref,
            type(ex).__name__,
        )
        # Not necessarily a FetchError, but the checkout *does* also fetch stuff
        #   (because we clone with --filter=blob:none)
        raise FetchError(
            "Failed on checking out the Git repository. Please verify the supplied reference "
            f'of "{ref}" is valid.'
        )
