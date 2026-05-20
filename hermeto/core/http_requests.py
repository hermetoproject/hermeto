# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from typing import Any

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
