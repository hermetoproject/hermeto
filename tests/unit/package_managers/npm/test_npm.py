# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from hermeto import APP_NAME
from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import NpmSettings
from hermeto.core.models.input import Request
from hermeto.core.models.output import ProjectFile, RequestOutput
from hermeto.core.models.sbom import Annotation, Component, Property
from hermeto.core.package_managers.npm.main import _generate_component_list, fetch_npm_source
from hermeto.core.package_managers.npm.project import (
    NpmComponentInfo,
    ResolvedNpmPackage,
)
from hermeto.core.package_managers.npm.resolver import _get_npm_dependencies
from hermeto.core.rooted_path import RootedPath


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
    "deps_to_download, expected_download_subpaths",
    [
        (
            {
                "https://github.com/cachito-testing/ms-1.0.0.tgz": {
                    "name": "ms",
                    "version": "1.0.0",
                    "integrity": "sha512-YOLO1111==",
                },
                # Test handling package with the same name but different version and integrity
                "https://github.com/cachito-testing/ms-2.0.0.tgz": {
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
                "git+ssh://git@gitlab.foo.bar.com/osbs/cachito-tests.git#c300503": {
                    "integrity": "sha512-FOOOOOOOOOYOLO==",
                    "name": "gitlab-hermeto-npm-without-deps-second",
                },
            },
            {
                "https://github.com/cachito-testing/ms-1.0.0.tgz": "external-ms/ms-external-sha256-YOLO1111.tgz",
                "https://github.com/cachito-testing/ms-2.0.0.tgz": "external-ms/ms-external-sha256-YOLO2222.tgz",
                "git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps-second.git#09992d418fc44a2895b7a9ff27c4e32d6f74a982": "bitbucket.org/cachi-testing/cachi2-without-deps-second/cachi2-without-deps-second-external-gitcommit-09992d418fc44a2895b7a9ff27c4e32d6f74a982.tgz",
                "https://registry.npmjs.org/@types/react-dom/-/react-dom-18.0.11.tgz": "types-react-dom-18.0.11.tgz",
                "https://registry.yarnpkg.com/abbrev/-/abbrev-2.0.0.tgz": "abbrev-2.0.0.tgz",
                "git+ssh://git@github.com/kevva/is-positive.git#97edff6f": "github.com/kevva/is-positive/is-positive-external-gitcommit-97edff6f.tgz",
                "git+ssh://git@gitlab.foo.bar.com/osbs/cachito-tests.git#c300503": "gitlab.foo.bar.com/osbs/cachito-tests/cachito-tests-external-gitcommit-c300503.tgz",
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
                "https://github.com/cachito-testing/ms-1.0.0.tgz": {
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
                "git+ssh://git@gitlab.foo.bar.com/osbs/cachito-tests.git#c300503": {
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
