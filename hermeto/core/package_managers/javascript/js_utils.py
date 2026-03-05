# SPDX-License-Identifier: GPL-3.0-only
"""Common utilities shared between JavaScript package managers (npm, yarn)."""

from pathlib import Path
from typing import NamedTuple

from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball


class NpmGitInfo(NamedTuple):
    """Parsed fields from an npm VCS dependency URL."""

    url: str
    ref: str
    host: str
    namespace: str
    repo: str


def clone_repo_pack_archive(
    info: NpmGitInfo,
    deps_dir: RootedPath,
) -> RootedPath:
    """Clone a git repository at a specific ref and pack it as a tarball.

    The tarball path follows the convention:
        {deps_dir}/{host}/{namespace}/{repo}/{repo}-external-gitcommit-{ref}.tgz

    :param info: parsed git repository location and ref
    :param deps_dir: the directory under which tarballs will be placed
    :return: the RootedPath to the created tarball
    """
    tarball_relpath = Path(
        info.host,
        info.namespace,
        info.repo,
        f"{info.repo}-external-gitcommit-{info.ref}.tgz",
    )
    tarball_path = deps_dir.join_within_root(str(tarball_relpath))

    tarball_path.path.parent.mkdir(parents=True, exist_ok=True)
    clone_as_tarball(info.url, info.ref, tarball_path.path)

    return tarball_path
