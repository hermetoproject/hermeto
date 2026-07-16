# SPDX-License-Identifier: GPL-3.0-only
from textwrap import dedent
from unittest import mock

import pytest
from git.repo import Repo
from pydantic import HttpUrl

from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, PackageRejected, UnsupportedFeature
from hermeto.core.models.output import EnvironmentVariable
from hermeto.core.package_managers.bundler.main import (
    _download_gems,
    _get_main_package_name_and_version,
    _get_repo_name_from_origin_remote,
    _prepare_environment_variables_for_hermetic_build,
    _prepare_for_hermetic_build,
)
from hermeto.core.package_managers.bundler.parser import (
    GemDependency,
    PathDependency,
)
from hermeto.core.rooted_path import RootedPath

FAKE_RUBYGEMS_SOURCE = "https://rubygems.org"
FAKE_PROXY_URL = "https://fakeproxy.com/rubygems"


def test_get_main_package_name_and_version(rooted_tmp_path: RootedPath) -> None:
    path_deps = [
        PathDependency(
            name="my_path_dep",
            version="0.2.0",
            root=str(rooted_tmp_path),
            subpath=".",
        ),
    ]

    name, version = _get_main_package_name_and_version(
        package_dir=rooted_tmp_path, path_deps=path_deps
    )
    assert name == "my_path_dep"
    assert version == "0.2.0"


def test_get_main_package_name_and_version_from_repo(rooted_tmp_path_repo: RootedPath) -> None:
    repo = Repo(rooted_tmp_path_repo)
    repo.create_remote("origin", "git@github.com:user/example.git")

    name, version = _get_main_package_name_and_version(
        package_dir=rooted_tmp_path_repo, path_deps=[]
    )

    assert name == "example"
    assert version is None


def test_get_main_package_name_and_version_from_repo_without_origin(
    rooted_tmp_path_repo: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(PackageRejected) as exc_info:
        _get_main_package_name_and_version(package_dir=rooted_tmp_path_repo, path_deps=[])

    assert "Failed to extract package name from origin remote" in exc_info.value.friendly_msg()


def test_prepare_for_hermetic_build_injects_necessary_variable_into_empty_config(
    rooted_tmp_path: RootedPath,
) -> None:
    expected_config_location = rooted_tmp_path.join_within_root(".bundle/config").path
    expected_config_contents = dedent(
        """
        BUNDLE_CACHE_PATH: "${output_dir}/deps/bundler"
        BUNDLE_DEPLOYMENT: "true"
        BUNDLE_NO_PRUNE: "true"
        BUNDLE_ALLOW_OFFLINE_INSTALL: "true"
        BUNDLE_DISABLE_VERSION_CHECK: "true"
        BUNDLE_VERSION: "system"
        """
    )

    assert not expected_config_location.exists(), "Unexpected .bundle/config in rooted_tmp_path"

    result = _prepare_for_hermetic_build(rooted_tmp_path, rooted_tmp_path)

    assert result.template == expected_config_contents


def test_prepare_for_hermetic_build_injects_necessary_variable_into_existing_config(
    rooted_tmp_path: RootedPath,
) -> None:
    expected_config_location = rooted_tmp_path.join_within_root(".bundle/config").path
    expected_config_contents = dedent(
        """
        BUNDLE_CACHE_PATH: "${output_dir}/deps/bundler"
        BUNDLE_DEPLOYMENT: "true"
        BUNDLE_NO_PRUNE: "true"
        BUNDLE_ALLOW_OFFLINE_INSTALL: "true"
        BUNDLE_DISABLE_VERSION_CHECK: "true"
        BUNDLE_VERSION: "system"
        """
    )
    existing_preamble = dedent(
        """---

        BUNDLER_NONEXISTENT_VARIABLE: "true"
        """
    )

    assert not expected_config_location.exists(), "Unexpected .bundle/config in rooted_tmp_path"
    assert not expected_config_location.parent.exists(), "Unexpected .bundle/ in rooted_tmp_path"

    expected_config_location.parent.mkdir()
    expected_config_location.write_text(existing_preamble)

    result = _prepare_for_hermetic_build(rooted_tmp_path, rooted_tmp_path)

    assert result.template == existing_preamble + expected_config_contents


def test_prepare_for_hermetic_build_injects_necessary_variable_into_existing_alternate_config(
    rooted_tmp_path: RootedPath,
) -> None:
    expected_alternate_config_location = rooted_tmp_path.join_within_root("alternate/config").path
    expected_alternate_config_contents = dedent(
        """
        BUNDLE_CACHE_PATH: "${output_dir}/deps/bundler"
        BUNDLE_DEPLOYMENT: "true"
        BUNDLE_NO_PRUNE: "true"
        BUNDLE_ALLOW_OFFLINE_INSTALL: "true"
        BUNDLE_DISABLE_VERSION_CHECK: "true"
        BUNDLE_VERSION: "system"
        """
    )
    existing_preamble = dedent(
        """---
        BUNDLER_NONEXISTENT_VARIABLE: "true"
        """
    )

    assert not expected_alternate_config_location.exists(), (
        "Unexpected .bundle/config in rooted_tmp_path"
    )
    assert not expected_alternate_config_location.parent.exists(), (
        "Unexpected .bundle/ in rooted_tmp_path"
    )

    expected_alternate_config_location.parent.mkdir()
    expected_alternate_config_location.write_text(existing_preamble)

    with mock.patch("hermeto.core.package_managers.bundler.main.os.getenv") as ge:
        ge.return_value = str(expected_alternate_config_location.parent)
        result = _prepare_for_hermetic_build(rooted_tmp_path, rooted_tmp_path)

    assert result.template == existing_preamble + expected_alternate_config_contents


def test_prepare_environment_variables_generates_git_config_entries_for_git_deps() -> None:
    git_paths = [
        ("gem-a", "gem-a-aabb11223344", "https://git.example/gem-a.git"),
        ("gem-b", "gem-b-ccdd55667788", "https://git.example/gem-b.git"),
    ]

    result = _prepare_environment_variables_for_hermetic_build(git_paths)

    expected = {
        EnvironmentVariable(
            name="BUNDLE_APP_CONFIG", value="${output_dir}/bundler/config_override"
        ),
        EnvironmentVariable(name="GIT_CONFIG_COUNT", value="3"),
        EnvironmentVariable(
            name="GIT_CONFIG_KEY_0",
            value="url.file://${output_dir}/deps/bundler/gem-a-aabb11223344/.insteadOf",
        ),
        EnvironmentVariable(name="GIT_CONFIG_VALUE_0", value="https://git.example/gem-a.git"),
        EnvironmentVariable(
            name="GIT_CONFIG_KEY_1",
            value="url.file://${output_dir}/deps/bundler/gem-b-ccdd55667788/.insteadOf",
        ),
        EnvironmentVariable(name="GIT_CONFIG_VALUE_1", value="https://git.example/gem-b.git"),
        EnvironmentVariable(name="GIT_CONFIG_KEY_2", value="protocol.file.allow"),
        EnvironmentVariable(name="GIT_CONFIG_VALUE_2", value="always"),
    }
    assert set(result) == expected


def test_prepare_environment_variables_raises_on_duplicate_git_url() -> None:
    git_paths = [
        ("gem-a", "gem-a-aabbccdd1234", "https://git.example/monorepo.git"),
        ("gem-b", "gem-b-eeff00112233", "https://git.example/monorepo.git"),
    ]

    with pytest.raises(UnsupportedFeature, match="same repository"):
        _prepare_environment_variables_for_hermetic_build(git_paths)


@mock.patch("hermeto.core.package_managers.bundler.main.get_config")
@mock.patch("hermeto.core.package_managers.bundler.main.get_repo_id")
def test_get_repo_name_raises_without_git_repo_even_in_permissive_mode(
    mock_handle_get_repo_id: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """Name inference from origin remote is mode-insensitive; it always requires git."""
    mock_handle_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.PERMISSIVE

    with pytest.raises(PackageRejected):
        _get_repo_name_from_origin_remote(rooted_tmp_path)


@mock.patch("hermeto.core.package_managers.bundler.gem_models.get_config")
@mock.patch("hermeto.core.package_managers.bundler.gem_models.get_vcs_qualifiers")
def test_path_dependency_purl_strict_mode_raises_without_git_repo(
    mock_get_vcs_qualifiers: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """PathDependency.purl re-raises NotAGitRepo in STRICT mode."""
    mock_get_vcs_qualifiers.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.STRICT

    dep = PathDependency(
        name="my-path-dep",
        version="0.1.0",
        root=rooted_tmp_path,
        subpath=".",
    )

    with pytest.raises(NotAGitRepo):
        _ = dep.purl


def _mock_bundler_config(proxy_url: HttpUrl | None) -> mock.Mock:
    mock_config = mock.Mock()
    mock_config.bundler.proxy_url = proxy_url
    mock_config.runtime.concurrency_limit = 5
    return mock_config


@pytest.mark.parametrize(
    "proxy_url, expected_url",
    [
        pytest.param(
            None,
            f"{FAKE_RUBYGEMS_SOURCE}/downloads/foo-1.0.0.gem",
            id="no_proxy_configured",
        ),
        pytest.param(
            HttpUrl(FAKE_PROXY_URL),
            f"{FAKE_PROXY_URL}/downloads/foo-1.0.0.gem",
            id="proxy_configured",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.bundler.main.async_download_files")
@mock.patch("hermeto.core.package_managers.bundler.main.get_config")
def test_download_gems_rewrites_url_when_proxy_configured(
    mock_get_config: mock.Mock,
    mock_async_download_files: mock.Mock,
    proxy_url: HttpUrl | None,
    expected_url: str,
    rooted_tmp_path: RootedPath,
) -> None:
    """Gems download from the original URL, or a proxy-rewritten one when configured."""
    mock_get_config.return_value = _mock_bundler_config(proxy_url)
    dep = GemDependency(name="foo", version="1.0.0", source=FAKE_RUBYGEMS_SOURCE)

    _download_gems([dep], rooted_tmp_path)

    mock_async_download_files.assert_called_once_with(
        files_to_download={expected_url: dep.download_location(rooted_tmp_path)},
        concurrency_limit=5,
    )
