# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import random
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import MagicMock

import aiohttp
import aiohttp_retry
import pytest
import requests
from requests.auth import AuthBase, HTTPBasicAuth

from hermeto.core.config import get_config
from hermeto.core.errors import FetchError, NotAGitRepo, UnsupportedFeature
from hermeto.core.package_managers import general
from hermeto.core.package_managers.general import (
    _async_download_binary_file,
    async_download_files,
    download_binary_file,
    get_vcs_qualifiers,
    pkg_requests_session,
)
from hermeto.core.scm import RepoID
from hermeto.core.type_aliases import StrPath
from tests.common_utils import GIT_REF


@pytest.mark.parametrize("auth", [None, HTTPBasicAuth("user", "password")])
@pytest.mark.parametrize("insecure", [True, False])
@pytest.mark.parametrize("chunk_size", [1024, 2048])
@mock.patch.object(pkg_requests_session, "get")
def test_download_binary_file(
    mock_get: Any, auth: AuthBase | None, insecure: bool, chunk_size: int, tmp_path: Path
) -> None:
    config = get_config()
    timeout = (config.http.connect_timeout, config.http.read_timeout)
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


@pytest.mark.parametrize(
    "url, nonstandard_info",  # See body of function for what is standard info
    [
        (
            # Standard case
            f"git+https://github.com/monty/python@{GIT_REF}",
            None,
        ),
        (
            # Ref should be converted to lowercase
            f"git+https://github.com/monty/python@{GIT_REF.upper()}",
            {"ref": GIT_REF},  # Standard but be explicit about it
        ),
        (
            # Repo ends with .git (that is okay)
            f"git+https://github.com/monty/python.git@{GIT_REF}",
            {"url": "https://github.com/monty/python.git"},
        ),
        (
            # git://
            f"git://github.com/monty/python@{GIT_REF}",
            {"url": "git://github.com/monty/python"},
        ),
        (
            # git+git://
            f"git+git://github.com/monty/python@{GIT_REF}",
            {"url": "git://github.com/monty/python"},
        ),
        (
            # No namespace
            f"git+https://github.com/python@{GIT_REF}",
            {"url": "https://github.com/python", "namespace": ""},
        ),
        (
            # Namespace with more parts
            f"git+https://github.com/monty/python/and/the/holy/grail@{GIT_REF}",
            {
                "url": "https://github.com/monty/python/and/the/holy/grail",
                "namespace": "monty/python/and/the/holy",
                "repo": "grail",
            },
        ),
        (
            # Port should be part of host
            f"git+https://github.com:443/monty/python@{GIT_REF}",
            {"url": "https://github.com:443/monty/python", "host": "github.com:443"},
        ),
        (
            # Authentication should not be part of host
            f"git+https://user:password@github.com/monty/python@{GIT_REF}",
            {
                "url": "https://user:password@github.com/monty/python",
                "host": "github.com",  # Standard but be explicit about it
            },
        ),
        (
            # Params, query and fragment should be stripped
            f"git+https://github.com/monty/python@{GIT_REF};foo=bar?bar=baz#egg=spam",
            {
                # Standard but be explicit about it
                "url": "https://github.com/monty/python",
            },
        ),
        (
            # RubyGems case
            f"https://github.com/monty/python@{GIT_REF}",
            {
                # Standard but be explicit about it
                "url": "https://github.com/monty/python",
            },
        ),
    ],
)
def test_extract_git_info(url: str, nonstandard_info: Any) -> None:
    """Test extraction of git info from VCS URL."""
    info = {
        "url": "https://github.com/monty/python",
        "ref": GIT_REF,
        "namespace": "monty",
        "repo": "python",
        "host": "github.com",
    }
    info.update(nonstandard_info or {})
    assert general.extract_git_info(url) == info


@pytest.mark.asyncio
async def test_async_download_binary_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = "http://example.com/file.tar"
    download_path = tmp_path / "file.tar"

    class MockReadChunk:
        def __init__(self) -> None:
            """Create a call count."""
            self.call_count = 0

        async def read_chunk(self, size: int) -> bytes:
            """Return a non-empty chunk for the first and second call, then an empty chunk."""
            self.call_count += 1
            chunks = {1: b"first_chunk-", 2: b"second_chunk-"}
            return chunks.get(self.call_count, b"")

    response, session = MagicMock(), MagicMock()
    response.content.read = MockReadChunk().read_chunk

    async def mock_aenter() -> MagicMock:
        return response

    session.get().__aenter__.side_effect = mock_aenter

    await _async_download_binary_file(session, url, download_path, ssl_context=None)

    with open(download_path, "rb") as f:
        assert f.read() == b"first_chunk-second_chunk-"

    assert session.get.called
    assert session.get.call_args == mock.call(
        url,
        timeout=aiohttp.ClientTimeout(total=None, connect=30, sock_read=300),
        auth=None,
        raise_for_status=True,
        ssl=None,
    )


@pytest.mark.asyncio
async def test_async_download_binary_file_exception(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = "http://example.com/file.tar"
    download_path = tmp_path / "file.tar"

    session = MagicMock()

    exception_message = "This is a test exception message."
    session.get().__aenter__.side_effect = Exception(exception_message)

    with pytest.raises(FetchError) as exc_info:
        await _async_download_binary_file(session, url, download_path)

    assert f"Unsuccessful download: {url}" in caplog.text
    assert str(exc_info.value) == f"exception_name: Exception, details: {exception_message}"


@pytest.mark.asyncio
@mock.patch("hermeto.core.package_managers.general._async_download_binary_file")
async def test_async_download_files(
    mock_download_file: MagicMock,
    tmp_path: Path,
) -> None:
    def mock_async_download_binary_file() -> MagicMock:
        async def mock_download_binary_file(
            session: aiohttp_retry.RetryClient,
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

    files_to_download: dict[str, StrPath] = {
        "file1": str(tmp_path / "path1"),
        "file2": str(tmp_path / "path2"),
        "file3": str(tmp_path / "path3"),
    }

    concurrency_limit = 2

    mock_download_file.return_value = mock_async_download_binary_file

    await async_download_files(files_to_download, concurrency_limit)

    assert mock_download_file.call_count == 3

    # Assert that mock_download_file was called with the correct arguments
    for call in mock_download_file.mock_calls:
        _, file, path = call.args
        assert file, path in files_to_download.items()


@pytest.mark.asyncio
async def test_async_download_files_exception(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = "http://example.com/file.tar"
    download_path = tmp_path / "file.tar"

    session = MagicMock()

    exception_message = "This is a test exception message."
    session.get().__aenter__.side_effect = Exception(exception_message)

    with pytest.raises(FetchError) as exc_info:
        await _async_download_binary_file(session, url, download_path)

    assert f"Unsuccessful download: {url}" in caplog.text
    assert str(exc_info.value) == f"exception_name: Exception, details: {exception_message}"


class TestGetVcsQualifiers:
    """Tests for the get_vcs_qualifiers helper function."""

    @mock.patch("hermeto.core.package_managers.general.get_repo_id")
    def test_returns_vcs_url_dict(self, mock_get_repo_id: mock.Mock, tmp_path: Path) -> None:
        """Test that valid git repo returns proper qualifiers dict."""
        mock_repo_id = RepoID(origin_url="https://github.com/org/repo", commit_id=GIT_REF)
        mock_get_repo_id.return_value = mock_repo_id

        result = get_vcs_qualifiers(tmp_path)

        assert result == {"vcs_url": f"git+https://github.com/org/repo@{GIT_REF}"}
        mock_get_repo_id.assert_called_once_with(tmp_path)

    @mock.patch("hermeto.core.package_managers.general.get_repo_id")
    def test_raises_not_a_git_repo(self, mock_get_repo_id: mock.Mock, tmp_path: Path) -> None:
        """Test that NotAGitRepo is raised for non-git directories."""
        mock_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")

        with pytest.raises(NotAGitRepo):
            get_vcs_qualifiers(tmp_path)

    @mock.patch("hermeto.core.package_managers.general.get_repo_id")
    def test_raises_unsupported_feature(self, mock_get_repo_id: mock.Mock, tmp_path: Path) -> None:
        """Test that UnsupportedFeature is raised for repos without origin remote."""
        mock_get_repo_id.side_effect = UnsupportedFeature(
            "Cannot process repos without origin", solution="Add origin remote"
        )

        with pytest.raises(UnsupportedFeature):
            get_vcs_qualifiers(tmp_path)
