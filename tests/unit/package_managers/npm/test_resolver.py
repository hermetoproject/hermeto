# SPDX-License-Identifier: GPL-3.0-only
import json
import os
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import NpmSettings
from hermeto.core.errors import LockfileNotFound, PackageRejected, UnsupportedFeature
from hermeto.core.models.output import ProjectFile
from hermeto.core.package_managers.npm.project import PackageLock
from hermeto.core.package_managers.npm.resolver import (
    _clone_repo_pack_archive,
    _get_npm_dependencies,
    _resolve_npm,
    _should_replace_dependency,
    _update_package_json_files,
    _update_package_lock_with_local_paths,
)
from hermeto.core.package_managers.npm.utils import NormalizedUrl
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID

MOCK_REPO_ID = RepoID("https://github.com/foolish/bar.git", "abcdef1234")
MOCK_REPO_VCS_URL = "git%2Bhttps://github.com/foolish/bar.git%40abcdef1234"


@pytest.fixture
def mock_get_repo_id() -> Iterator[mock.Mock]:
    with mock.patch("hermeto.core.package_managers.npm.project.get_repo_id") as mocked_get_repo_id:
        mocked_get_repo_id.return_value = MOCK_REPO_ID
        yield mocked_get_repo_id


def urlq(url: str) -> str:
    return urllib.parse.quote(url, safe=":/")


@pytest.mark.parametrize(
    "lockfile_exists, node_mods_exists, expected_error, expected_exception",
    [
        pytest.param(
            False,
            False,
            "Required files not found:",
            LockfileNotFound,
            id="no lockfile present",
        ),
        pytest.param(
            True,
            True,
            "The 'node_modules' directory cannot be present in the source repository",
            PackageRejected,
            id="lockfile present; node_modules present",
        ),
    ],
)
@mock.patch("pathlib.Path.exists")
def test_resolve_npm_validation(
    mock_exists: mock.Mock,
    lockfile_exists: bool,
    node_mods_exists: bool,
    expected_error: str,
    expected_exception: type[PackageRejected],
    rooted_tmp_path: RootedPath,
) -> None:
    mock_exists.side_effect = [lockfile_exists, node_mods_exists]
    npm_deps_dir = mock.Mock(spec=RootedPath)
    with pytest.raises(expected_exception, match=expected_error):
        _resolve_npm(rooted_tmp_path, npm_deps_dir)


@pytest.mark.parametrize(
    "main_pkg_subpath, package_lock_json, expected_output",
    [
        pytest.param(
            ".",
            {
                "name": "foo",
                "version": "1.0.0",
                "lockfileVersion": 2,
                "packages": {
                    "": {
                        "name": "foo",
                        "version": "1.0.0",
                        "dependencies": {"bar": "^2.0.0"},
                    },
                    "node_modules/bar": {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/bar/-/bar-2.0.0.tgz",
                        "integrity": "sha512-JCB8C6SnDoQf",
                    },
                },
                "dependencies": {
                    "bar": {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/bar/-/bar-2.0.0.tgz",
                    },
                },
            },
            {
                "package": {
                    "name": "foo",
                    "version": "1.0.0",
                    "purl": f"pkg:npm/foo@1.0.0?vcs_url={MOCK_REPO_VCS_URL}",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                "dependencies": [
                    {
                        "name": "bar",
                        "version": "2.0.0",
                        "purl": "pkg:npm/bar@2.0.0",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,  # correct since integrity is missing from dependencies but is included in packages section
                        "external_refs": None,
                    }
                ],
                "projectfiles": [
                    ProjectFile(abspath="/some/path", template="some text"),
                    ProjectFile(abspath="/some/other/path", template="some other text"),
                ],
            },
            id="npm_v2_lockfile",
        ),
        pytest.param(
            ".",
            {
                "name": "foo",
                "version": "1.0.0",
                "lockfileVersion": 2,
                "packages": {
                    "": {
                        "name": "foo",
                        "version": "1.0.0",
                        "dependencies": {"bar": "^2.0.0"},
                    },
                    "node_modules/bar": {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/bar/-/bar-2.0.0.tgz",
                        "integrity": "sha512-JCB8C6SnDoQf",
                    },
                    "node_modules/bar/node_modules/baz": {
                        "version": "3.0.0",
                        "resolved": "https://registry.npmjs.org/baz/-/baz-3.0.0.tgz",
                        "integrity": "sha512-YOLOYOLO",
                    },
                    "node_modules/bar/node_modules/spam": {
                        "version": "4.0.0",
                        "inBundle": True,
                    },
                },
                "dependencies": {
                    "bar": {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/bar/-/bar-2.0.0.tgz",
                        "dependencies": {
                            "baz": {
                                "version": "3.0.0",
                                "resolved": "https://registry.npmjs.org/baz/-/baz-3.0.0.tgz",
                            },
                            "spam": {
                                "version": "4.0.0",
                                "bundled": True,
                            },
                        },
                    },
                },
            },
            {
                "package": {
                    "name": "foo",
                    "version": "1.0.0",
                    "purl": f"pkg:npm/foo@1.0.0?vcs_url={MOCK_REPO_VCS_URL}",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                "dependencies": [
                    {
                        "name": "bar",
                        "version": "2.0.0",
                        "purl": "pkg:npm/bar@2.0.0",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    },
                    {
                        "name": "baz",
                        "version": "3.0.0",
                        "purl": "pkg:npm/baz@3.0.0",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    },
                    {
                        "name": "spam",
                        "version": "4.0.0",
                        "purl": "pkg:npm/spam@4.0.0",
                        "bundled": True,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    },
                ],
                "projectfiles": [
                    ProjectFile(abspath="/some/path", template="some text"),
                    ProjectFile(abspath="/some/other/path", template="some other text"),
                ],
            },
            id="npm_v2_lockfile_nested_deps",
        ),
        pytest.param(
            ".",
            {
                "name": "foo",
                "version": "1.0.0",
                "lockfileVersion": 2,
                "packages": {
                    "": {
                        "name": "foo",
                        "version": "1.0.0",
                        "workspaces": ["bar"],
                    },
                    "bar": {
                        "name": "not-bar",
                        "version": "2.0.0",
                    },
                    "node_modules/not-bar": {"resolved": "bar", "link": True},
                },
                "dependencies": {
                    "not-bar": {
                        "version": "file:bar",
                    },
                },
            },
            {
                "package": {
                    "name": "foo",
                    "version": "1.0.0",
                    "purl": f"pkg:npm/foo@1.0.0?vcs_url={MOCK_REPO_VCS_URL}",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                "dependencies": [
                    {
                        "name": "not-bar",
                        "version": "2.0.0",
                        "purl": f"pkg:npm/not-bar@2.0.0?vcs_url={MOCK_REPO_VCS_URL}#bar",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    }
                ],
                "projectfiles": [
                    ProjectFile(abspath="/some/path", template="some text"),
                    ProjectFile(abspath="/some/other/path", template="some other text"),
                ],
            },
            id="npm_v2_lockfile_workspace",
        ),
        pytest.param(
            "subpath",
            {
                "name": "foo",
                "version": "1.0.0",
                "lockfileVersion": 2,
                "packages": {
                    "": {
                        "name": "foo",
                        "version": "1.0.0",
                        "workspaces": ["bar"],
                    },
                    "bar": {
                        "name": "not-bar",
                        "version": "2.0.0",
                    },
                    "node_modules/not-bar": {"resolved": "bar", "link": True},
                },
                "dependencies": {
                    "not-bar": {
                        "version": "file:bar",
                    },
                },
            },
            {
                "package": {
                    "name": "foo",
                    "version": "1.0.0",
                    "purl": f"pkg:npm/foo@1.0.0?vcs_url={MOCK_REPO_VCS_URL}#subpath",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                "dependencies": [
                    {
                        "name": "not-bar",
                        "version": "2.0.0",
                        "purl": f"pkg:npm/not-bar@2.0.0?vcs_url={MOCK_REPO_VCS_URL}#subpath/bar",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    }
                ],
                "projectfiles": [
                    ProjectFile(abspath="/some/path", template="some text"),
                    ProjectFile(abspath="/some/other/path", template="some other text"),
                ],
            },
            id="npm_v2_at_subpath_with_workspace",
        ),
        pytest.param(
            ".",
            {
                "name": "foo",
                "version": "1.0.0",
                "lockfileVersion": 2,
                "packages": {
                    "": {
                        "name": "foo",
                        "version": "1.0.0",
                    },
                    "node_modules/bar": {
                        "version": "2.0.0",
                        "resolved": "https://foohub.org/bar/-/bar-2.0.0.tgz",
                        "integrity": "sha512-JCB8C6SnDoQf",
                    },
                    "node_modules/spam": {
                        "version": "3.0.0",
                        "resolved": "git+ssh://git@github.com/spam/spam.git#deadbeef",
                    },
                },
                "get_list_of_workspaces": [],
                "dependencies": {
                    "bar": {
                        "version": "https://foohub.org/bar/-/bar-2.0.0.tgz",
                        "integrity": "sha512-JCB8C6SnDoQf",
                    },
                    "spam": {
                        "version": "git+ssh://git@github.com/spam/spam.git#deadbeef",
                    },
                },
            },
            {
                "package": {
                    "name": "foo",
                    "version": "1.0.0",
                    "purl": f"pkg:npm/foo@1.0.0?vcs_url={MOCK_REPO_VCS_URL}",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                "dependencies": [
                    {
                        "name": "bar",
                        "version": "2.0.0",
                        "purl": "pkg:npm/bar@2.0.0?checksum=sha512:24207c0ba4a70e841f&download_url=https://foohub.org/bar/-/bar-2.0.0.tgz",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    },
                    {
                        "name": "spam",
                        "version": "3.0.0",
                        "purl": f"pkg:npm/spam@3.0.0?vcs_url={urlq('git+ssh://git@github.com/spam/spam.git@deadbeef')}",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    },
                ],
                "projectfiles": [
                    ProjectFile(abspath="/some/path", template="some text"),
                    ProjectFile(abspath="/some/other/path", template="some other text"),
                ],
            },
            id="npm_v2_lockfile_non_registry_deps",
        ),
        pytest.param(
            ".",
            {
                "name": "foo",
                "version": "1.0.0",
                "lockfileVersion": 2,
                "packages": {
                    "": {
                        "name": "foo",
                        "version": "1.0.0",
                        "dependencies": {"@bar/baz": "^2.0.0"},
                    },
                    "node_modules/@bar/baz": {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/@bar/baz/-/baz-2.0.0.tgz",
                    },
                },
                "dependencies": {
                    "@bar/baz": {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/@bar/baz/-/baz-2.0.0.tgz",
                        "integrity": "sha512-JCB8C6SnDoQf",
                    },
                },
            },
            {
                "package": {
                    "name": "foo",
                    "version": "1.0.0",
                    "purl": f"pkg:npm/foo@1.0.0?vcs_url={MOCK_REPO_VCS_URL}",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                "dependencies": [
                    {
                        "name": "@bar/baz",
                        "version": "2.0.0",
                        "purl": "pkg:npm/%40bar/baz@2.0.0",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": Path("package-lock.json"),
                        "external_refs": None,
                    }
                ],
                "projectfiles": [
                    ProjectFile(abspath="/some/path", template="some text"),
                    ProjectFile(abspath="/some/other/path", template="some other text"),
                ],
            },
            id="npm_v2_lockfile_grouped_deps",
        ),
        pytest.param(
            ".",
            {
                "name": "foo",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "packages": {
                    "": {
                        "name": "foo",
                        "version": "1.0.0",
                        "dependencies": {"bar": "^2.0.0"},
                    },
                    "node_modules/bar": {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/bar/-/bar-2.0.0.tgz",
                    },
                },
            },
            {
                "package": {
                    "name": "foo",
                    "version": "1.0.0",
                    "purl": f"pkg:npm/foo@1.0.0?vcs_url={MOCK_REPO_VCS_URL}",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                "dependencies": [
                    {
                        "name": "bar",
                        "version": "2.0.0",
                        "purl": "pkg:npm/bar@2.0.0",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": Path("package-lock.json"),
                        "external_refs": None,
                    }
                ],
                "projectfiles": [
                    ProjectFile(abspath="/some/path", template="some text"),
                    ProjectFile(abspath="/some/other/path", template="some other text"),
                ],
            },
            id="npm_v3_lockfile",
        ),
        pytest.param(
            ".",
            {
                "name": "foo",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "packages": {
                    "": {
                        "name": "foo",
                        "version": "1.0.0",
                        "dependencies": {"bar": "^2.0.0"},
                    },
                    "node_modules/bar": {
                        "version": "2.0.0",
                        "resolved": "https://registry.npmjs.org/bar/-/bar-2.0.0.tgz",
                    },
                    "node_modules/baz": {
                        "version": "4.2.3",
                        "resolved": "file:baz-4.2.3.tgz",
                        "license": "MIT",
                    },
                    "node_modules/spam": {
                        "version": "3.1.0",
                        "resolved": "git+ssh://git@github.com/spamming/spam.git#97edff6f525f192a3f83cea1944765f769ae2678",
                    },
                    "node_modules/eggs": {
                        "version": "1.0.0",
                        "resolved": "https://github.com/omelette/ham/raw/tarball/eggs-1.0.0.tgz",
                    },
                },
            },
            {
                "package": {
                    "name": "foo",
                    "version": "1.0.0",
                    "purl": f"pkg:npm/foo@1.0.0?vcs_url={MOCK_REPO_VCS_URL}",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                "dependencies": [
                    {
                        "name": "bar",
                        "version": "2.0.0",
                        "purl": "pkg:npm/bar@2.0.0",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": Path("package-lock.json"),
                        "external_refs": None,
                    },
                    {
                        "name": "baz",
                        "version": "4.2.3",
                        "purl": "pkg:npm/baz@4.2.3?vcs_url=git%2Bhttps://github.com/foolish/bar.git%40abcdef1234#baz-4.2.3.tgz",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    },
                    {
                        "name": "spam",
                        "version": "3.1.0",
                        "purl": "pkg:npm/spam@3.1.0?vcs_url=git%2Bssh://git%40github.com/spamming/spam.git%4097edff6f525f192a3f83cea1944765f769ae2678",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    },
                    {
                        "name": "eggs",
                        "version": "1.0.0",
                        "purl": "pkg:npm/eggs@1.0.0?download_url=https://github.com/omelette/ham/raw/tarball/eggs-1.0.0.tgz",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": Path("package-lock.json"),
                        "external_refs": None,
                    },
                ],
                "projectfiles": [
                    ProjectFile(abspath="/some/path", template="some text"),
                    ProjectFile(abspath="/some/other/path", template="some other text"),
                ],
            },
            id="npm_v3_missing_hash",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.npm.resolver._get_npm_dependencies")
@mock.patch("hermeto.core.package_managers.npm.resolver._update_package_lock_with_local_paths")
@mock.patch("hermeto.core.package_managers.npm.resolver._update_package_json_files")
def test_resolve_npm(
    update_package_json_files: mock.Mock,
    update_package_lock_with_local_paths: mock.Mock,
    mock_get_npm_dependencies: mock.Mock,
    rooted_tmp_path: RootedPath,
    main_pkg_subpath: str,
    package_lock_json: dict[str, str | dict],
    expected_output: dict[str, Any],
    mock_get_repo_id: mock.Mock,
) -> None:
    """Test _resolve_npm with different package-lock.json inputs."""
    pkg_dir = rooted_tmp_path.join_within_root(main_pkg_subpath)
    pkg_dir.path.mkdir(exist_ok=True)

    lockfile_path = pkg_dir.join_within_root("package-lock.json").path
    with lockfile_path.open("w") as f:
        json.dump(package_lock_json, f)

    output_dir = rooted_tmp_path.join_within_root("output")
    npm_deps_dir = output_dir.join_within_root("deps", "npm")

    # Mock package.json files
    update_package_json_files.return_value = [
        ProjectFile(abspath="/some/path", template="some text"),
        ProjectFile(abspath="/some/other/path", template="some other text"),
    ]

    pkg_info = _resolve_npm(pkg_dir, npm_deps_dir)
    expected_output["projectfiles"].append(
        ProjectFile(
            abspath=lockfile_path.resolve(), template=json.dumps(package_lock_json, indent=2) + "\n"
        )
    )

    mock_get_npm_dependencies.assert_called()
    update_package_lock_with_local_paths.assert_called()
    update_package_json_files.assert_called()

    assert pkg_info == expected_output
    mock_get_repo_id.assert_called_once_with(rooted_tmp_path.root)


def test_resolve_npm_unsupported_lockfileversion(rooted_tmp_path: RootedPath) -> None:
    """Test _resolve_npm with unsupported lockfileVersion."""
    package_lock_json = {
        "name": "foo",
        "version": "1.0.0",
        "lockfileVersion": 4,
    }
    lockfile_path = rooted_tmp_path.path / "package-lock.json"
    with lockfile_path.open("w") as f:
        json.dump(package_lock_json, f)

    expected_error = f"lockfileVersion {package_lock_json['lockfileVersion']} from {lockfile_path} is not supported"
    npm_deps_dir = mock.Mock(spec=RootedPath)
    with pytest.raises(UnsupportedFeature, match=expected_error):
        _resolve_npm(rooted_tmp_path, npm_deps_dir)


@mock.patch("hermeto.core.package_managers.npm.resolver.clone_as_tarball")
def test_clone_repo_pack_archive(
    mock_clone_as_tarball: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    vcs = NormalizedUrl("git+ssh://bitbucket.org/cachi-testing/cachi2-without-deps.git#9e164b9")
    download_path = _clone_repo_pack_archive(vcs, rooted_tmp_path)
    expected_path = rooted_tmp_path.join_within_root(
        "bitbucket.org",
        "cachi-testing",
        "cachi2-without-deps",
        "cachi2-without-deps-external-gitcommit-9e164b9.tgz",
    )
    assert download_path.path.parent.is_dir()
    mock_clone_as_tarball.assert_called_once_with(
        "ssh://bitbucket.org/cachi-testing/cachi2-without-deps.git", "9e164b9", expected_path.path
    )


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


@pytest.mark.parametrize(
    "deps_to_download, expected_download_subpaths",
    [
        (
            {
                "https://github.com/hermetoproject/ms-1.0.0.tgz": {
                    "name": "ms",
                    "version": "1.0.0",
                    "integrity": "sha512-YOLO1111==",
                },
                # Test handling package with the same name but different version and integrity
                "https://github.com/hermetoproject/ms-2.0.0.tgz": {
                    "name": "ms",
                    "version": "2.0.0",
                    "integrity": "sha512-YOLO2222==",
                },
                "https://registry.npmjs.org/@types/react-dom/-/react-dom-18.0.11.tgz": {
                    "name": "@types/react-dom",
                    "version": "18.0.11",
                    "integrity": "sha512-YOLO00000==",
                },
                "https://registry.yarnpkg.com/abbrev/-/abbrev-2.0.0.tgz": {
                    "name": "abbrev",
                    "version": "2.0.0",
                    "integrity": "sha512-YOLO33333==",
                },
                "git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps-second.git#09992d418fc44a2895b7a9ff27c4e32d6f74a982": {
                    "version": "2.0.0",
                    "name": "cachi2-without-deps-second",
                },
                # Test short representation of git reference
                "git+ssh://git@github.com/kevva/is-positive.git#97edff6f": {
                    "integrity": "sha512-8ND1j3y9YOLO==",
                    "name": "is-positive",
                },
                # The name of the package is different from the repo name, we expect the result archive to have the repo name in it
                "git+ssh://git@gitlab.foo.bar.com/osbs/hermetoproject/integration-tests.git#c300503": {
                    "integrity": "sha512-FOOOOOOOOOYOLO==",
                    "name": "gitlab-hermeto-npm-without-deps-second",
                },
            },
            {
                "https://github.com/hermetoproject/ms-1.0.0.tgz": "external-ms/ms-external-sha256-YOLO1111.tgz",
                "https://github.com/hermetoproject/ms-2.0.0.tgz": "external-ms/ms-external-sha256-YOLO2222.tgz",
                "git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps-second.git#09992d418fc44a2895b7a9ff27c4e32d6f74a982": "bitbucket.org/cachi-testing/cachi2-without-deps-second/cachi2-without-deps-second-external-gitcommit-09992d418fc44a2895b7a9ff27c4e32d6f74a982.tgz",
                "https://registry.npmjs.org/@types/react-dom/-/react-dom-18.0.11.tgz": "types-react-dom-18.0.11.tgz",
                "https://registry.yarnpkg.com/abbrev/-/abbrev-2.0.0.tgz": "abbrev-2.0.0.tgz",
                "git+ssh://git@github.com/kevva/is-positive.git#97edff6f": "github.com/kevva/is-positive/is-positive-external-gitcommit-97edff6f.tgz",
                "git+ssh://git@gitlab.foo.bar.com/osbs/hermetoproject/integration-tests.git#c300503": "gitlab.foo.bar.com/osbs/hermetoproject/integration-tests/hermetoproject/integration-tests-external-gitcommit-c300503.tgz",
            },
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.npm.resolver.async_download_files")
@mock.patch("hermeto.core.package_managers.npm.resolver.must_match_any_checksum")
@mock.patch("hermeto.core.checksum.ChecksumInfo.from_sri")
@mock.patch("hermeto.core.package_managers.npm.resolver.clone_as_tarball")
def test_get_npm_dependencies(
    mock_clone_as_tarball: mock.Mock,
    mock_from_sri: mock.Mock,
    mock_must_match_any_checksum: mock.Mock,
    mock_async_download_files: mock.Mock,
    rooted_tmp_path: RootedPath,
    deps_to_download: dict[str, dict[str, str | None]],
    expected_download_subpaths: dict[str, str],
) -> None:
    def args_based_return_checksum(integrity: str) -> ChecksumInfo:
        if integrity == "sha512-YOLO1111==":
            return ChecksumInfo("sha256", "YOLO1111")
        elif integrity == "sha512-YOLO2222==":
            return ChecksumInfo("sha256", "YOLO2222")
        else:
            return ChecksumInfo("sha256", "YOLO")

    mock_from_sri.side_effect = args_based_return_checksum
    mock_must_match_any_checksum.return_value = None
    mock_clone_as_tarball.return_value = None
    mock_async_download_files.return_value = None

    download_paths = _get_npm_dependencies(rooted_tmp_path, deps_to_download)
    expected_download_paths = {}
    for url, subpath in expected_download_subpaths.items():
        expected_download_paths[url] = rooted_tmp_path.join_within_root(subpath)

    assert download_paths == expected_download_paths


@pytest.mark.parametrize(
    "proxy_url",
    [
        pytest.param("https://foo:bar@example.com", id="full_credentials_are_present"),
        pytest.param("https://:bar@example.com", id="login_is_missing"),
        pytest.param("https://foo:@example.com", id="password_is_missing"),
    ],
)
def test_npm_settings_rejects_proxy_urls_containing_credentials(
    proxy_url: str,
) -> None:
    with pytest.raises(ValueError):
        NpmSettings(proxy_url=proxy_url)


@pytest.mark.parametrize(
    "deps_to_download",
    [
        pytest.param(
            {
                "https://github.com/hermetoproject/integration-tests/ms-1.0.0.tgz": {
                    "name": "ms",
                    "version": "1.0.0",
                    "integrity": "completely-fake",
                },
                "git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps-second.git#09992d418fc44a2895b7a9ff27c4e32d6f74a982": {
                    "version": "2.0.0",
                    "name": "cachi2-without-deps-second",
                },
                "git+ssh://git@github.com/kevva/is-positive.git#97edff6f": {
                    "integrity": "completely-fake",
                    "name": "is-positive",
                },
                "git+ssh://git@gitlab.foo.bar.com/osbs/hermetoproject/integration-tests.git#c300503": {
                    "integrity": "completely-fake",
                    "name": "gitlab-hermeto-npm-without-deps-second",
                },
            },
            id="multiple_vsc_systems_simultaneously",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.npm.resolver.async_download_files")
@mock.patch("hermeto.core.package_managers.npm.resolver.must_match_any_checksum")
@mock.patch("hermeto.core.checksum.ChecksumInfo.from_sri")
@mock.patch("hermeto.core.package_managers.npm.resolver.clone_as_tarball")
@mock.patch("hermeto.core.package_managers.npm.resolver.get_config")
def test_npm_proxy_credentials_do_not_propagate_to_nonregistry_hosts(
    mocked_config: mock.Mock,
    mock_clone_as_tarball: mock.Mock,
    mock_from_sri: mock.Mock,
    mock_must_match_any_checksum: mock.Mock,
    mock_async_download_files: mock.Mock,
    rooted_tmp_path: RootedPath,
    deps_to_download: dict[str, dict[str, str | None]],
) -> None:
    mock_config = mock.Mock()
    mock_config.npm.proxy_url = "https://fakeproxy.com"
    # ruff would assume this is a hardcoded password otherwise
    mock_config.npm.proxy_password = "fake-proxy-password"  # noqa: S105
    mock_config.npm.proxy_login = "fake-proxy-login"
    mocked_config.return_value = mock_config
    mock_from_sri.return_value = ("fake-algorithm", "fake-digest")

    _get_npm_dependencies(rooted_tmp_path, deps_to_download)

    for call in mock_async_download_files.mock_calls:
        assert call.kwargs["auth"] is None, "Found credentials where they should not be!"


@pytest.mark.parametrize(
    "deps_to_download",
    [
        pytest.param(
            {
                "https://registry.npmjs.org/@types/react-dom/-/react-dom-18.0.11.tgz": {
                    "name": "@types/react-dom",
                    "version": "18.0.11",
                    "integrity": "completely-fake",
                },
            },
            id="single_registry_dependency",
        ),
        pytest.param(
            {
                "https://registry.npmjs.org/@types/react-dom/-/react-dom-18.0.11.tgz": {
                    "name": "@types/react-dom",
                    "version": "18.0.11",
                    "integrity": "completely-fake",
                },
                "https://registry.yarnpkg.com/abbrev/-/abbrev-2.0.0.tgz": {
                    "name": "abbrev",
                    "version": "2.0.0",
                    "integrity": "completely-fake",
                },
            },
            id="multiple_registry_dependencies",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.npm.resolver.async_download_files")
@mock.patch("hermeto.core.package_managers.npm.resolver.must_match_any_checksum")
@mock.patch("hermeto.core.checksum.ChecksumInfo.from_sri")
@mock.patch("hermeto.core.package_managers.npm.resolver.clone_as_tarball")
@mock.patch("hermeto.core.package_managers.npm.resolver.get_config")
def test_npm_proxy_credentials_propagate_to_registry_hosts(
    mocked_config: mock.Mock,
    mock_clone_as_tarball: mock.Mock,
    mock_from_sri: mock.Mock,
    mock_must_match_any_checksum: mock.Mock,
    mock_async_download_files: mock.Mock,
    rooted_tmp_path: RootedPath,
    deps_to_download: dict[str, dict[str, str | None]],
) -> None:
    mock_config = mock.Mock()
    mock_config.npm.proxy_url = "https://fakeproxy.com"
    # ruff would assume this is a hardcoded password otherwise
    mock_config.npm.proxy_password = "fake-proxy-password"  # noqa: S105
    mock_config.npm.proxy_login = "fake-proxy-login"
    mocked_config.return_value = mock_config
    mock_from_sri.return_value = ("fake-algorithm", "fake-digest")

    _get_npm_dependencies(rooted_tmp_path, deps_to_download)

    msg = "Not found credentials where they should be!"
    for call in mock_async_download_files.mock_calls:
        assert call.kwargs["auth"] is not None, msg


@pytest.mark.parametrize(
    "deps_to_download",
    [
        pytest.param(
            {
                "https://registry.npmjs.org/@types/react-dom/-/react-dom-18.0.11.tgz": {
                    "name": "@types/react-dom",
                    "version": "18.0.11",
                    "integrity": "completely-fake",
                },
            },
            id="single_registry_dependency",
        ),
        pytest.param(
            {
                "https://registry.npmjs.org/@types/react-dom/-/react-dom-18.0.11.tgz": {
                    "name": "@types/react-dom",
                    "version": "18.0.11",
                    "integrity": "completely-fake",
                },
                "https://registry.yarnpkg.com/abbrev/-/abbrev-2.0.0.tgz": {
                    "name": "abbrev",
                    "version": "2.0.0",
                    "integrity": "completely-fake",
                },
            },
            id="multiple_registry_dependencies",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.npm.resolver.async_download_files")
@mock.patch("hermeto.core.package_managers.npm.resolver.must_match_any_checksum")
@mock.patch("hermeto.core.checksum.ChecksumInfo.from_sri")
@mock.patch("hermeto.core.package_managers.npm.resolver.clone_as_tarball")
@mock.patch("hermeto.core.package_managers.npm.resolver.get_config")
def test_npm_proxy_url_gets_substituted_for_registry_hosts(
    mocked_config: mock.Mock,
    mock_clone_as_tarball: mock.Mock,
    mock_from_sri: mock.Mock,
    mock_must_match_any_checksum: mock.Mock,
    mock_async_download_files: mock.Mock,
    rooted_tmp_path: RootedPath,
    deps_to_download: dict[str, dict[str, str | None]],
) -> None:
    proxy_url = "https://fakeproxy.com"
    mock_config = mock.Mock()
    mock_config.npm.proxy_url = proxy_url
    # ruff would assume this is a hardcoded password otherwise
    mock_config.npm.proxy_password = "fake-proxy-password"  # noqa: S105
    mock_config.npm.proxy_login = "fake-proxy-login"
    mocked_config.return_value = mock_config
    mock_from_sri.return_value = ("fake-algorithm", "fake-digest")

    _get_npm_dependencies(rooted_tmp_path, deps_to_download)

    msg = "Proxy URL was not substituted!"
    for call in mock_async_download_files.mock_calls:
        location = next(iter(call.kwargs["files_to_download"].keys()))
        assert location.startswith(proxy_url), msg
