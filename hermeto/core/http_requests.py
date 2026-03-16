# SPDX-License-Identifier: GPL-3.0-or-later
import email.utils
import logging
import time
from typing import Any

import requests
from aiohttp import ClientResponse
from aiohttp_retry import JitterRetry
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
DEFAULT_RETRY_OPTIONS: dict[str, Any] = {
    "total": 5,
    "read": 5,
    "connect": 5,
    "backoff_factor": 1.3,
    "status_forcelist": (429, 500, 502, 503, 504),
}


class RetryAfterJitterRetry(JitterRetry):
    """JitterRetry that uses the Retry-After header value when present."""

    def get_timeout(
        self,
        attempt: int,
        response: ClientResponse | None = None,
    ) -> float:
        """Return the retry delay, preferring the Retry-After header when present."""
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                parsed = self._parse_retry_after(retry_after)
                if parsed is not None and parsed > 0:
                    timeout = min(parsed, self._max_timeout)
                    log.debug("Using Retry-After header value: %s seconds", timeout)
                    return timeout

        return super().get_timeout(attempt, response)

    @staticmethod
    def _parse_retry_after(retry_after: str) -> float | None:
        """Parse a Retry-After header value into seconds, or None if unparseable."""
        retry_after = retry_after.strip()
        if not retry_after:
            return None

        # Try integer seconds first (most common for 429 responses)
        try:
            return float(int(retry_after))
        except ValueError:
            pass

        # Try HTTP-date format
        parsed_date = email.utils.parsedate_tz(retry_after)
        if parsed_date is not None:
            retry_timestamp = email.utils.mktime_tz(parsed_date)
            return max(0.0, retry_timestamp - time.time())

        return None


def get_requests_session(retry_options: dict | None = None) -> Session:
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
