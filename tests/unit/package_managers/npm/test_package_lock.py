# SPDX-License-Identifier: GPL-3.0-only
import urllib.parse
from typing import Any
from unittest import mock

import pytest
from packageurl import PackageURL

from hermeto.core.errors import UnexpectedFormat
from hermeto.core.package_managers.npm import (
    NormalizedUrl,
    Package,
    PackageLock,
    _extract_git_info_npm,
    _Purlifier,
    _update_vcs_url_with_full_hostname,
)
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF

from .conftest import MOCK_REPO_VCS_URL


class TestPackage:
    @pytest.mark.parametrize(
        "package, expected_resolved_url",
        [
            pytest.param(
                Package(
                    "foo",
                    "",
                    {
                        "version": "1.0.0",
                        "resolved": "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                    },
                ),
                "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                id="registry_dependency",
            ),
            pytest.param(
                Package(
                    "foo",
                    "node_modules/foo",
                    {
                        "version": "1.0.0",
                        "resolved": "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                    },
                ),
                "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                id="package",
            ),
            pytest.param(
                Package(
                    "foo",
                    "foo",
                    {
                        "version": "1.0.0",
                    },
                ),
                "file:foo",
                id="workspace_package",
            ),
            pytest.param(
                Package(
                    "foo",
                    "node_modules/bar/node_modules/foo",
                    {
                        "version": "1.0.0",
                        "inBundle": True,
                    },
                ),
                None,
                id="bundled_package",
            ),
            pytest.param(
                Package(
                    "foo",
                    "node_modules/foo",
                    {
                        "version": "1.0.0",
                        "resolved": "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                        # direct bundled dependency, should be treated as not bundled (it's not
                        # bundled in the source repo, but would be bundled via `npm pack .`)
                        "inBundle": True,
                    },
                ),
                "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                id="directly_bundled_package",
            ),
        ],
    )
    def test_get_resolved_url(self, package: Package, expected_resolved_url: str) -> None:
        assert package.resolved_url == expected_resolved_url

    @pytest.mark.parametrize(
        "package, expected_version, expected_resolved_url",
        [
            pytest.param(
                Package(
                    "foo",
                    "",
                    {
                        "version": "1.0.0",
                        "resolved": "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                    },
                ),
                "1.0.0",
                "file:///foo-1.0.0.tgz",
                id="registry_dependency",
            ),
            pytest.param(
                Package(
                    "foo",
                    "node_modules/foo",
                    {
                        "version": "1.0.0",
                        "resolved": "https://some.registry.org/foo/-/foo-1.0.0.tgz",
                    },
                ),
                "1.0.0",
                "file:///foo-1.0.0.tgz",
                id="package",
            ),
            pytest.param(
                Package(
                    "foo",
                    "foo",
                    {
                        # version omitted for workspace/unpublished package
                        "resolved": "file:foo",
                    },
                ),
                None,
                "file:///foo-1.0.0.tgz",
                id="workspace_no_version",
            ),
        ],
    )
    def test_set_resolved_url(
        self, package: Package, expected_version: str | None, expected_resolved_url: str
    ) -> None:
        package.resolved_url = "file:///foo-1.0.0.tgz"
        assert package.version == expected_version
        assert package.resolved_url == expected_resolved_url

    def test_eq(self) -> None:
        assert Package("foo", "", {}) == Package("foo", "", {})
        assert Package("foo", "", {}) != Package("bar", "", {})
        assert 1 != Package("foo", "", {})


class TestPackageLock:
    @pytest.mark.parametrize(
        "resolved_url, lockfile_data, expected_result",
        [
            pytest.param(
                "bar",
                {
                    "packages": {
                        "": {"workspaces": ["foo"], "version": "1.0.0"},
                    }
                },
                False,
                id="missing_package_in_workspaces",
            ),
            pytest.param(
                "foo",
                {
                    "packages": {
                        "": {"version": "1.0.0"},
                    }
                },
                False,
                id="missing_workspaces",
            ),
            pytest.param(
                "foo",
                {
                    "packages": {
                        "": {
                            "workspaces": ["foo", "./bar", "spam-packages/spam", "eggs-packages/*"],
                        }
                    },
                },
                True,
                id="exact_match_package_in_workspace",
            ),
            pytest.param(
                "bar",
                {
                    "packages": {
                        "": {
                            "workspaces": ["foo", "./bar", "spam-packages/spam", "eggs-packages/*"]
                        }
                    },
                },
                True,
                id="compare_package_with_slash_in_workspace",
            ),
            pytest.param(
                "spam-packages/spam",
                {
                    "packages": {
                        "": {
                            "workspaces": ["foo", "./bar", "spam-packages/spam", "eggs-packages/*"]
                        }
                    },
                },
                True,
                id="workspace_with_subdirectory",
            ),
            pytest.param(
                "eggs-packages/eggs",
                {
                    "packages": {
                        "": {
                            "workspaces": ["foo", "./bar", "spam-packages/spam", "eggs-packages/*"]
                        }
                    },
                },
                True,
                id="anything_in_subdirectory",
            ),
        ],
    )
    def test_check_if_package_is_workspace(
        self,
        rooted_tmp_path: RootedPath,
        resolved_url: str,
        lockfile_data: dict[str, Any],
        expected_result: bool,
    ) -> None:
        package_lock = PackageLock(rooted_tmp_path, lockfile_data)
        assert package_lock._check_if_package_is_workspace(resolved_url) == expected_result

    @pytest.mark.parametrize(
        "lockfile_data, expected_result",
        [
            pytest.param(
                {
                    "lockfileVersion": 2,
                    "packages": {
                        "": {"workspaces": ["foo"], "version": "1.0.0"},
                        "node_modules/foo": {"version": "1.0.0", "resolved": "foo"},
                        "node_modules/bar": {"version": "2.0.0", "resolved": "bar"},
                        "node_modules/@yolo/baz": {
                            "version": "0.16.3",
                            "resolved": "https://registry.foo.org/@yolo/baz/-/baz-0.16.3.tgz",
                            "integrity": "sha512-YOLO8888",
                        },
                        "node_modules/git-repo": {
                            "version": "2.0.0",
                            "resolved": "git+ssh://git@foo.org/foo-namespace/git-repo.git#YOLO1234",
                        },
                        "node_modules/https-tgz": {
                            "version": "3.0.0",
                            "resolved": "https://gitfoo.com/https-namespace/https-tgz/raw/tarball/https-tgz-3.0.0.tgz",
                            "integrity": "sha512-YOLO-4321",
                        },
                        # Check that file dependency wil be ignored
                        "node_modules/file-foo": {
                            "version": "4.0.0",
                            "resolved": "file://file-foo",
                        },
                    },
                },
                {
                    "foo": {
                        "version": "1.0.0",
                        "name": "foo",
                        "integrity": None,
                    },
                    "bar": {
                        "version": "2.0.0",
                        "name": "bar",
                        "integrity": None,
                    },
                    "https://registry.foo.org/@yolo/baz/-/baz-0.16.3.tgz": {
                        "version": "0.16.3",
                        "name": "@yolo/baz",
                        "integrity": "sha512-YOLO8888",
                    },
                    "git+ssh://git@foo.org/foo-namespace/git-repo.git#YOLO1234": {
                        "version": "2.0.0",
                        "name": "git-repo",
                        "integrity": None,
                    },
                    "https://gitfoo.com/https-namespace/https-tgz/raw/tarball/https-tgz-3.0.0.tgz": {
                        "version": "3.0.0",
                        "name": "https-tgz",
                        "integrity": "sha512-YOLO-4321",
                    },
                },
                id="get_dependencies",
            ),
        ],
    )
    def test_get_dependencies_to_download(
        self,
        rooted_tmp_path: RootedPath,
        lockfile_data: dict[str, Any],
        expected_result: bool,
    ) -> None:
        package_lock = PackageLock(rooted_tmp_path, lockfile_data)
        assert package_lock.get_dependencies_to_download() == expected_result

    @pytest.mark.parametrize(
        "lockfile_data, expected_packages, expected_workspaces, expected_main_package",
        [
            pytest.param(
                {},
                [],
                [],
                Package("", "", {}),
                id="no_packages",
            ),
            # We test here intentionally unexpected format of package-lock.json (resolved is a
            # directory path but there's no link -> it would not happen in package-lock.json)
            # to see if collecting workspaces works as expected.
            pytest.param(
                {
                    "packages": {
                        "": {"name": "npm_test", "workspaces": ["foo"], "version": "1.0.0"},
                        "node_modules/foo": {"version": "1.0.0", "resolved": "foo"},
                        "node_modules/bar": {"version": "2.0.0", "resolved": "bar"},
                    }
                },
                [
                    Package("foo", "node_modules/foo", {"version": "1.0.0", "resolved": "foo"}),
                    Package("bar", "node_modules/bar", {"version": "2.0.0", "resolved": "bar"}),
                ],
                [],
                Package(
                    "npm_test", "", {"name": "npm_test", "version": "1.0.0", "workspaces": ["foo"]}
                ),
                id="normal_packages",
            ),
            pytest.param(
                {
                    "packages": {
                        "": {"name": "npm_test", "workspaces": ["not-foo"], "version": "1.0.0"},
                        "foo": {"version": "1.0.0", "resolved": "foo"},
                        "node_modules/foo": {"link": True, "resolved": "not-foo"},
                    }
                },
                [
                    Package("foo", "foo", {"version": "1.0.0", "resolved": "foo"}),
                ],
                ["not-foo"],
                Package(
                    "npm_test",
                    "",
                    {"name": "npm_test", "version": "1.0.0", "workspaces": ["not-foo"]},
                ),
                id="workspace_link",
            ),
            pytest.param(
                {
                    "packages": {
                        "": {"name": "npm_test", "version": "1.0.0"},
                        "foo": {"name": "not-foo", "version": "1.0.0", "resolved": "foo"},
                        "node_modules/not-foo": {"link": True, "resolved": "not-foo"},
                    }
                },
                [
                    Package(
                        "not-foo", "foo", {"name": "not-foo", "version": "1.0.0", "resolved": "foo"}
                    ),
                ],
                [],
                Package("npm_test", "", {"name": "npm_test", "version": "1.0.0"}),
                id="workspace_different_name",
            ),
            pytest.param(
                {
                    "packages": {
                        "": {"name": "npm_test", "version": "1.0.0"},
                        "node_modules/@foo/bar": {"version": "1.0.0", "resolved": "@foo/bar"},
                    }
                },
                [
                    Package(
                        "@foo/bar",
                        "node_modules/@foo/bar",
                        {"version": "1.0.0", "resolved": "@foo/bar"},
                    ),
                ],
                [],
                Package("npm_test", "", {"name": "npm_test", "version": "1.0.0"}),
                id="group_package",
            ),
        ],
    )
    def test_get_packages(
        self,
        rooted_tmp_path: RootedPath,
        lockfile_data: dict[str, Any],
        expected_packages: list[Package],
        expected_workspaces: list[str],
        expected_main_package: Package,
    ) -> None:
        package_lock = PackageLock(rooted_tmp_path, lockfile_data)
        assert package_lock._packages == expected_packages
        assert package_lock.workspaces == expected_workspaces
        assert package_lock.main_package == expected_main_package

    def test_get_sbom_components(self) -> None:
        mock_package_lock = mock.Mock()
        mock_package_lock.get_sbom_components = PackageLock.get_sbom_components
        mock_package_lock.lockfile_version = 2
        mock_package_lock._packages = [
            Package("foo", "node_modules/foo", {"version": "1.0.0"}),
        ]
        mock_package_lock._dependencies = [
            Package("bar", "", {"version": "2.0.0"}),
        ]

        components = mock_package_lock.get_sbom_components(mock_package_lock)
        names = {component["name"] for component in components}
        assert names == {"foo"}


def urlq(url: str) -> str:
    return urllib.parse.quote(url, safe=":/")


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


@pytest.mark.parametrize(
    "vcs, expected",
    [
        (
            (f"git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git#{GIT_REF}"),
            {
                "url": "ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git",
                "ref": GIT_REF,
                "host": "bitbucket.org",
                "namespace": "cachi-testing",
                "repo": "cachi2-without-deps",
            },
        ),
    ],
)
def test_extract_git_info_npm(vcs: NormalizedUrl, expected: dict[str, str]) -> None:
    assert _extract_git_info_npm(vcs) == expected


def test_extract_git_info_with_missing_ref() -> None:
    vcs = NormalizedUrl("git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git")
    expected_error = (
        "ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git "
        "is not valid VCS url. ref is missing."
    )
    with pytest.raises(UnexpectedFormat, match=expected_error):
        _extract_git_info_npm(vcs)


@pytest.mark.parametrize(
    "vcs, expected",
    [
        (
            "github:kevva/is-positive#97edff6",
            "git+ssh://git@github.com/kevva/is-positive.git#97edff6",
        ),
        ("github:kevva/is-positive", "git+ssh://git@github.com/kevva/is-positive.git"),
        (
            "bitbucket:cachi-testing/cachi2-without-deps#9e164b9",
            "git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps.git#9e164b9",
        ),
        ("gitlab:foo/bar#YOLO", "git+ssh://git@gitlab.com/foo/bar.git#YOLO"),
    ],
)
def test_update_vcs_url_with_full_hostname(vcs: str, expected: str) -> None:
    assert _update_vcs_url_with_full_hostname(vcs) == expected
