# SPDX-License-Identifier: GPL-3.0-only
import json
import urllib.parse
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from hermeto import APP_NAME
from hermeto.core.errors import LockfileNotFound, PackageRejected, UnsupportedFeature
from hermeto.core.models.input import Request
from hermeto.core.models.output import ProjectFile, RequestOutput
from hermeto.core.models.sbom import Annotation, Component, Property
from hermeto.core.package_managers.npm import (
    NpmComponentInfo,
    ResolvedNpmPackage,
    _generate_component_list,
    _resolve_npm,
    fetch_npm_source,
)
from hermeto.core.rooted_path import RootedPath

from .conftest import MOCK_REPO_VCS_URL


def urlq(url: str) -> str:
    return urllib.parse.quote(url, safe=":/")


@pytest.mark.parametrize(
    "components, expected_components",
    [
        (
            [
                {
                    "name": "foo",
                    "purl": "pkg:npm/foo@1.0.0",
                    "version": "1.0.0",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                {
                    "name": "bar",
                    "purl": "pkg:npm/bar@1.0.0",
                    "version": "1.0.0",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
            ],
            [
                Component(name="foo", version="1.0.0", purl="pkg:npm/foo@1.0.0"),
                Component(name="bar", version="1.0.0", purl="pkg:npm/bar@1.0.0"),
            ],
        ),
        (
            [
                {
                    "name": "foo",
                    "purl": "pkg:npm/foo@1.0.0",
                    "version": "1.0.0",
                    "bundled": False,
                    "dev": True,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name="cdx:npm:package:development", value="true"),
                        Property(name=f"{APP_NAME}:found_by", value=f"{APP_NAME}"),
                    ],
                ),
            ],
        ),
        (
            [
                {
                    "name": "foo",
                    "purl": "pkg:npm/foo@1.0.0",
                    "version": "1.0.0",
                    "bundled": True,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name="cdx:npm:package:bundled", value="true"),
                        Property(name=f"{APP_NAME}:found_by", value=f"{APP_NAME}"),
                    ],
                ),
            ],
        ),
        (
            [
                {
                    "name": "foo",
                    "purl": "pkg:npm/foo@1.0.0",
                    "version": "1.0.0",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": Path("path/to/foo/package-lock.json"),
                    "external_refs": None,
                },
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(
                            name=f"{APP_NAME}:missing_hash:in_file",
                            value="path/to/foo/package-lock.json",
                        ),
                    ],
                ),
            ],
        ),
    ],
)
def test_generate_component_list(
    components: list[NpmComponentInfo], expected_components: list[Component]
) -> None:
    """Test _generate_component_list with different NpmComponentInfo inputs."""
    merged_components = _generate_component_list(components)
    assert merged_components == expected_components


@pytest.mark.parametrize(
    "npm_input_packages, resolved_packages, request_output",
    [
        pytest.param(
            [{"type": "npm", "path": "."}],
            [
                {
                    "package": {
                        "name": "foo",
                        "version": "1.0.0",
                        "purl": "pkg:npm/foo@1.0.0",
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
                        }
                    ],
                    "projectfiles": [
                        ProjectFile(abspath="/some/path", template="some text"),
                    ],
                    "dependencies_to_download": {
                        "https://some.registry.org/bar/-/bar-2.0.0.tgz": {
                            "integrity": "sha512-JCB8C6SnDoQf",
                            "name": "bar",
                            "version": "2.0.0",
                        }
                    },
                    "package_lock_file": ProjectFile(abspath="/some/path", template="some text"),
                },
            ],
            {
                "components": [
                    Component(name="foo", version="1.0.0", purl="pkg:npm/foo@1.0.0"),
                    Component(name="bar", version="2.0.0", purl="pkg:npm/bar@2.0.0"),
                ],
                "environment_variables": [],
                "project_files": [
                    ProjectFile(abspath="/some/path", template="some text"),
                ],
            },
            id="single_input_package",
        ),
        pytest.param(
            [{"type": "npm", "path": "."}, {"type": "npm", "path": "path"}],
            [
                {
                    "package": {
                        "name": "foo",
                        "version": "1.0.0",
                        "purl": "pkg:npm/foo@1.0.0",
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
                        }
                    ],
                    "projectfiles": [
                        ProjectFile(abspath="/some/path", template="some text"),
                    ],
                    "dependencies_to_download": {
                        "https://some.registry.org/bar/-/bar-2.0.0.tgz": {
                            "integrity": "sha512-JCB8C6SnDoQf",
                            "name": "bar",
                            "version": "2.0.0",
                        }
                    },
                    "package_lock_file": ProjectFile(abspath="/some/path", template="some text"),
                },
                {
                    "package": {
                        "name": "spam",
                        "version": "3.0.0",
                        "purl": "pkg:npm/spam@3.0.0",
                        "bundled": False,
                        "dev": False,
                        "missing_hash_in_file": None,
                        "external_refs": None,
                    },
                    "dependencies": [
                        {
                            "name": "eggs",
                            "version": "4.0.0",
                            "purl": "pkg:npm/eggs@4.0.0",
                            "bundled": False,
                            "dev": False,
                            "missing_hash_in_file": None,
                            "external_refs": None,
                        }
                    ],
                    "dependencies_to_download": {
                        "https://some.registry.org/eggs/-/eggs-1.0.0.tgz": {
                            "integrity": "sha512-JCB8C6SnDoQfYOLOO",
                            "name": "eggs",
                            "version": "1.0.0",
                        }
                    },
                    "projectfiles": [
                        ProjectFile(abspath="/some/path", template="some text"),
                        ProjectFile(abspath="/some/other/path", template="some other text"),
                    ],
                    "package_lock_file": ProjectFile(
                        abspath="/some/other/path", template="some other text"
                    ),
                },
            ],
            {
                "components": [
                    Component(name="foo", version="1.0.0", purl="pkg:npm/foo@1.0.0"),
                    Component(name="bar", version="2.0.0", purl="pkg:npm/bar@2.0.0"),
                    Component(name="spam", version="3.0.0", purl="pkg:npm/spam@3.0.0"),
                    Component(name="eggs", version="4.0.0", purl="pkg:npm/eggs@4.0.0"),
                ],
                "environment_variables": [],
                "project_files": [
                    ProjectFile(abspath="/some/path", template="some text"),
                    ProjectFile(abspath="/some/other/path", template="some other text"),
                ],
            },
            id="multiple_input_package",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.npm.main.create_backend_annotation")
@mock.patch("hermeto.core.package_managers.npm.main._resolve_npm")
def test_fetch_npm_source(
    mock_resolve_npm: mock.Mock,
    mock_create_annotation: mock.Mock,
    npm_request: Request,
    npm_input_packages: dict[str, str],
    resolved_packages: list[ResolvedNpmPackage],
    request_output: dict[str, list[Any]],
) -> None:
    """Test fetch_npm_source with different Request inputs."""
    mock_annotation = Annotation(
        subjects=set(),
        annotator={"organization": {"name": "red hat"}},
        timestamp="2026-01-01T00:00:00Z",
        text="hermeto:backend:npm",
    )
    mock_create_annotation.return_value = mock_annotation
    mock_resolve_npm.side_effect = resolved_packages
    output = fetch_npm_source(npm_request)
    expected_output = RequestOutput.from_obj_list(
        components=request_output["components"],
        environment_variables=request_output["environment_variables"],
        project_files=request_output["project_files"],
        annotations=[mock_annotation],
    )

    assert output == expected_output


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
@mock.patch("hermeto.core.package_managers.npm.main._get_npm_dependencies")
@mock.patch("hermeto.core.package_managers.npm.main._update_package_lock_with_local_paths")
@mock.patch("hermeto.core.package_managers.npm.main._update_package_json_files")
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
