# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path

from hermeto.core.type_aliases import StrPath

GIT_REF = "f" * 40


class Symlink(str):
    """
    Use this to create symlinks via write_file_tree().

    The value of a Symlink instance is the target path (path to make a symlink to).
    """


def write_file_tree(tree_def: dict, rooted_at: StrPath, exist_ok: bool = False) -> None:
    """
    Write a file tree to disk.

    :param tree_def: Definition of file tree, see usage for intuitive examples
    :param rooted_at: Root of file tree, must be an existing directory
    :param exist_ok: If True, existing directories will not cause this function to fail
    """
    root = Path(rooted_at)
    for entry, value in tree_def.items():
        entry_path = root / entry
        if isinstance(value, Symlink):
            os.symlink(value, entry_path)
        elif isinstance(value, str):
            entry_path.write_text(value)
        else:
            entry_path.mkdir(exist_ok=exist_ok)
            write_file_tree(value, entry_path)
