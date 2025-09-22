import os
from typing import Union

from semver import Version

StrPath = Union[str, os.PathLike[str]]
SemverLike = Union[Version, str]
