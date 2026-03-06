# SPDX-License-Identifier: GPL-3.0-only
from typing import Any
from unittest import mock

import pytest

from hermeto.core.package_managers.npm.models import Package, PackageLock
from hermeto.core.rooted_path import RootedPath


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
