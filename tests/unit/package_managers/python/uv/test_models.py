# SPDX-License-Identifier: GPL-3.0-only
import pydantic
import pytest

from hermeto.core.errors import PackageRejected
from hermeto.core.package_managers.python.uv.models import (
    PackageSourceGit,
    PackageSourceRegistry,
)


class TestPackageSourceRegistry:
    def test_rejects_empty_index(self) -> None:
        with pytest.raises(pydantic.ValidationError, match="registry source must not be empty"):
            PackageSourceRegistry(kind="registry", location="")


class TestPackageSourceGit:
    @pytest.mark.parametrize(
        "location, expected",
        [
            pytest.param(
                "https://github.com/org/repo?tag=v1.0#0123abcd",
                "https://github.com/org/repo",
                id="query_and_fragment_stripped",
            ),
            pytest.param(
                "https://github.com/org/repo#0123abcd",
                "https://github.com/org/repo",
                id="fragment_only",
            ),
        ],
    )
    def test_clone_url(self, location: str, expected: str) -> None:
        assert PackageSourceGit(kind="git", location=location).clone_url == expected

    def test_commit(self) -> None:
        source = PackageSourceGit(
            kind="git", location="https://github.com/org/repo?rev=main#0123abcd"
        )
        assert source.commit == "0123abcd"

    def test_missing_commit(self) -> None:
        with pytest.raises(PackageRejected, match="does not pin a commit"):
            PackageSourceGit(kind="git", location="https://github.com/org/repo?rev=main")
