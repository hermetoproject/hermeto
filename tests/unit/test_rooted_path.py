# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Literal

import pydantic
import pytest

from hermeto.core.rooted_path import PathOutsideRoot, RootedPath


@pytest.fixture
def test_path(tmp_path: Path) -> Path:
    tmp_path.joinpath("symlink-to-parent").symlink_to("..")
    tmp_path.joinpath("subpath").mkdir()
    tmp_path.joinpath("subpath/symlink-to-parent").symlink_to("..")
    tmp_path.joinpath("subpath/symlink-to-abspath").symlink_to("/abspath")
    return tmp_path


def assert_attrs(rooted_path: RootedPath, *, path: Path, root: Path) -> None:
    assert rooted_path.path == path
    assert rooted_path.root == root


def test_path_must_be_absolute() -> None:
    with pytest.raises(ValueError):
        RootedPath("foo")


def test_rooted_path_init() -> None:
    rooted_path = RootedPath("/some/directory")
    assert_attrs(rooted_path, path=Path("/some/directory"), root=Path("/some/directory"))


def test_join_within_root(test_path: Path) -> None:
    rooted_path = RootedPath(test_path)

    assert_attrs(
        rooted_path.join_within_root("nonexistent-subpath"),
        path=test_path / "nonexistent-subpath",
        root=test_path,
    )
    assert_attrs(
        rooted_path.join_within_root("nonexistent-subpath/.."),
        path=test_path,
        root=test_path,
    )
    assert_attrs(
        rooted_path.join_within_root("nonexistent-subpath", ".."),
        path=test_path,
        root=test_path,
    )
    assert_attrs(
        rooted_path.join_within_root("nonexistent-subpath").join_within_root(".."),
        path=test_path,
        root=test_path,
    )
    assert_attrs(
        rooted_path.join_within_root("subpath").join_within_root("symlink-to-parent"),
        path=test_path,
        root=test_path,
    )


def test_re_root(test_path: Path) -> None:
    rooted_path = RootedPath(test_path)

    assert_attrs(
        rooted_path.re_root("subpath"),
        path=test_path / "subpath",
        root=test_path / "subpath",
    )
    assert_attrs(
        rooted_path.re_root("nonexistent-subpath"),
        path=test_path / "nonexistent-subpath",
        root=test_path / "nonexistent-subpath",
    )


@pytest.mark.parametrize("join_method", ["re_root", "join_within_root"])
def test_dont_leave_root(
    join_method: Literal["re_root", "join_within_root"], test_path: Path
) -> None:
    rooted_path = RootedPath(test_path)

    if join_method == "re_root":
        join = RootedPath.re_root
    else:
        join = RootedPath.join_within_root

    # root/..
    with pytest.raises(PathOutsideRoot):
        join(rooted_path, "..")

    # root/symlink-to-parent
    with pytest.raises(PathOutsideRoot):
        join(rooted_path, "symlink-to-parent")

    # root/subpath/../..
    with pytest.raises(PathOutsideRoot):
        join(rooted_path.join_within_root("subpath"), "../..")

    # root/subpath/symlink-to-abspath
    with pytest.raises(PathOutsideRoot):
        join(rooted_path.join_within_root("subpath"), "symlink-to-abspath")

    # root/ /abspath
    with pytest.raises(PathOutsideRoot):
        join(rooted_path, "/abspath")

    # (root/subpath)/..
    with pytest.raises(PathOutsideRoot):
        join(rooted_path.re_root("subpath"), "..")


def test_rooted_path_eq() -> None:
    assert RootedPath("/some/directory") == RootedPath("/some/directory")
    assert RootedPath("/some/directory").re_root("subpath") == RootedPath("/some/directory/subpath")

    a = RootedPath("/some/directory").join_within_root("subpath")
    assert a != RootedPath("/some/directory")
    assert a != RootedPath("/some/directory/subpath")
    assert a == RootedPath("/some/directory").join_within_root("subpath")


@pytest.mark.parametrize(
    "attr",
    [
        pytest.param("name", id="name"),
        pytest.param("stem", id="stem"),
        pytest.param("suffix", id="suffix"),
        pytest.param("exists", id="exists"),
        pytest.param("is_dir", id="is_dir"),
        pytest.param("is_file", id="is_file"),
        pytest.param("as_posix", id="as_posix"),
    ],
)
def test_passthrough_matches_inner_path(test_path: Path, attr: str) -> None:
    rp = RootedPath(test_path)
    rp_val = getattr(rp, attr)
    path_val = getattr(test_path, attr)
    if callable(rp_val):
        assert rp_val() == path_val()
    else:
        assert rp_val == path_val


def test_relative_to(test_path: Path) -> None:
    rp = RootedPath(test_path).join_within_root("subpath")
    assert rp.relative_to(test_path) == Path("subpath")
    assert rp.is_relative_to(test_path)


def test_pydantic_integration() -> None:
    class SomeModel(pydantic.BaseModel):
        path: RootedPath

    x = SomeModel.model_validate({"path": "/foo"})
    assert isinstance(x.path, RootedPath)
    assert_attrs(x.path, root=Path("/foo"), path=Path("/foo"))

    with pytest.raises(pydantic.ValidationError, match="expected str or os.PathLike, got bytes"):
        SomeModel.model_validate({"path": b"/foo"})

    with pytest.raises(pydantic.ValidationError, match="path must be absolute: foo/bar"):
        SomeModel.model_validate({"path": "foo/bar"})
