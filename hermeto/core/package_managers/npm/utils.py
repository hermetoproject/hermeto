# SPDX-License-Identifier: GPL-3.0-only
from typing import Literal, NewType
from urllib.parse import urlparse

from hermeto.core.errors import UnexpectedFormat

# In rare cases, package-lock.json may contain resolved urls from the Yarn registry.
# This most likely happens when converting a yarn.lock to package-lock.json
# ("importing" one with npm or "exporting" with yarn).
NPM_REGISTRY_URL = "https://registry.npmjs.org"
YARN_REGISTRY_URL = "https://registry.yarnpkg.com"

NormalizedUrl = NewType("NormalizedUrl", str)


def normalize_resolved_url(resolved_url: str) -> NormalizedUrl:
    """Normalize the resolved URL format used in npm lockfiles."""
    if resolved_url.startswith(("github:", "gitlab:", "bitbucket:")):
        resolved_url = update_vcs_url_with_full_hostname(resolved_url)
    return NormalizedUrl(resolved_url)


def is_from_npm_registry(url: str) -> bool:
    """Return True if a package URL is from the NPM or Yarn registry."""
    return urlparse(url).hostname in ("registry.npmjs.org", "registry.yarnpkg.com")


def classify_resolved_url(
    resolved_url: NormalizedUrl,
) -> Literal["registry", "git", "file", "https"]:
    """Classify a normalized npm resolved URL by source type."""
    url = urlparse(resolved_url)
    if is_from_npm_registry(resolved_url):
        return "registry"
    if url.scheme == "git" or url.scheme.startswith("git+"):
        return "git"
    if url.scheme == "file":
        return "file"
    return "https"


def update_vcs_url_with_full_hostname(vcs: str) -> str:
    """Update VCS URL with full hostname.

    Transform github:kevva/is-positive#97edff6
    into git+ssh://github.com/kevva/is-positive.git#97edff6

    :param vcs: VCS URL to be modified with full hostname and file extension
    :return: Updated VCS URL
    """
    host, _, path = vcs.partition(":")
    namespace_repo, _, ref = path.partition("#")
    suffix_domain = "org" if host == "bitbucket" else "com"

    vcs = f"git+ssh://git@{host}.{suffix_domain}/{namespace_repo}.git"
    if ref:
        vcs = f"{vcs}#{ref}"
    return vcs


def extract_git_info_npm(vcs_url: NormalizedUrl) -> dict[str, str]:
    """
    Extract important info from a VCS requirement URL.

    Given a URL such as git+ssh://user@host/namespace/repo.git#9e164b970

    this function will extract:
    - the "clean" URL: ssh://user@host/namespace/repo.git
    - the git ref: 9e164b970

    The clean URL and ref can be passed straight to scm.Git to fetch the repo.
    The host, namespace and repo will be used to construct the file path under deps/npm.

    :param vcs_url: The URL of a VCS requirement, must be valid (have git ref in path)
    :return: Dict with url, ref, host, namespace and repo keys
    """
    clean_url, _, ref = vcs_url.partition("#")
    # if scheme is git+protocol://, keep only protocol://
    clean_url = clean_url.removeprefix("git+")

    url = urlparse(clean_url)
    namespace_repo = url.path.strip("/").removesuffix(".git")

    # Everything up to the last '/' is namespace, the rest is repo
    namespace, _, repo = namespace_repo.partition("/")

    vcs_url_info = {
        "url": clean_url,
        "ref": ref.lower(),
        "namespace": namespace,
        "repo": repo,
    }

    for key, value in vcs_url_info.items():
        if not value:
            raise UnexpectedFormat(f"{vcs_url} is not valid VCS url. {key} is missing.")

    if url.hostname:
        vcs_url_info["host"] = url.hostname
    else:
        raise UnexpectedFormat(f"{vcs_url} is not valid VCS url. Host is missing.")

    return vcs_url_info
