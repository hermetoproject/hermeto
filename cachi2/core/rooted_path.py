from os import PathLike
from pathlib import Path
from typing import Any, TypeVar, Union

from pydantic_core import CoreSchema, core_schema

from cachi2.core.errors import PathOutsideRoot

StrPath = Union[str, PathLike[str]]
RootedPathT = TypeVar("RootedPathT", bound="RootedPath")


class RootedPath(PathLike[str]):
    """A safer way to handle subpaths.

    Get a subpath, guaranteeing that it really is a subpath:

    >>> rooted_path = RootedPath("/some/directory")
    >>> rooted_path.join_within_root("..")                  # ERROR PathOutsideRoot
    >>> rooted_path.join_within_root("/abspath")            # ERROR PathOutsideRoot
    >>> rooted_path.join_within_root("symlink-to-parent")   # ERROR PathOutsideRoot

    Access the underlying Path object:

    >>> rooted_path = RootedPath("/some/directory")
    >>> rooted_path.join_within_root("vendor", "modules.txt").path.read_text()

    The join_within_root method remembers the original root. See the join_within_root
    and re_root docstrings for more details.

    Implements the PathLike interface -> most stdlib methods that accept paths will work
    with a RootedPath as well.

    Implements __get_validators__ for pydantic integration.
    """

    def __init__(self, path: StrPath) -> None:
        """Create a RootedPath.

        :param path: the path (which also becomes the root of the RootedPath)
        """
        self._root = Path(path)
        self._path = self.root
        if not self._path.is_absolute():
            raise ValueError(f"path must be absolute: {path}")

    @property
    def root(self) -> Path:
        """Get the root directory which this path is not allowed to leave."""
        return self._root

    @property
    def path(self) -> Path:
        """Get the current path, which is guaranteed to be at or below the root."""
        return self._path

    @property
    def subpath_from_root(self) -> Path:
        """Get the path relative to the root."""
        return self._path.relative_to(self._root)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RootedPath):
            # NotImplemented is a special value which should be returned by the binary special methods
            # (e.g. __eq__(), __lt__(), __add__(), __rsub__(), etc.)  to indicate that the operation is
            # not implemented with respect to the other type - https://docs.python.org/3/library/constants.html#NotImplemented
            return NotImplemented

        return self.path == other.path and self.root == other.root

    def __fspath__(self) -> str:
        return self.path.__fspath__()

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        typename = type(self).__name__
        subpath_from_root = self.path.relative_to(self.root)
        return f"<{typename} root={str(self.root)!r} subpath={str(subpath_from_root)!r}>"

    def re_root(self: RootedPathT, *other: StrPath) -> RootedPathT:
        """Safely join other path components and make the result the new root.

        >>> rooted_path = RootedPath("/some/directory")
        >>> rooted_path.re_root("subpath").join_within_root("..")   # ERROR

        :raises PathOutsideRoot: if the resulting path is not a subpath of the root
        """
        subpath = self.path.joinpath(*other).resolve()
        if not subpath.is_relative_to(self.root):
            raise PathOutsideRoot(
                s_self=str(Path(*other)),
                s_other=str(self.path),
                s_root=str(self.root),
            )
        cls = type(self)
        return cls(subpath)

    def join_within_root(self: RootedPathT, *other: StrPath) -> RootedPathT:
        """Safely join other path components but remember the original root.

        >>> rooted_path = RootedPath("/some/directory")
        >>> rooted_path.join_within_root("subpath").join_within_root("..")  # OK

        :raises PathOutsideRoot: if the resulting path is not a subpath of the root
        """
        new = self.re_root(*other)
        new._root = self.root
        return new

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> CoreSchema:
        return core_schema.no_info_before_validator_function(
            cls._validate, core_schema.any_schema()
        )

    @staticmethod
    def _validate(value: Any) -> "RootedPath":
        if not isinstance(value, (str, PathLike)):
            raise ValueError(f"expected str or os.PathLike, got {type(value).__name__}")
        return RootedPath(path=value)
