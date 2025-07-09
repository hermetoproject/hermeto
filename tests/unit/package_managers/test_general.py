# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import random
from os import PathLike
from pathlib import Path
from typing import Any, Optional, Union
from unittest import mock
from unittest.mock import MagicMock

import pytest
import requests
from requests.auth import AuthBase, HTTPBasicAuth

from hermeto.core.config import get_config
from hermeto.core.errors import FetchError
from hermeto.core.package_managers.general import (
    _async_download_binary_file,
    async_download_files,
    download_binary_file,
    pkg_requests_session,
)


@pytest.mark.parametrize("auth", [None, HTTPBasicAuth("user", "password")])
@pytest.mark.parametrize("insecure", [True, False])
@pytest.mark.parametrize("chunk_size", [1024, 2048])
@mock.patch.object(pkg_requests_session, "get")
def test_download_binary_file(
    mock_get: Any, auth: Optional[AuthBase], insecure: bool, chunk_size: int, tmp_path: Path
) -> None:
    timeout = get_config().requests_timeout
    url = "http://example.org/example.tar.gz"
    content = b"file content"

    mock_response = mock_get.return_value
    mock_response.iter_content.return_value = [content]

    download_path = tmp_path.joinpath("example.tar.gz")
    download_binary_file(
        url, str(download_path), auth=auth, insecure=insecure, chunk_size=chunk_size
    )

    assert download_path.read_bytes() == content
    mock_get.assert_called_with(url, stream=True, verify=not insecure, auth=auth, timeout=timeout)
    mock_response.iter_content.assert_called_with(chunk_size=chunk_size)


@mock.patch.object(pkg_requests_session, "get")
def test_download_binary_file_failed(mock_get: Any) -> None:
    mock_get.side_effect = [requests.RequestException("Something went wrong")]

    expected = "Could not download http://example.org/example.tar.gz: Something went wrong"
    with pytest.raises(FetchError, match=expected):
        download_binary_file("http://example.org/example.tar.gz", "/example.tar.gz")


@pytest.mark.asyncio
async def test_async_download_binary_file_exception(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = "http://example.com/file.tar"
    download_path = tmp_path / "file.tar"

    session = MagicMock()

    exception_message = "This is a test exception message."
    session.get().__aenter__.side_effect = Exception(exception_message)

    with pytest.raises(FetchError):
        await _async_download_binary_file(session, url, download_path)


@pytest.mark.asyncio
@mock.patch("hermeto.core.package_managers.general._async_download_binary_file")
async def test_async_download_files(
    mock_download_file: MagicMock,
    tmp_path: Path,
) -> None:
    def mock_async_download_binary_file() -> MagicMock:
        async def mock_download_binary_file(
            url: str,
            download_path: str,
        ) -> dict[str, str]:
            # Simulate a file download by sleeping for a random duration
            await asyncio.sleep(random.uniform(0.1, 0.5))

            # Write some dummy data to the download path
            with open(download_path, "wb") as file:
                file.write(b"Mock file content")

            # Return a dummy response indicating success
            return {"status": "success", "url": url, "download_path": download_path}

        return MagicMock(side_effect=mock_download_binary_file)

    files_to_download: dict[str, Union[str, PathLike[str]]] = {
        "file1": str(tmp_path / "path1"),
        "file2": str(tmp_path / "path2"),
        "file3": str(tmp_path / "path3"),
    }

    mock_download_file.return_value = mock_async_download_binary_file

    await async_download_files(files_to_download)

    assert mock_download_file.call_count == 3


@pytest.mark.asyncio
async def test_async_download_files_exception(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = "http://example.com/file.tar"
    download_path = tmp_path / "file.tar"

    session = MagicMock()

    exception_message = "This is a test exception message."
    session.get().__aenter__.side_effect = Exception(exception_message)

    with pytest.raises(FetchError):
        await _async_download_binary_file(session, url, download_path)
