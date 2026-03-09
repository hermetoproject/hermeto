# SPDX-License-Identifier: GPL-3.0-only
import json
import os
from typing import Any

import pytest

from hermeto.core.package_managers.npm import (
    NormalizedUrl,
    PackageLock,
    _should_replace_dependency,
    _update_package_json_files,
    _update_package_lock_with_local_paths,
)
from hermeto.core.rooted_path import RootedPath


@pytest.mark.parametrize(
    "dependency_version, expected_result",
    [
        ("1.0.0 - 2.9999.9999", False),
        (">=1.0.2 <2.1.2", False),
        ("2.0.1", False),
        ("<1.0.0 || >=2.3.1 <2.4.5 || >=2.5.2 <3.0.0", False),
        ("~1.2", False),
        ("3.3.x", False),
        ("latest", False),
        ("file:../dyl", False),
        ("", False),
        ("*", False),
        ("npm:somedep@^1.0.0", False),
        ("git+ssh://git@github.com:npm/cli.git#v1.0.27", True),
        ("git+ssh://git@github.com:npm/cli#semver:^5.0", True),
        ("git+https://isaacs@github.com/npm/cli.git", True),
        ("git://github.com/npm/cli.git#v1.0.27", True),
        ("git+ssh://git@github.com:npm/cli.git#v1.0.27", True),
        ("expressjs/express", True),
        ("mochajs/mocha#4727d357ea", True),
        ("user/repo#feature/branch", True),
        ("https://asdf.com/asdf.tar.gz", True),
        ("https://asdf.com/asdf.tgz", True),
    ],
)
def test_should_replace_dependency(dependency_version: str, expected_result: bool) -> None:
    assert _should_replace_dependency(dependency_version) == expected_result


@pytest.mark.parametrize(
    "lockfile_data, download_paths, expected_lockfile_data",
    [
        pytest.param(
            {
                "lockfileVersion": 2,
                "packages": {
                    "": {
                        "workspaces": ["foo", "bar"],
                        "version": "1.0.0",
                        "dependencies": {
                            "@types/zzz": "^18.0.1",
                            "hm-tarball": "https://gitfoo.com/https-namespace/hm-tgz/raw/tarball/hm-tgz-666.0.0.tgz",
                            "git-repo": "git+ssh://git@foo.org/foo-namespace/git-repo.git#5464684321",
                        },
                    },
                    "node_modules/foo": {"version": "1.0.0", "resolved": "foo", "link": True},
                    "node_modules/bar": {"version": "2.0.0", "resolved": "bar", "link": True},
                    "node_modules/@yolo/baz": {
                        "version": "0.16.3",
                        "resolved": "https://registry.foo.org/@yolo/baz/-/baz-0.16.3.tgz",
                        "integrity": "sha512-YOLO8888",
                    },
                    "node_modules/git-repo": {
                        "version": "2.0.0",
                        "resolved": "git+ssh://git@foo.org/foo-namespace/git-repo.git#YOLO1234",
                        "integrity": "SHOULD-be-removed",
                    },
                    "node_modules/https-tgz": {
                        "version": "3.0.0",
                        "resolved": "https://gitfoo.com/https-namespace/https-tgz/raw/tarball/https-tgz-3.0.0.tgz",
                        "integrity": "sha512-YOLO-4321",
                        "dependencies": {
                            "@types/zzz": "^18.0.1",
                            "hm-tarball": "https://gitfoo.com/https-namespace/hm-tgz/raw/tarball/hm-tgz-666.0.0.tgz",
                            "git-repo": "git+ssh://git@foo.org/foo-namespace/git-repo.git#5464684321",
                        },
                    },
                    # Check that file dependency wil be ignored
                    "node_modules/file-foo": {
                        "version": "4.0.0",
                        "resolved": "file://file-foo",
                    },
                },
            },
            {
                "https://registry.foo.org/@yolo/baz/-/baz-0.16.3.tgz": "deps/baz-0.16.3.tgz",
                "git+ssh://git@foo.org/foo-namespace/git-repo.git#YOLO1234": "deps/git-repo.git#YOLO1234.tgz",
                "https://gitfoo.com/https-namespace/https-tgz/raw/tarball/https-tgz-3.0.0.tgz": "deps/https-tgz-3.0.0.tgz",
            },
            {
                "lockfileVersion": 2,
                "packages": {
                    "": {
                        "workspaces": ["foo", "bar"],
                        "version": "1.0.0",
                        "dependencies": {
                            "@types/zzz": "^18.0.1",
                            "hm-tarball": "",
                            "git-repo": "",
                        },
                    },
                    "node_modules/foo": {"version": "1.0.0", "resolved": "foo", "link": True},
                    "node_modules/bar": {"version": "2.0.0", "resolved": "bar", "link": True},
                    "node_modules/@yolo/baz": {
                        "version": "0.16.3",
                        "resolved": "file://${output_dir}/deps/baz-0.16.3.tgz",
                        "integrity": "sha512-YOLO8888",
                    },
                    "node_modules/git-repo": {
                        "version": "2.0.0",
                        "resolved": "file://${output_dir}/deps/git-repo.git#YOLO1234.tgz",
                        "integrity": "",
                    },
                    "node_modules/https-tgz": {
                        "version": "3.0.0",
                        "resolved": "file://${output_dir}/deps/https-tgz-3.0.0.tgz",
                        "integrity": "sha512-YOLO-4321",
                        "dependencies": {
                            "@types/zzz": "^18.0.1",
                            "hm-tarball": "",
                            "git-repo": "",
                        },
                    },
                    # Check that file dependency wil be ignored
                    "node_modules/file-foo": {
                        "version": "4.0.0",
                        "resolved": "file://file-foo",
                    },
                },
            },
            id="update_package-lock_json",
        ),
    ],
)
def test_update_package_lock_with_local_paths(
    rooted_tmp_path: RootedPath,
    lockfile_data: dict[str, Any],
    download_paths: dict[NormalizedUrl, RootedPath],
    expected_lockfile_data: dict[str, Any],
) -> None:
    for url, download_path in download_paths.items():
        download_paths.update({url: rooted_tmp_path.join_within_root(download_path)})
    package_lock = PackageLock(rooted_tmp_path, lockfile_data)
    _update_package_lock_with_local_paths(download_paths, package_lock)
    assert package_lock.lockfile_data == expected_lockfile_data


@pytest.mark.parametrize(
    "file_data, workspaces, expected_file_data",
    [
        pytest.param(
            {
                "devDependencies": {
                    "express": "^4.18.2",
                },
                "peerDependencies": {
                    "@types/react-dom": "^18.0.1",
                },
                "bundleDependencies": {
                    "sax": "0.1.1",
                },
                "optionalDependencies": {
                    "foo-tarball": "https://foohub.com/foo-namespace/foo/raw/tarball/foo-tarball-1.0.0.tgz",
                },
                "dependencies": {
                    "debug": "",
                    "foo": "file://foo.tgz",
                    "baz-positive": "github:baz/bar",
                    "bar-deps": "https://foobucket.org/foo-namespace/bar-deps-.git",
                },
            },
            ["foo-workspace"],
            {
                # In this test case only git and https type of packages should be replaced for empty strings
                "devDependencies": {
                    "express": "^4.18.2",
                },
                "peerDependencies": {
                    "@types/react-dom": "^18.0.1",
                },
                "bundleDependencies": {
                    "sax": "0.1.1",
                },
                "optionalDependencies": {
                    "foo-tarball": "",
                },
                "dependencies": {
                    "debug": "",
                    "foo": "file://foo.tgz",
                    "baz-positive": "",
                    "bar-deps": "",
                },
            },
            id="update_package_jsons",
        ),
    ],
)
def test_update_package_json_files(
    rooted_tmp_path: RootedPath,
    file_data: dict[str, Any],
    workspaces: list[str],
    expected_file_data: dict[str, Any],
) -> None:
    # Create package.json files to check dependency update
    root_package_json = rooted_tmp_path.join_within_root("package.json")
    workspace_dir = rooted_tmp_path.join_within_root("foo-workspace")
    workspace_package_json = rooted_tmp_path.join_within_root("foo-workspace/package.json")
    with open(root_package_json.path, "w") as outfile:
        json.dump(file_data, outfile)
    os.mkdir(workspace_dir.path)
    with open(workspace_package_json.path, "w") as outfile:
        json.dump(file_data, outfile)

    package_json_projectfiles = _update_package_json_files(workspaces, rooted_tmp_path)
    for projectfile in package_json_projectfiles:
        assert json.loads(projectfile.template) == expected_file_data
