# SPDX-License-Identifier: GPL-3.0-only
"""The tooling formats Hermeto can consume Python dependencies from.

This lives in the shared ``python`` package rather than the pip backend because
the lockfile formats it names are ecosystem-wide.
"""

from enum import Enum


class PythonPackagingTool(str, Enum):
    """The lockfile format a Python package's dependencies are declared in."""

    REQUIREMENTS = "requirements"
    PYLOCK = "pylock"
