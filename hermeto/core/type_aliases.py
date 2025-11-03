import os

from semver import Version

StrPath = str | os.PathLike[str]
SemverLike = Version | str
