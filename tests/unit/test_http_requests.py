# SPDX-License-Identifier: GPL-3.0-only
import email.utils
import time
from unittest import mock

import pytest
from aiohttp import ClientResponse

from hermeto.core.http_requests import RetryAfterJitterRetry

_FALLBACK_TIMEOUT = 1.0


@pytest.fixture()
def retry() -> RetryAfterJitterRetry:
    return RetryAfterJitterRetry()


@pytest.fixture()
def mock_response() -> mock.Mock:
    resp = mock.Mock(spec=ClientResponse)
    resp.headers = {}
    return resp


@pytest.mark.parametrize(
    "retry_after, expected",
    [
        pytest.param("5", 5.0, id="integer"),
        pytest.param("0", 0.0, id="zero"),
        pytest.param("2.5", 2.5, id="float"),
    ],
)
def test_get_timeout_valid_seconds(
    retry: RetryAfterJitterRetry,
    mock_response: mock.Mock,
    retry_after: str,
    expected: float,
) -> None:
    mock_response.headers = {"Retry-After": retry_after}
    assert retry.get_timeout(attempt=1, response=mock_response) == expected


def test_get_timeout_valid_http_date(
    retry: RetryAfterJitterRetry,
    mock_response: mock.Mock,
) -> None:
    future = time.time() + 10
    mock_response.headers = {"Retry-After": email.utils.formatdate(future, usegmt=True)}
    timeout = retry.get_timeout(attempt=1, response=mock_response)
    assert timeout == pytest.approx(10, abs=1)


def test_get_timeout_capped_at_max_timeout(mock_response: mock.Mock) -> None:
    retry = RetryAfterJitterRetry(max_timeout=10.0)
    mock_response.headers = {"Retry-After": "60"}
    assert retry.get_timeout(attempt=1, response=mock_response) == 10.0


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-header"),
        pytest.param({"Retry-After": "-5"}, id="negative"),
        pytest.param({"Retry-After": "not-a-number"}, id="unparseable"),
    ],
)
@mock.patch("hermeto.core.http_requests.JitterRetry.get_timeout", return_value=_FALLBACK_TIMEOUT)
def test_get_timeout_falls_back_to_default(
    mock_get_timeout: mock.Mock,
    retry: RetryAfterJitterRetry,
    mock_response: mock.Mock,
    headers: dict[str, str],
) -> None:
    mock_response.headers = headers
    assert retry.get_timeout(attempt=1, response=mock_response) == _FALLBACK_TIMEOUT


@mock.patch("hermeto.core.http_requests.JitterRetry.get_timeout", return_value=_FALLBACK_TIMEOUT)
def test_get_timeout_no_response(mock_get_timeout: mock.Mock, retry: RetryAfterJitterRetry) -> None:
    assert retry.get_timeout(attempt=1, response=None) == _FALLBACK_TIMEOUT
