# SPDX-License-Identifier: GPL-3.0-only
import pytest

from hermeto.core.errors import UnexpectedFormat
from hermeto.core.package_managers.npm.utils import (
    NormalizedUrl,
    extract_git_info_npm,
    is_from_npm_registry,
    update_vcs_url_with_full_hostname,
)
from tests.common_utils import GIT_REF


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


@pytest.mark.parametrize(
    "vcs, expected",
    [
        (
            (f"git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git#{GIT_REF}"),
            {
                "url": "ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git",
                "ref": GIT_REF,
                "host": "bitbucket.org",
                "namespace": "cachi-testing",
                "repo": "cachi2-without-deps",
            },
        ),
    ],
)
def test_extract_git_info_npm(vcs: NormalizedUrl, expected: dict[str, str]) -> None:
    assert extract_git_info_npm(vcs) == expected


def test_extract_git_info_with_missing_ref() -> None:
    vcs = NormalizedUrl("git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git")
    expected_error = (
        "ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git "
        "is not valid VCS url. ref is missing."
    )
    with pytest.raises(UnexpectedFormat, match=expected_error):
        extract_git_info_npm(vcs)


@pytest.mark.parametrize(
    "vcs, expected",
    [
        (
            "github:kevva/is-positive#97edff6",
            "git+ssh://git@github.com/kevva/is-positive.git#97edff6",
        ),
        ("github:kevva/is-positive", "git+ssh://git@github.com/kevva/is-positive.git"),
        (
            "bitbucket:cachi-testing/cachi2-without-deps#9e164b9",
            "git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git#9e164b9",
        ),
        ("gitlab:foo/bar#YOLO", "git+ssh://git@gitlab.com/foo/bar.git#YOLO"),
    ],
)
def test_update_vcs_url_with_full_hostname(vcs: str, expected: str) -> None:
    assert update_vcs_url_with_full_hostname(vcs) == expected
