# SPDX-License-Identifier: GPL-3.0-only
from unittest import mock
from unittest.mock import MagicMock

import pytest

from hermeto.core.http_requests import DEFAULT_RETRY_OPTIONS, RetryAfterJitterRetry


def _make_retry(max_timeout: float = 30.0) -> RetryAfterJitterRetry:
    """Create a RetryAfterJitterRetry instance for testing."""
    return RetryAfterJitterRetry(
        attempts=3,
        start_timeout=0.1,
        max_timeout=max_timeout,
    )


def _make_response(retry_after: str | None) -> MagicMock:
    """Create a mock aiohttp.ClientResponse with optional Retry-After header."""
    response = MagicMock()
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    response.headers = headers
    return response


class TestRetryAfterJitterRetryGetTimeout:
    """Tests for RetryAfterJitterRetry.get_timeout."""

    def test_retry_after_integer(self) -> None:
        """Use Retry-After integer value as the timeout."""
        retry = _make_retry()
        response = _make_response("10")
        timeout = retry.get_timeout(attempt=0, response=response)
        assert timeout == 10.0

    def test_retry_after_integer_with_whitespace(self) -> None:
        """Trim whitespace from Retry-After integer value."""
        retry = _make_retry()
        response = _make_response("  5  ")
        timeout = retry.get_timeout(attempt=0, response=response)
        assert timeout == 5.0

    def test_retry_after_capped_by_max_timeout(self) -> None:
        """Retry-After value exceeding max_timeout is capped."""
        retry = _make_retry(max_timeout=10.0)
        response = _make_response("60")
        timeout = retry.get_timeout(attempt=0, response=response)
        assert timeout == 10.0

    @mock.patch("hermeto.core.http_requests.time")
    def test_retry_after_http_date(self, mock_time: MagicMock) -> None:
        """Parse Retry-After HTTP-date and compute delay from current time."""
        # Sun, 09 Sep 2001 01:46:40 GMT = 1000000000 epoch seconds
        mock_time.time.return_value = 999999990.0
        retry = _make_retry()
        response = _make_response("Sun, 09 Sep 2001 01:46:40 GMT")
        timeout = retry.get_timeout(attempt=0, response=response)
        assert timeout == pytest.approx(10.0, abs=1.0)

    @mock.patch("hermeto.core.http_requests.time")
    def test_retry_after_http_date_in_past(self, mock_time: MagicMock) -> None:
        """HTTP-date in the past yields 0.0, falling back to jitter backoff."""
        mock_time.time.return_value = 2000000000.0
        retry = _make_retry()
        response = _make_response("Sun, 09 Sep 2001 01:46:40 GMT")
        timeout = retry.get_timeout(attempt=0, response=response)
        # max(0.0, negative) = 0.0 which is not > 0, so falls back to jitter
        assert timeout > 0

    def test_retry_after_zero(self) -> None:
        """Zero Retry-After falls back to jitter backoff."""
        retry = _make_retry()
        response = _make_response("0")
        timeout = retry.get_timeout(attempt=0, response=response)
        assert timeout > 0

    def test_no_retry_after_header(self) -> None:
        """Fall back to jitter backoff when no Retry-After header is present."""
        retry = _make_retry()
        response = _make_response(None)
        timeout = retry.get_timeout(attempt=0, response=response)
        assert timeout > 0

    def test_response_none(self) -> None:
        """Fall back to jitter backoff when response is None (connection error)."""
        retry = _make_retry()
        timeout = retry.get_timeout(attempt=0, response=None)
        assert timeout > 0

    def test_retry_after_invalid_string(self) -> None:
        """Fall back to jitter on unparseable Retry-After value."""
        retry = _make_retry()
        response = _make_response("not-a-number-or-date")
        timeout = retry.get_timeout(attempt=0, response=response)
        assert timeout > 0

    def test_retry_after_empty_string(self) -> None:
        """Empty Retry-After falls back to jitter backoff."""
        retry = _make_retry()
        response = _make_response("")
        timeout = retry.get_timeout(attempt=0, response=response)
        assert timeout > 0

    def test_retry_after_float_string(self) -> None:
        """Float string like '1.5' falls back to jitter (int() rejects floats)."""
        retry = _make_retry()
        response = _make_response("1.5")
        timeout = retry.get_timeout(attempt=0, response=response)
        assert timeout > 0


class TestParseRetryAfter:
    """Tests for RetryAfterJitterRetry._parse_retry_after."""

    def test_integer_seconds(self) -> None:
        """Parse integer seconds."""
        assert RetryAfterJitterRetry._parse_retry_after("120") == 120.0

    def test_integer_with_whitespace(self) -> None:
        """Parse integer with surrounding whitespace."""
        assert RetryAfterJitterRetry._parse_retry_after("  60  ") == 60.0

    @mock.patch("hermeto.core.http_requests.time")
    def test_http_date(self, mock_time: MagicMock) -> None:
        """Parse HTTP-date into seconds from now."""
        mock_time.time.return_value = 999999990.0
        result = RetryAfterJitterRetry._parse_retry_after("Sun, 09 Sep 2001 01:46:40 GMT")
        assert result == pytest.approx(10.0, abs=1.0)

    def test_returns_none_for_garbage(self) -> None:
        """Return None for unparseable values."""
        assert RetryAfterJitterRetry._parse_retry_after("xyz") is None

    def test_returns_none_for_empty(self) -> None:
        """Return None for empty string."""
        assert RetryAfterJitterRetry._parse_retry_after("") is None

    def test_negative_integer(self) -> None:
        """Negative integer is parsed but returned as negative float."""
        assert RetryAfterJitterRetry._parse_retry_after("-5") == -5.0


class TestDefaultRetryOptions:
    """Tests for DEFAULT_RETRY_OPTIONS configuration."""

    def test_status_forcelist_includes_429(self) -> None:
        """Verify HTTP 429 (Too Many Requests) is in the retry status codes."""
        assert 429 in DEFAULT_RETRY_OPTIONS["status_forcelist"]

    def test_status_forcelist_includes_server_errors(self) -> None:
        """Verify standard server error codes are in the retry status codes."""
        for status in (500, 502, 503, 504):
            assert status in DEFAULT_RETRY_OPTIONS["status_forcelist"]
