# SPDX-License-Identifier: GPL-3.0-only
import pytest

from hermeto.core.errors import UnexpectedFormat
from hermeto.core.package_managers.javascript.js_utils import (
    GitCloneUrl,
    clone_repo_pack_archive,
    parse_git_clone_url,
)
from hermeto.core.package_managers.javascript.npm.utils import (
    is_from_npm_registry,
    update_vcs_url_with_full_hostname,
)
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF


def test_clone_repo_pack_archive_rejects_missing_ref(rooted_tmp_path: RootedPath) -> None:
    url = parse_git_clone_url("git+ssh://git@bitbucket.org/example-org/example-repo.git")
    with pytest.raises(UnexpectedFormat):
        clone_repo_pack_archive(url, rooted_tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "https://registry.npmjs.org/chai/-/chai-4.2.0.tgz",
        "https://registry.yarnpkg.com/chai/-/chai-4.2.0.tgz",
    ],
)
def test_is_from_npm_registry_can_parse_correct_registry_urls(url: str) -> None:
    assert is_from_npm_registry(url)


def test_is_from_npm_registry_can_parse_incorrect_registry_urls() -> None:
    assert not is_from_npm_registry("https://example.org/fecha.tar.gz")


def test_parse_git_clone_url_from_npm_vcs() -> None:
    vcs = f"git+ssh://git@bitbucket.org/example-org/example-repo.git#{GIT_REF}"
    assert parse_git_clone_url(vcs) == GitCloneUrl(
        "ssh://git@bitbucket.org/example-org/example-repo.git",
        GIT_REF,
    )


@pytest.mark.parametrize(
    "vcs, expected",
    [
        (
            "github:kevva/is-positive#97edff6",
            "git+ssh://git@github.com/kevva/is-positive.git#97edff6",
        ),
        ("github:kevva/is-positive", "git+ssh://git@github.com/kevva/is-positive.git"),
        (
            "bitbucket:example-org/example-repo#9e164b9",
            "git+ssh://git@bitbucket.org/example-org/example-repo.git#9e164b9",
        ),
        ("gitlab:foo/bar#YOLO", "git+ssh://git@gitlab.com/foo/bar.git#YOLO"),
    ],
)
def test_update_vcs_url_with_full_hostname(vcs: str, expected: str) -> None:
    assert update_vcs_url_with_full_hostname(vcs) == expected
