from collections.abc import Iterable

import pypi_simple
import requests
from packaging.utils import canonicalize_version

from hermeto.core.config import get_config
from hermeto.core.errors import FetchError


def get_project_packages_from(
    index_url: str,
    name: str,
    version: str,
    auth: requests.auth.HTTPBasicAuth | None = None,
) -> Iterable[pypi_simple.DistributionPackage]:
    """Get all the project packages from the given index URL."""
    config = get_config()
    timeout = (config.http.connect_timeout, config.http.read_timeout)
    with pypi_simple.PyPISimple(index_url, auth=auth) as client:
        try:
            project_page = client.get_project_page(name, timeout)
        except (requests.RequestException, pypi_simple.NoSuchProjectError) as e:
            if (
                config.pip.proxy_url is not None
                and isinstance(e, requests.HTTPError)
                and e.response is not None
                and e.response.status_code == 401
            ):
                raise FetchError(
                    "Proxy requires authentication. "
                    "Verify that proxy URL, login and password are set correctly."
                ) from e
            raise FetchError(f"PyPI query failed: {e}") from e

    return filter(
        lambda p: (
            p.version is not None
            and canonicalize_version(p.version) == canonicalize_version(version)
        ),
        project_page.packages,
    )


def make_proxy_override(pm_name: str) -> tuple[str, str | None, str | None] | None:
    """Extract package manager related configuration from config if present."""
    config = get_config()
    pm = getattr(config, pm_name)
    proxy_url = str(pm.proxy_url) if pm.proxy_url is not None else None
    proxy_login = proxy_password = None
    if proxy_url is None:
        return None
    if pm.proxy_login and pm.proxy_password:
        proxy_password = pm.proxy_password.get_secret_value()
        proxy_login = pm.proxy_login
    return proxy_url, proxy_login, proxy_password
