# SPDX-License-Identifier: GPL-3.0-only
import json
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from packageurl import PackageURL

from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, PackageRejected
from hermeto.core.package_managers.javascript.npm.project import Package, PackageLock, _Purlifier
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID

MOCK_REPO_ID = RepoID("https://github.com/foolish/bar.git", "abcdef1234")
MOCK_REPO_VCS_URL = "git%2Bhttps://github.com/foolish/bar.git%40abcdef1234"


@pytest.fixture
def mock_get_repo_id() -> Iterator[mock.Mock]:
    with mock.patch(
        "hermeto.core.package_managers.javascript.npm.project.get_repo_id"
    ) as mocked_get_repo_id:
        mocked_get_repo_id.return_value = MOCK_REPO_ID
        yield mocked_get_repo_id


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

    @pytest.mark.parametrize(
        "context_path, dep_name, packages_data, expected_path",
        [
            pytest.param(
                "node_modules/express",
                "qs",
                {"node_modules/qs": {"version": "1.0.0"}},
                "node_modules/qs",
                id="hoisted_dep",
            ),
            pytest.param(
                "node_modules/express",
                "qs",
                {
                    "node_modules/express/node_modules/qs": {"version": "2.0.0"},
                    "node_modules/qs": {"version": "1.0.0"},
                },
                "node_modules/express/node_modules/qs",
                id="nested_dep_preferred_over_hoisted",
            ),
            pytest.param(
                "client",
                "yup",
                {"client/node_modules/yup": {"version": "1.0.0"}},
                "client/node_modules/yup",
                id="workspace_local_dep",
            ),
            pytest.param(
                "client",
                "react",
                {"node_modules/react": {"version": "18.0.0"}},
                "node_modules/react",
                id="workspace_hoisted_dep",
            ),
            pytest.param(
                "node_modules/foo",
                "nonexistent",
                {},
                None,
                id="not_found",
            ),
        ],
    )
    def test_resolve_dep_path(
        self,
        context_path: str,
        dep_name: str,
        packages_data: dict[str, Any],
        expected_path: str | None,
    ) -> None:
        assert PackageLock._resolve_dep_path(context_path, dep_name, packages_data) == expected_path

    def _make_workspace_lockfile_data(self) -> dict[str, Any]:
        """Return lockfile data for workspace filter tests."""
        return {
            "packages": {
                "": {
                    "name": "my-app",
                    "version": "1.0.0",
                    "workspaces": ["client", "server"],
                    "dependencies": {"shared-lib": "^1.0.0"},
                },
                "client": {"version": "1.0.0"},
                "server": {"version": "1.0.0"},
                "node_modules/client": {"link": True, "resolved": "client"},
                "node_modules/server": {"link": True, "resolved": "server"},
                "node_modules/shared-lib": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/shared-lib/-/shared-lib-1.0.0.tgz",
                    "dependencies": {"shared-dep": "^1.0.0"},
                },
                "node_modules/shared-dep": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/shared-dep/-/shared-dep-1.0.0.tgz",
                },
                "node_modules/client-only": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/client-only/-/client-only-1.0.0.tgz",
                    "dependencies": {"client-transitive": "^1.0.0"},
                },
                "node_modules/client-transitive": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/ct/-/ct-1.0.0.tgz",
                },
                "node_modules/server-only": {
                    "version": "2.0.0",
                    "resolved": "https://registry.npmjs.org/server-only/-/server-only-2.0.0.tgz",
                    "dependencies": {"server-transitive": "^1.0.0"},
                },
                "node_modules/server-transitive": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/st/-/st-1.0.0.tgz",
                },
                "client/node_modules/client-local": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/cl/-/cl-1.0.0.tgz",
                },
            },
        }

    def _write_workspace_package_jsons(self, root: Path) -> None:
        """Create workspace package.json files on disk for filter tests."""
        client_dir = root / "client"
        client_dir.mkdir(exist_ok=True)
        (client_dir / "package.json").write_text(
            json.dumps(
                {
                    "name": "client",
                    "version": "1.0.0",
                    "dependencies": {"client-only": "^1.0.0", "client-local": "^1.0.0"},
                }
            )
        )
        server_dir = root / "server"
        server_dir.mkdir(exist_ok=True)
        (server_dir / "package.json").write_text(
            json.dumps(
                {
                    "name": "server",
                    "version": "1.0.0",
                    "dependencies": {"server-only": "^2.0.0"},
                }
            )
        )

    def test_workspace_filter_no_filter(self, rooted_tmp_path: RootedPath) -> None:
        """Test that without a filter all packages are present."""
        lockfile_data = self._make_workspace_lockfile_data()
        self._write_workspace_package_jsons(rooted_tmp_path.path)

        pl = PackageLock(rooted_tmp_path, lockfile_data)
        all_names = {p.name for p in pl.packages}
        assert "client-only" in all_names
        assert "server-only" in all_names
        assert pl.workspaces == ["client", "server"]

    def test_workspace_filter_selected(self, rooted_tmp_path: RootedPath) -> None:
        """Test that filtering keeps only packages reachable from selected workspaces."""
        lockfile_data = self._make_workspace_lockfile_data()
        self._write_workspace_package_jsons(rooted_tmp_path.path)

        pl = PackageLock(rooted_tmp_path, lockfile_data, workspace_filter=["client"])
        filtered_names = {p.name for p in pl.packages}

        assert "shared-lib" in filtered_names
        assert "shared-dep" in filtered_names
        assert "client-only" in filtered_names
        assert "client-transitive" in filtered_names
        assert "client-local" in filtered_names
        assert "server-only" not in filtered_names
        assert "server-transitive" not in filtered_names
        assert pl.workspaces == ["client"]

    def test_workspace_filter_selected_paths(self, rooted_tmp_path: RootedPath) -> None:
        """Test that filtering excludes the unselected workspace entry."""
        lockfile_data = self._make_workspace_lockfile_data()
        self._write_workspace_package_jsons(rooted_tmp_path.path)

        pl = PackageLock(rooted_tmp_path, lockfile_data, workspace_filter=["client"])

        assert {p.path for p in pl.packages} == {
            "client",
            "node_modules/shared-lib",
            "node_modules/shared-dep",
            "node_modules/client-only",
            "node_modules/client-transitive",
            "client/node_modules/client-local",
        }

    def test_workspace_filter_from_file(self) -> None:
        """Test that from_file passes workspace_filter through to the constructor."""
        lockfile_data = {"lockfileVersion": 3, "packages": {}}
        mock_load = mock.patch(
            "hermeto.core.package_managers.javascript.npm.project._load_json_file",
            return_value=lockfile_data,
        )
        mock_init = mock.patch.object(PackageLock, "__init__", return_value=None)
        with mock_load, mock_init as init_mock:
            mock_path = mock.Mock()
            mock_path.path = Path("/fake/package-lock.json")
            PackageLock.from_file(mock_path, workspace_filter=["ws-a"])
            init_mock.assert_called_once_with(mock_path, lockfile_data, ["ws-a"])

    def test_workspace_filter_unknown_name(self, rooted_tmp_path: RootedPath) -> None:
        """Test that unknown workspace names raise PackageRejected."""
        lockfile_data: dict[str, Any] = {
            "packages": {
                "": {"name": "my-app", "version": "1.0.0", "workspaces": ["ws-a"]},
                "ws-a": {"version": "1.0.0"},
                "node_modules/ws-a": {"link": True, "resolved": "ws-a"},
            },
        }
        with pytest.raises(PackageRejected):
            PackageLock(rooted_tmp_path, lockfile_data, workspace_filter=["typo"])

    def test_workspace_filter_includes_workspace_entry(self, rooted_tmp_path: RootedPath) -> None:
        """Test that the workspace package itself is kept in the filtered result."""
        lockfile_data: dict[str, Any] = {
            "packages": {
                "": {"name": "my-app", "version": "1.0.0", "workspaces": ["client"]},
                "client": {"version": "1.0.0", "name": "client"},
                "node_modules/client": {"link": True, "resolved": "client"},
                "node_modules/dep-a": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/dep-a/-/dep-a-1.0.0.tgz",
                },
            },
        }
        client_dir = rooted_tmp_path.path / "client"
        client_dir.mkdir()
        (client_dir / "package.json").write_text(
            json.dumps({"name": "client", "version": "1.0.0", "dependencies": {"dep-a": "^1.0.0"}})
        )

        pl = PackageLock(rooted_tmp_path, lockfile_data, workspace_filter=["client"])
        filtered_paths = {p.path for p in pl.packages}
        assert "client" in filtered_paths

    def test_workspace_filter_follows_links(self, rooted_tmp_path: RootedPath) -> None:
        """Test that inter-workspace deps are followed through link entries."""
        lockfile_data: dict[str, Any] = {
            "packages": {
                "": {
                    "name": "my-app",
                    "version": "1.0.0",
                    "workspaces": ["client", "common"],
                },
                "client": {"version": "1.0.0"},
                "common": {"version": "1.0.0"},
                "node_modules/client": {"link": True, "resolved": "client"},
                "node_modules/common": {"link": True, "resolved": "common"},
                "node_modules/common-dep": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/common-dep/-/common-dep-1.0.0.tgz",
                },
            },
        }
        client_dir = rooted_tmp_path.path / "client"
        client_dir.mkdir()
        (client_dir / "package.json").write_text(
            json.dumps({"name": "client", "version": "1.0.0", "dependencies": {"common": "^1.0.0"}})
        )
        common_dir = rooted_tmp_path.path / "common"
        common_dir.mkdir()
        (common_dir / "package.json").write_text(
            json.dumps(
                {"name": "common", "version": "1.0.0", "dependencies": {"common-dep": "^1.0.0"}}
            )
        )

        # Only select client — common's deps should still be followed via the link
        pl = PackageLock(rooted_tmp_path, lockfile_data, workspace_filter=["client"])
        filtered_names = {p.name for p in pl.packages}
        assert filtered_names == {"client", "common", "common-dep"}

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
        "main_pkg_subpath, pkg_data, expect_purl",
        [
            (
                ".",
                ("main-pkg", None, "file:."),
                "pkg:npm/main-pkg",
            ),
            (
                "subpath",
                ("main-pkg", None, "file:."),
                "pkg:npm/main-pkg#subpath",
            ),
            (
                ".",
                ("main-pkg", "1.0.0", "file:."),
                "pkg:npm/main-pkg@1.0.0",
            ),
            (
                "subpath",
                ("file-dep", "1.0.0", "file:packages/foo"),
                "pkg:npm/file-dep@1.0.0#subpath/packages/foo",
            ),
        ],
    )
    def test_get_purl_for_file_permissive_mode_returns_PackageURL_without_vcs_url(
        self,
        main_pkg_subpath: str,
        pkg_data: tuple[str, str | None, str],
        expect_purl: PackageURL,
        rooted_tmp_path: RootedPath,
    ) -> None:
        with (
            mock.patch(
                "hermeto.core.package_managers.javascript.npm.project.get_repo_id"
            ) as mock_get_repo_id,
            mock.patch(
                "hermeto.core.package_managers.javascript.npm.project.get_config"
            ) as mock_get_config,
        ):
            mock_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
            mock_get_config.return_value.mode = Mode.PERMISSIVE
            pkg_path = rooted_tmp_path.join_within_root(main_pkg_subpath)
            purl = _Purlifier(pkg_path).get_purl(*pkg_data, integrity=None)
            assert purl.to_string() == expect_purl

    def test_get_purl_for_file_package_strict_mode_raises_without_git_repo(
        self,
        rooted_tmp_path: RootedPath,
    ) -> None:
        with (
            mock.patch(
                "hermeto.core.package_managers.javascript.npm.project.get_repo_id"
            ) as mock_get_repo_id,
            mock.patch(
                "hermeto.core.package_managers.javascript.npm.project.get_config"
            ) as mock_get_config,
        ):
            mock_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
            mock_get_config.return_value.mode = Mode.STRICT
            pkg_path = rooted_tmp_path.join_within_root(".")
            with pytest.raises(NotAGitRepo):
                _Purlifier(pkg_path).get_purl("main-pkg", None, "file:.", integrity=None)

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
                ("file-dep", "1.0.0", "file:packages/foo"),
                f"pkg:npm/file-dep@1.0.0?vcs_url={MOCK_REPO_VCS_URL}#packages/foo",
            ),
        ],
    )
    def test_get_purl_for_file_permissive_mode_returns_PackageURL_with_vcs_url(
        self,
        main_pkg_subpath: str,
        pkg_data: tuple[str, str | None, str],
        expect_purl: PackageURL,
        rooted_tmp_path: RootedPath,
    ) -> None:
        with (
            mock.patch(
                "hermeto.core.package_managers.javascript.npm.project.get_repo_id"
            ) as mock_get_repo_id,
            mock.patch(
                "hermeto.core.package_managers.javascript.npm.project.get_config"
            ) as mock_get_config,
        ):
            mock_get_repo_id.return_value = MOCK_REPO_ID
            mock_get_config.return_value.mode = Mode.PERMISSIVE
            pkg_path = rooted_tmp_path.join_within_root(main_pkg_subpath)
            purl = _Purlifier(pkg_path).get_purl(*pkg_data, integrity=None)
            assert purl.qualifiers is not None
            assert purl.to_string() == expect_purl
