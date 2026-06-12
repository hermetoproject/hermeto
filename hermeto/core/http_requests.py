# SPDX-License-Identifier: GPL-3.0-or-later
import email.utils
import logging
import time
from typing import Any

from aiohttp import ClientResponse
from aiohttp_retry import JitterRetry

log = logging.getLogger(__name__)

# The set is extended version of constant Retry.DEFAULT_ALLOWED_METHODS
# with PATCH and POST methods included.
ALL_REQUEST_METHODS = frozenset(
    {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS", "TRACE"}
)
# The set includes only methods which don't modify state of the service.
SAFE_REQUEST_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
DEFAULT_RETRY_OPTIONS: dict[str, Any] = {
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
        default = super().get_timeout(attempt, response)

        if response is None:
            return default

        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            return default

        parsed = self._parse_retry_after(retry_after)
        if parsed is None:
            log.warning("Unparseable Retry-After header: '%s'", retry_after)
            return default
        if parsed < 0:
            log.warning("Retry-After resolved to negative delay (%.1fs): '%s'", parsed, retry_after)
            return default

        return min(parsed, self._max_timeout)

    @staticmethod
    def _parse_retry_after(retry_after: str) -> float | None:
        """Parse a Retry-After header value into seconds, or None if unparseable."""
        # Try numeric seconds first (most common for 429 responses)
        try:
            return float(retry_after)
        except ValueError:
            pass

        # Try HTTP-date format
        parsed_date = email.utils.parsedate_tz(retry_after)
        if parsed_date is not None:
            retry_timestamp = email.utils.mktime_tz(parsed_date)
            return retry_timestamp - time.time()

        return None
