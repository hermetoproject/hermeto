import filecmp
import sys
import tarfile
from pathlib import Path
from typing import Any, Union
from urllib.parse import urlsplit

import pytest
from git.repo import Repo

from hermeto.core.errors import FetchError, NotAGitRepo, UnsupportedFeature
from hermeto.core.scm import RepoID, clone_as_tarball, extract_git_info, get_repo_id
from tests.common_utils import GIT_REF

INITIAL_COMMIT = "78510c591e2be635b010a52a7048b562bad855a3"


class TestRepoID:
    @pytest.mark.parametrize(
        "repo_url, expect_result",
        [
            # scp-style
            ("git.host.com:some/path", "ssh://git.host.com/some/path"),
            ("git.host.com:/some/path", "ssh://git.host.com/some/path"),
            ("user@git.host.com:some/path", "ssh://user@git.host.com/some/path"),
            # no-op
            ("ssh://user@git.host.com/some/path", "ssh://user@git.host.com/some/path"),
            ("https://git.host.com/some/path", "https://git.host.com/some/path"),
            # credentials
            (
                "https://student:password@github.com/student/repo.git",
                "https://github.com/student/repo.git",
            ),
            # unsupported
            (
                "./foo:bar",
                UnsupportedFeature("Could not canonicalize repository origin url: ./foo:bar"),
            ),
            (
                "/foo",
                UnsupportedFeature("Could not canonicalize repository origin url: /foo"),
            ),
        ],
    )
    def test_get_repo_id(
        self, repo_url: str, expect_result: Union[str, Exception], golang_repo_path: Path
    ) -> None:
        Repo(golang_repo_path).create_remote("origin", repo_url)
        expect_commit_id = "4a481f0bae82adef3ea6eae3d167af6e74499cb2"

        if isinstance(expect_result, str):
            repo_id = get_repo_id(golang_repo_path)
            assert repo_id.origin_url == expect_result
            assert repo_id.parsed_origin_url == urlsplit(expect_result)
            assert repo_id.commit_id == expect_commit_id
        else:
            with pytest.raises(type(expect_result), match=str(expect_result)):
                get_repo_id(golang_repo_path)

    def test_get_repo_id_no_origin(self, golang_repo_path: Path) -> None:
        with pytest.raises(
            UnsupportedFeature,
            match="cannot process repositories that don't have an 'origin' remote",
        ):
            get_repo_id(golang_repo_path)

    def test_get_repo_id_invalid_path(self, tmp_path: Path) -> None:
        with pytest.raises(NotAGitRepo):
            get_repo_id(tmp_path)

    def test_as_vcs_url_qualifier(self) -> None:
        origin_url = "ssh://git@github.com/foo/bar.git"
        commit_id = "abcdef1234"
        expect_vcs_url = "git+ssh://git@github.com/foo/bar.git@abcdef1234"
        assert RepoID(origin_url, commit_id).as_vcs_url_qualifier() == expect_vcs_url


def test_clone_as_tarball(golang_repo_path: Path, tmp_path: Path) -> None:
    original_path = golang_repo_path
    to_path = tmp_path / "my-repo.tar.gz"

    clone_as_tarball(f"file://{original_path}", INITIAL_COMMIT, to_path)

    with tarfile.open(to_path) as tar:
        if sys.version_info >= (3, 12):
            tar.extractall(tmp_path / "my-repo", filter="fully_trusted")
        else:
            tar.extractall(tmp_path / "my-repo")

    my_path = tmp_path / "my-repo" / "app"

    original_repo = Repo(original_path)
    my_repo = Repo(my_path)

    assert original_repo.commit().hexsha != my_repo.commit().hexsha
    assert my_repo.commit().hexsha == INITIAL_COMMIT

    compare = filecmp.dircmp(original_path, my_path)
    assert compare.same_files == [
        ".gitignore",
        "README.md",
        "go.sum",
        "main.go",
    ]
    # go.mod is the only file that changed between the initial commit and the current one
    assert compare.diff_files == ["go.mod"]


def test_clone_as_tarball_wrong_url(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="Failed cloning the Git repository"):
        clone_as_tarball("file:///no/such/directory", INITIAL_COMMIT, tmp_path / "my-repo.tar.gz")


def test_clone_as_tarball_wrong_ref(golang_repo_path: Path, tmp_path: Path) -> None:
    bad_commit = "baaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaad"
    with pytest.raises(
        FetchError,
        match=f'Please verify the supplied reference of "{bad_commit}" is valid',
    ):
        clone_as_tarball(f"file://{golang_repo_path}", bad_commit, tmp_path / "my-repo.tar.gz")


@pytest.mark.parametrize(
    "url, nonstandard_info",  # See body of function for what is standard info
    [
        (
            # Standard case
            f"git+https://github.com/monty/python@{GIT_REF}",
            None,
        ),
        (
            # Ref should be converted to lowercase
            f"git+https://github.com/monty/python@{GIT_REF.upper()}",
            {"ref": GIT_REF},  # Standard but be explicit about it
        ),
        (
            # Repo ends with .git (that is okay)
            f"git+https://github.com/monty/python.git@{GIT_REF}",
            {"url": "https://github.com/monty/python.git"},
        ),
        (
            # git://
            f"git://github.com/monty/python@{GIT_REF}",
            {"url": "git://github.com/monty/python"},
        ),
        (
            # git+git://
            f"git+git://github.com/monty/python@{GIT_REF}",
            {"url": "git://github.com/monty/python"},
        ),
        (
            # No namespace
            f"git+https://github.com/python@{GIT_REF}",
            {"url": "https://github.com/python", "namespace": ""},
        ),
        (
            # Namespace with more parts
            f"git+https://github.com/monty/python/and/the/holy/grail@{GIT_REF}",
            {
                "url": "https://github.com/monty/python/and/the/holy/grail",
                "namespace": "monty/python/and/the/holy",
                "repo": "grail",
            },
        ),
        (
            # Port should be part of host
            f"git+https://github.com:443/monty/python@{GIT_REF}",
            {"url": "https://github.com:443/monty/python", "host": "github.com:443"},
        ),
        (
            # Authentication should not be part of host
            f"git+https://user:password@github.com/monty/python@{GIT_REF}",
            {
                "url": "https://user:password@github.com/monty/python",
                "host": "github.com",  # Standard but be explicit about it
            },
        ),
        (
            # Params, query and fragment should be stripped
            f"git+https://github.com/monty/python@{GIT_REF};foo=bar?bar=baz#egg=spam",
            {
                # Standard but be explicit about it
                "url": "https://github.com/monty/python",
            },
        ),
        (
            # RubyGems case
            f"https://github.com/monty/python@{GIT_REF}",
            {
                # Standard but be explicit about it
                "url": "https://github.com/monty/python",
            },
        ),
    ],
)
def test_extract_git_info(url: str, nonstandard_info: Any) -> None:
    """Test extraction of git info from VCS URL."""
    info = {
        "url": "https://github.com/monty/python",
        "ref": GIT_REF,
        "namespace": "monty",
        "repo": "python",
        "host": "github.com",
    }
    info.update(nonstandard_info or {})
    assert extract_git_info(url) == info
