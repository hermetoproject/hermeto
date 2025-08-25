import shutil
import sys
import tarfile
from pathlib import Path
from typing import Optional

import git
import pytest

from hermeto.core.models.input import Request
from hermeto.core.rooted_path import RootedPath


def _create_git_repo(path: Path, files: Optional[dict[str, str]] = None) -> git.Repo:
    """Create a git repository with initial files.

    :param path: Directory to create the repository in
    :param files: Dictionary of file paths to content (empty string creates empty file)
    :return: Initialized git repository
    """
    path.mkdir(exist_ok=True)
    repo = git.Repo.init(path)
    repo.git.config("user.name", "user")
    repo.git.config("user.email", "user@example.com")

    if files:
        for file_path, content in files.items():
            file_full_path = path / file_path
            file_full_path.parent.mkdir(parents=True, exist_ok=True)
            file_full_path.write_text(content)
            repo.index.add([file_path])
        repo.index.commit("Initial commit")

    return repo


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
    _create_git_repo(rooted_tmp_path.path, {"README.md": ""})
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


@pytest.fixture
def repo_with_nested_submodules(tmp_path: Path) -> git.Repo:
    """Create a git repository with nested submodules: main -> submodule -> nested."""
    # Create nested submodule origin
    nested_origin = tmp_path / "nested_origin"
    _create_git_repo(nested_origin, {"nested_file.txt": "nested content"})

    # Create submodule origin with its own nested submodule
    submodule_origin = tmp_path / "submodule_origin"
    submodule_repo = _create_git_repo(submodule_origin, {"submodule_file.txt": "initial content"})

    nested_sub = submodule_repo.create_submodule(
        name="nested",
        path="nested",
        url=f"file://{nested_origin}",
    )
    submodule_repo.index.commit("Add nested submodule")
    nested_sub.update(init=True)

    # Create main repository with submodule
    main_dir = tmp_path / "main"
    main_repo = _create_git_repo(main_dir, {"README.md": "# Main Repository"})

    submodule = main_repo.create_submodule(
        name="submodule",
        path="submodule",
        url=f"file://{submodule_origin}",
    )
    main_repo.index.commit("Add submodule")
    submodule.update(init=True, recursive=True)

    return main_repo


@pytest.fixture
def repo_with_uninitialized_submodule(
    repo_with_nested_submodules: git.Repo,
) -> git.Repo:
    """Create a test setup with an uninitialized submodule.

    Simulates the state after a fresh clone without --recursive.
    """
    main_repo = repo_with_nested_submodules
    # Get the 'submodule' submodule by name
    submodule = next(s for s in main_repo.submodules if s.name == "submodule")
    submodule_path = Path(main_repo.working_dir) / submodule.path

    if submodule_path.exists():
        shutil.rmtree(submodule_path)
    submodule_path.mkdir()  # Git expects empty directory
    return main_repo
