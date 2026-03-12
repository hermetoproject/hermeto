# SPDX-License-Identifier: GPL-3.0-only
from unittest import mock

import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.config import NpmSettings
from hermeto.core.package_managers.npm import (
    NormalizedUrl,
    _clone_repo_pack_archive,
    _get_npm_dependencies,
)
from hermeto.core.rooted_path import RootedPath


@mock.patch("hermeto.core.package_managers.npm.fetch.clone_as_tarball")
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
@mock.patch("hermeto.core.package_managers.npm.fetch.async_download_files")
@mock.patch("hermeto.core.package_managers.npm.fetch.must_match_any_checksum")
@mock.patch("hermeto.core.checksum.ChecksumInfo.from_sri")
@mock.patch("hermeto.core.package_managers.npm.fetch.clone_as_tarball")
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
@mock.patch("hermeto.core.package_managers.npm.fetch.async_download_files")
@mock.patch("hermeto.core.package_managers.npm.fetch.must_match_any_checksum")
@mock.patch("hermeto.core.checksum.ChecksumInfo.from_sri")
@mock.patch("hermeto.core.package_managers.npm.fetch.clone_as_tarball")
@mock.patch("hermeto.core.package_managers.npm.fetch.get_config")
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
@mock.patch("hermeto.core.package_managers.npm.fetch.async_download_files")
@mock.patch("hermeto.core.package_managers.npm.fetch.must_match_any_checksum")
@mock.patch("hermeto.core.checksum.ChecksumInfo.from_sri")
@mock.patch("hermeto.core.package_managers.npm.fetch.clone_as_tarball")
@mock.patch("hermeto.core.package_managers.npm.fetch.get_config")
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
@mock.patch("hermeto.core.package_managers.npm.fetch.async_download_files")
@mock.patch("hermeto.core.package_managers.npm.fetch.must_match_any_checksum")
@mock.patch("hermeto.core.checksum.ChecksumInfo.from_sri")
@mock.patch("hermeto.core.package_managers.npm.fetch.clone_as_tarball")
@mock.patch("hermeto.core.package_managers.npm.fetch.get_config")
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
