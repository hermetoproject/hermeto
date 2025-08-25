# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import re
import tarfile
import tempfile
from os import PathLike
from pathlib import Path
from typing import NamedTuple, Optional, Union
from urllib.parse import ParseResult, SplitResult, urlparse, urlsplit

import git
from git.exc import InvalidGitRepositoryError, NoSuchPathError
from git.repo import Repo

from hermeto import APP_NAME
from hermeto.core.errors import FetchError, NotAGitRepo, UnsupportedFeature

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


def get_repo_id(repo: Union[str, PathLike[str], Repo]) -> RepoID:
    """Get the RepoID for a git.Repo object or a git directory.

    If the remote url is an scp-style [user@]host:path, convert it into ssh://[user@]host/path.

    See `man git-clone` (GIT URLS) for some of the url formats that git supports.
    """
    if isinstance(repo, (str, PathLike)):
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


def _find_submodule_containing_path(repo: Repo, target_path: Path) -> Optional[git.Submodule]:
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
        parts[-1] = re.sub(r":/*", "/", parts[-1], 1)
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
                repo = Repo.clone_from(
                    url,
                    temp_dir,
                    no_checkout=True,
                    filter="blob:none",
                    # Don't allow git to prompt for a username if we don't have access
                    env={"GIT_TERMINAL_PROMPT": "0"},
                )
            except Exception as ex:
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
