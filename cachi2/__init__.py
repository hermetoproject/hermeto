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
    if (name := Path(sys.argv[0]).name) not in ("cachi2",):
        spec = imputil.find_spec(__name__)
        name = spec.name if spec else "cachi2"
    return name


APP_NAME = _get_app_name()
