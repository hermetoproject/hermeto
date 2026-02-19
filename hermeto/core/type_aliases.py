# SPDX-License-Identifier: GPL-3.0-only
import os

from semver import Version

StrPath = str | os.PathLike[str]
SemverLike = Version | str
