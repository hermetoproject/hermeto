# SPDX-License-Identifier: GPL-3.0-only
"""Common utilities shared between JavaScript package managers (npm, yarn)."""

from pathlib import Path

from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball


def clone_repo_pack_archive(
    clone_url: str,
    ref: str,
    deps_dir: RootedPath,
    host: str,
    namespace: str,
    repo: str,
) -> RootedPath:
    """Clone a git repository at a specific ref and pack it as a tarball.

    The tarball path follows the convention:
        {deps_dir}/{host}/{namespace}/{repo}/{repo}-external-gitcommit-{ref}.tgz

    :param clone_url: the git clone URL
    :param ref: the git ref (commit SHA) to check out
    :param deps_dir: the directory under which tarballs will be placed
    :param host: the hostname portion of the URL
    :param namespace: the namespace/org portion of the URL path
    :param repo: the repository name
    :return: the RootedPath to the created tarball
    """
    tarball_relpath = Path(
        host,
        namespace,
        repo,
        f"{repo}-external-gitcommit-{ref}.tgz",
    )
    tarball_path = deps_dir.join_within_root(str(tarball_relpath))

    tarball_path.path.parent.mkdir(parents=True, exist_ok=True)
    clone_as_tarball(clone_url, ref, tarball_path.path)

    return tarball_path
