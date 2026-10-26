# SPDX-License-Identifier: GPL-3.0-only
import importlib.metadata
import importlib.util as imputil
import sys
from pathlib import Path


def _get_app_name() -> str:
    """
    Get main application name.

    Handles invocations via all supported CLI script names. Falls back to using the name of the
    installed package (e.g. during pytest invocations) or a hardcoded value if spec is not
    available (to satisfy mypy).
    """
    if (name := Path(sys.argv[0]).name) not in ("cachi2", "hermeto"):
        spec = imputil.find_spec(__name__)
        name = spec.name if spec else "hermeto"
    return name


def _get_app_version() -> str:
    """Get the version of the installed hermeto distribution."""
    try:
        return importlib.metadata.version("hermeto")
    except importlib.metadata.PackageNotFoundError:
        # Both CLI entry points only exist once hermeto is installed, so this can only happen when
        # importing hermeto from a source tree that was never installed (e.g. via PYTHONPATH).
        # Don't let missing metadata break every import of the package in that case.
        return "unknown"


APP_NAME = _get_app_name()
APP_VERSION = _get_app_version()
