# SPDX-License-Identifier: GPL-3.0-only
import json
from collections import UserDict
from pathlib import Path
from typing import Any

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound


class PackageJson(UserDict):
    """Class representing package.json files."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        """Initialize a PackageJson object."""
        self.path = path
        super().__init__(data)

    @classmethod
    def from_file(cls, path: Path) -> "PackageJson":
        """Create a PackageJson object from a package.json file."""
        if not path.exists():
            raise LockfileNotFound(
                path,
                solution="Make sure the package.json file exists in the specified directory.",
            )

        try:
            with path.open("r") as f:
                data = json.load(f)
        except json.decoder.JSONDecodeError as e:
            raise InvalidLockfileFormat(
                lockfile_path=path,
                err_details=str(e),
                solution="The package.json file must contain valid JSON.",
            )

        return cls(path, data)

    @classmethod
    def from_dir(cls, path: Path) -> "PackageJson":
        """Create a PackageJson object from a package.json file."""
        return cls.from_file(path.joinpath("package.json"))

    def write(self) -> None:
        """Write the data to the package.json file."""
        with self.path.open("w") as f:
            json.dump(self.data, f, indent=2)
            f.write("\n")
