# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from typing import Any, Dict, Optional

import requests
from requests import Session
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# The set is extended version of constant Retry.DEFAULT_ALLOWED_METHODS
# with PATCH and POST methods included.
ALL_REQUEST_METHODS = frozenset(
    {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS", "TRACE"}
)
# The set includes only methods which don't modify state of the service.
SAFE_REQUEST_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
DEFAULT_RETRY_OPTIONS: Dict[str, Any] = {
    "total": 5,
    "read": 5,
    "connect": 5,
    "backoff_factor": 1.3,
    "status_forcelist": (500, 502, 503, 504),
}


def get_requests_session(retry_options: Optional[dict] = None) -> Session:
    """
    Create a requests session with retries.

    :param dict retry_options: overwrite options for initialization of Retry instance
    :return: the configured requests session
    :rtype: requests.Session
    """
    if retry_options is None:
        retry_options = {}
    session = requests.Session()
    retry_options = {**DEFAULT_RETRY_OPTIONS, **retry_options}
    adapter = requests.adapters.HTTPAdapter(max_retries=Retry(**retry_options))
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
