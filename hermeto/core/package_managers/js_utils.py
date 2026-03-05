# SPDX-License-Identifier: GPL-3.0-only
"""Common utilities shared between JavaScript package managers (npm, yarn)."""

import re
from pathlib import Path
from urllib.parse import urlparse

from hermeto.core.errors import PackageRejected
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball


def extract_git_url_parts(clone_url: str) -> dict[str, str]:
    """Extract host, namespace, and repo from a git clone URL.

    Handles both protocol-based URLs (https://host/ns/repo.git) and SCP-style
    URLs (git@host:ns/repo.git).

    :param clone_url: the git clone URL
    :return: dict with keys: host, namespace, repo
    :raises PackageRejected: if the URL cannot be parsed
    """
    if "://" in clone_url:
        parsed = urlparse(clone_url)
        host = parsed.hostname or ""
        path = parsed.path.strip("/").removesuffix(".git")
    else:
        # SCP-style: git@host:ns/repo.git
        match = re.match(r"^[^@]*@([^:]+):(.+)$", clone_url)
        if not match:
            raise PackageRejected(
                f"Cannot parse git URL: {clone_url}",
                solution="Ensure the git dependency has a valid URL.",
            )
        host = match.group(1)
        path = match.group(2).removesuffix(".git")

    namespace, _, repo = path.rpartition("/")
    return {"host": host, "namespace": namespace, "repo": repo}


def clone_repo_pack_archive(
    clone_url: str,
    ref: str,
    deps_dir: RootedPath,
) -> RootedPath:
    """Clone a git repository at a specific ref and pack it as a tarball.

    The tarball path follows the convention:
        {deps_dir}/{host}/{namespace}/{repo}/{repo}-external-gitcommit-{ref}.tgz

    :param clone_url: the git clone URL (protocol-based or SCP-style)
    :param ref: the git ref (commit SHA) to check out
    :param deps_dir: the directory under which tarballs will be placed
    :return: the RootedPath to the created tarball
    """
    parts = extract_git_url_parts(clone_url)
    tarball_relpath = Path(
        parts["host"],
        parts["namespace"],
        parts["repo"],
        f"{parts['repo']}-external-gitcommit-{ref}.tgz",
    )
    tarball_path = deps_dir.join_within_root(str(tarball_relpath))

    tarball_path.path.parent.mkdir(parents=True, exist_ok=True)
    clone_as_tarball(clone_url, ref, tarball_path.path)

    return tarball_path
