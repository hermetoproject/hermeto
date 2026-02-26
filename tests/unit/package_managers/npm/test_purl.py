# SPDX-License-Identifier: GPL-3.0-only
from collections.abc import Iterator
from unittest import mock

import pytest
from packageurl import PackageURL

from hermeto.core.package_managers.npm.main import _Purlifier
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID
from tests.unit.package_managers.npm.test_npm import urlq

MOCK_REPO_ID = RepoID("https://github.com/foolish/bar.git", "abcdef1234")
MOCK_REPO_VCS_URL = "git%2Bhttps://github.com/foolish/bar.git%40abcdef1234"


@pytest.fixture
def mock_get_repo_id() -> Iterator[mock.Mock]:
    with mock.patch("hermeto.core.package_managers.npm.purl.get_repo_id") as mocked_get_repo_id:
        mocked_get_repo_id.return_value = MOCK_REPO_ID
        yield mocked_get_repo_id


class TestPurlifier:
    @pytest.mark.parametrize(
        "pkg_data, expect_purl",
        [
            (
                ("registry-dep", "1.0.0", "https://registry.npmjs.org/registry-dep-1.0.0.tgz"),
                "pkg:npm/registry-dep@1.0.0",
            ),
            (
                ("bundled-dep", "1.0.0", None),
                "pkg:npm/bundled-dep@1.0.0",
            ),
            (
                (
                    "@scoped/registry-dep",
                    "2.0.0",
                    "https://registry.npmjs.org/registry-dep-2.0.0.tgz",
                ),
                "pkg:npm/%40scoped/registry-dep@2.0.0",
            ),
            (
                (
                    "sus-registry-dep",
                    "1.0.0",
                    "https://registry.yarnpkg.com/sus-registry-dep-1.0.0.tgz",
                ),
                "pkg:npm/sus-registry-dep@1.0.0",
            ),
            (
                ("https-dep", None, "https://host.org/https-dep-1.0.0.tar.gz"),
                "pkg:npm/https-dep?download_url=https://host.org/https-dep-1.0.0.tar.gz",
            ),
            (
                ("https-dep", "1.0.0", "https://host.org/https-dep-1.0.0.tar.gz"),
                "pkg:npm/https-dep@1.0.0?download_url=https://host.org/https-dep-1.0.0.tar.gz",
            ),
            (
                ("http-dep", None, "http://host.org/http-dep-1.0.0.tar.gz"),
                "pkg:npm/http-dep?download_url=http://host.org/http-dep-1.0.0.tar.gz",
            ),
            (
                ("http-dep", "1.0.0", "http://host.org/http-dep-1.0.0.tar.gz"),
                "pkg:npm/http-dep@1.0.0?download_url=http://host.org/http-dep-1.0.0.tar.gz",
            ),
            (
                ("git-dep", None, "git://github.com/org/git-dep.git#deadbeef"),
                f"pkg:npm/git-dep?vcs_url={urlq('git+git://github.com/org/git-dep.git@deadbeef')}",
            ),
            (
                ("git-dep", "1.0.0", "git://github.com/org/git-dep.git#deadbeef"),
                f"pkg:npm/git-dep@1.0.0?vcs_url={urlq('git+git://github.com/org/git-dep.git@deadbeef')}",
            ),
            (
                ("gitplus-dep", None, "git+https://github.com/org/git-dep.git#deadbeef"),
                f"pkg:npm/gitplus-dep?vcs_url={urlq('git+https://github.com/org/git-dep.git@deadbeef')}",
            ),
            (
                ("github-dep", None, "github:org/git-dep#deadbeef"),
                f"pkg:npm/github-dep?vcs_url={urlq('git+ssh://git@github.com/org/git-dep.git@deadbeef')}",
            ),
            (
                ("gitlab-dep", None, "gitlab:org/git-dep#deadbeef"),
                f"pkg:npm/gitlab-dep?vcs_url={urlq('git+ssh://git@gitlab.com/org/git-dep.git@deadbeef')}",
            ),
            (
                ("bitbucket-dep", None, "bitbucket:org/git-dep#deadbeef"),
                f"pkg:npm/bitbucket-dep?vcs_url={urlq('git+ssh://git@bitbucket.org/org/git-dep.git@deadbeef')}",
            ),
        ],
    )
    def test_get_purl_for_remote_package(
        self,
        pkg_data: tuple[str, str | None, str | None],
        expect_purl: str,
        rooted_tmp_path: RootedPath,
    ) -> None:
        purl = _Purlifier(rooted_tmp_path).get_purl(*pkg_data, integrity=None)
        assert purl.to_string() == expect_purl

    @pytest.mark.parametrize(
        "main_pkg_subpath, pkg_data, expect_purl",
        [
            (
                ".",
                ("main-pkg", None, "file:."),
                f"pkg:npm/main-pkg?vcs_url={MOCK_REPO_VCS_URL}",
            ),
            (
                "subpath",
                ("main-pkg", None, "file:."),
                f"pkg:npm/main-pkg?vcs_url={MOCK_REPO_VCS_URL}#subpath",
            ),
            (
                ".",
                ("main-pkg", "1.0.0", "file:."),
                f"pkg:npm/main-pkg@1.0.0?vcs_url={MOCK_REPO_VCS_URL}",
            ),
            (
                "subpath",
                ("main-pkg", "2.0.0", "file:."),
                f"pkg:npm/main-pkg@2.0.0?vcs_url={MOCK_REPO_VCS_URL}#subpath",
            ),
            (
                ".",
                ("file-dep", "1.0.0", "file:packages/foo"),
                f"pkg:npm/file-dep@1.0.0?vcs_url={MOCK_REPO_VCS_URL}#packages/foo",
            ),
            (
                "subpath",
                ("file-dep", "1.0.0", "file:packages/foo"),
                f"pkg:npm/file-dep@1.0.0?vcs_url={MOCK_REPO_VCS_URL}#subpath/packages/foo",
            ),
            (
                "subpath",
                ("parent-is-file-dep", "1.0.0", "file:.."),
                f"pkg:npm/parent-is-file-dep@1.0.0?vcs_url={MOCK_REPO_VCS_URL}",
            ),
            (
                "subpath",
                ("nephew-is-file-dep", "1.0.0", "file:../packages/foo"),
                f"pkg:npm/nephew-is-file-dep@1.0.0?vcs_url={MOCK_REPO_VCS_URL}#packages/foo",
            ),
        ],
    )
    def test_get_purl_for_local_package(
        self,
        main_pkg_subpath: str,
        pkg_data: tuple[str, str | None, str],
        expect_purl: PackageURL,
        rooted_tmp_path: RootedPath,
        mock_get_repo_id: mock.Mock,
    ) -> None:
        pkg_path = rooted_tmp_path.join_within_root(main_pkg_subpath)
        purl = _Purlifier(pkg_path).get_purl(*pkg_data, integrity=None)
        assert purl.to_string() == expect_purl
        mock_get_repo_id.assert_called_once_with(rooted_tmp_path.root)

    @pytest.mark.parametrize(
        "resolved_url, integrity, expect_checksum_qualifier",
        [
            # integrity ignored for registry deps
            ("https://registry.npmjs.org/registry-dep-1.0.0.tgz", "sha512-3q2+7w==", None),
            # as well as git deps, if they somehow have it
            ("git+https://github.com/foo/bar.git#deeadbeef", "sha512-3q2+7w==", None),
            # and file deps
            ("file:foo.tar.gz", "sha512-3q2+7w==", None),
            # checksum qualifier added for http(s) deps
            ("https://foohub.com/foo.tar.gz", "sha512-3q2+7w==", "sha512:deadbeef"),
            # unless integrity is missing
            ("https://foohub.com/foo.tar.gz", None, None),
        ],
    )
    def test_get_purl_integrity_handling(
        self,
        resolved_url: str,
        integrity: str | None,
        expect_checksum_qualifier: str | None,
        mock_get_repo_id: mock.Mock,
    ) -> None:
        purl = _Purlifier(RootedPath("/foo")).get_purl("foo", None, resolved_url, integrity)
        assert isinstance(purl.qualifiers, dict)
        assert purl.qualifiers.get("checksum") == expect_checksum_qualifier
