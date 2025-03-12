import sys
import tarfile
from pathlib import Path

import git
import pytest

from hermeto.core.models.input import Request
from hermeto.core.rooted_path import RootedPath


@pytest.fixture
def data_dir() -> Path:
    """Return Path object for the directory that stores unit test data."""
    return Path(__file__).parent / "data"


@pytest.fixture
def golang_repo_path(data_dir: Path, tmp_path: Path) -> Path:
    """Return extracted Golang git repository inside a temporary directory."""
    with tarfile.open(data_dir / "golang_git_repo.tar.gz") as tar:
        if sys.version_info >= (3, 12):
            tar.extractall(tmp_path, filter="fully_trusted")
        else:
            tar.extractall(tmp_path)

    return tmp_path / "golang_git_repo"


@pytest.fixture
def rooted_tmp_path(tmp_path: Path) -> RootedPath:
    """Return RootedPath object wrapper for the tmp_path fixture."""
    return RootedPath(tmp_path)


@pytest.fixture
def rooted_tmp_path_repo(rooted_tmp_path: RootedPath) -> RootedPath:
    """Return RootedPath object wrapper for the tmp_path fixture with initialized git repository."""
    repo = git.Repo.init(rooted_tmp_path)
    repo.git.config("user.name", "user")
    repo.git.config("user.email", "user@example.com")

    Path(rooted_tmp_path, "README.md").touch()
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")

    return rooted_tmp_path


@pytest.fixture
def input_request(tmp_path: Path, request: pytest.FixtureRequest) -> Request:
    package_input: list[dict[str, str]] = request.param

    # Create folder in the specified path, otherwise Request validation would fail
    for package in package_input:
        if "path" in package:
            (tmp_path / package["path"]).mkdir(exist_ok=True)

    return Request(
        source_dir=tmp_path,
        output_dir=tmp_path / "output",
        packages=package_input,
    )
