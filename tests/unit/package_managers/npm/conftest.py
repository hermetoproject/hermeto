# SPDX-License-Identifier: GPL-3.0-only
from collections.abc import Iterator
from unittest import mock

import pytest

from hermeto.core.models.input import Request
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID

MOCK_REPO_ID = RepoID("https://github.com/foolish/bar.git", "abcdef1234")
MOCK_REPO_VCS_URL = "git%2Bhttps://github.com/foolish/bar.git%40abcdef1234"


@pytest.fixture
def npm_request(rooted_tmp_path: RootedPath, npm_input_packages: list[dict[str, str]]) -> Request:
    # Create folder in the specified path, otherwise Request validation would fail
    for package in npm_input_packages:
        if "path" in package:
            (rooted_tmp_path.path / package["path"]).mkdir(exist_ok=True)

    return Request(
        source_dir=rooted_tmp_path,
        output_dir=rooted_tmp_path.join_within_root("output"),
        packages=npm_input_packages,
    )


@pytest.fixture
def mock_get_repo_id() -> Iterator[mock.Mock]:
    with mock.patch(
        "hermeto.core.package_managers.npm.package_lock.get_repo_id"
    ) as mocked_get_repo_id:
        mocked_get_repo_id.return_value = MOCK_REPO_ID
        yield mocked_get_repo_id
