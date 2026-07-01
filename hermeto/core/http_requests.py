# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from types import TracebackType
from typing import Any

from typing_extensions import Self
from urllib3.connectionpool import ConnectionPool
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
    "backoff_factor": 1.3,
    "status_forcelist": (500, 502, 503, 504),
}


class SyncLoggingRetry(Retry):
    """
    Retry subclass that emits a hermeto-style debug log on each retry.
    """

    def __init__(self, *args: Any, **kw: Any) -> None:
        """
        Initialize retry with total count preserved for logging.
        """
        self.total_retries = kw.get("total")
        super().__init__(*args, **kw)

    def new(self, **kw: Any) -> Self:
        """
        Propagate the original total across retry instances.
        """
        new_instance = super().new(**kw)
        new_instance.total_retries = self.total_retries
        return new_instance

    def increment(
        self,
        method: str | None = None,
        url: str | None = None,
        response: Any | None = None,
        error: Exception | None = None,
        _pool: ConnectionPool | None = None,
        _stacktrace: TracebackType | None = None,
    ) -> Self:
        """
        Log retry attempt at DEBUG level.
        """
        # retry object - Named to match urllib3.Retry internal logic.
        new_retry = super().increment(
            method=method,
            url=url,
            response=response,
            error=error,
            _pool=_pool,
            _stacktrace=_stacktrace,
        )
        retry_number = len(new_retry.history)
        status = response.status if response else "N/A"
        backoff = new_retry.get_backoff_time()
        log.debug(
            "Retrying request: retry=%d/%d url=%s status=%s backoff=%.1fs",
            retry_number,
            self.total_retries,
            url,
            status,
            backoff,
        )
        return new_retry
