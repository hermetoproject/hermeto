# SPDX-License-Identifier: GPL-3.0-only
import json
from unittest import mock

import pytest

from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo
from hermeto.core.models.sbom import PROXY_COMMENT, ExternalReference
from hermeto.core.package_managers.javascript.pnpm.project import PnpmPackage
from hermeto.core.package_managers.javascript.pnpm.resolver import (
    JSR_REGISTRY_URL,
    _create_dependency_components,
    _create_root_component,
    _generate_purl_for,
    generate_sbom_components,
)
from hermeto.core.package_managers.npm import NPM_REGISTRY_URL
from hermeto.core.rooted_path import RootedPath
from tests.unit.package_managers.javascript.pnpm.test_main import FAKE_PROXY_URL


@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_vcs_qualifiers")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_config")
def test_generate_sbom_components_in_strict_mode_without_git_repo(
    mock_get_config: mock.Mock,
    mock_get_vcs_qualifiers: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_get_config.return_value = mock.Mock()
    mock_get_config.return_value.mode = Mode.STRICT
    mock_get_vcs_qualifiers.side_effect = NotAGitRepo("", solution=None)

    with pytest.raises(NotAGitRepo):
        generate_sbom_components(rooted_tmp_path, [])


@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_config")
def test_create_lockfile_components_without_proxy(mock_get_config: mock.Mock) -> None:
    mock_get_config.return_value = mock.Mock()
    mock_get_config.return_value.pnpm.proxy_url = None

    pkg = PnpmPackage("pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz")
    components = _create_dependency_components([pkg], vcs_qualifiers={})
    assert components[0].external_references is None


@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_config")
def test_create_lockfile_components_with_proxy(mock_get_config: mock.Mock) -> None:
    mock_get_config.return_value = mock.Mock()
    mock_get_config.return_value.pnpm.proxy_url = FAKE_PROXY_URL

    registry_pkg = PnpmPackage(
        "pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz"
    )
    non_registry_pkg = PnpmPackage(
        "pkg@1.0.0", "", "pkg", "1.0.0", "https://example.com/pkg-1.0.0.tgz"
    )
    components = _create_dependency_components([registry_pkg, non_registry_pkg], vcs_qualifiers={})

    assert components[0].external_references == [
        ExternalReference(url=FAKE_PROXY_URL, comment=PROXY_COMMENT)
    ]
    assert components[1].external_references is None


@pytest.mark.parametrize(
    ("package", "vcs_qualifiers", "expected_purl"),
    [
        pytest.param(
            PnpmPackage("pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz"),
            {},
            "pkg:npm/pkg@1.0.0",
            id="npm_registry",
        ),
        pytest.param(
            PnpmPackage(
                "@scope/pkg@1.0.0",
                "scope",
                "pkg",
                "1.0.0",
                f"{NPM_REGISTRY_URL}/@scope/pkg/-/pkg-1.0.0.tgz",
            ),
            {},
            "pkg:npm/%40scope/pkg@1.0.0",
            id="scoped_npm_registry",
        ),
        pytest.param(
            PnpmPackage("pkg@1.0.0", "", "pkg", "1.0.0", f"{JSR_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz"),
            {},
            "pkg:npm/pkg@1.0.0?repository_url=https://npm.jsr.io",
            id="jsr_registry",
        ),
        pytest.param(
            PnpmPackage(
                "@scope/pkg@1.0.0",
                "scope",
                "pkg",
                "1.0.0",
                f"{JSR_REGISTRY_URL}/@scope/pkg/-/pkg-1.0.0.tgz",
            ),
            {},
            "pkg:npm/%40scope/pkg@1.0.0?repository_url=https://npm.jsr.io",
            id="scoped_jsr_registry",
        ),
        pytest.param(
            PnpmPackage(
                "pkg@1.0.0",
                "",
                "pkg",
                "1.0.0",
                "https://codeload.github.com/org/pkg/tar.gz/abc123",
            ),
            {},
            "pkg:npm/pkg@1.0.0?download_url=https://codeload.github.com/org/pkg/tar.gz/abc123",
            id="git",
        ),
        pytest.param(
            PnpmPackage("local@1.0.0", "", "local", "1.0.0", "file:packages/local.tgz"),
            {"vcs_url": "git+https://github.com/org/repo@abc"},
            "pkg:npm/local@1.0.0?vcs_url=git%2Bhttps://github.com/org/repo%40abc#packages/local.tgz",
            id="local",
        ),
    ],
)
def test_generate_purl_for(
    package: PnpmPackage, vcs_qualifiers: dict[str, str], expected_purl: str
) -> None:
    assert _generate_purl_for(package, vcs_qualifiers).to_string() == expected_purl


@pytest.mark.parametrize(
    ("name", "expected_purl"),
    [
        (
            "app",
            "pkg:npm/app@0.1.0?vcs_url=git%2Bhttps://github.com/org/repo%40abc#ui/frontend",
        ),
        (
            "@scope/app",
            "pkg:npm/%40scope/app@0.1.0?vcs_url=git%2Bhttps://github.com/org/repo%40abc#ui/frontend",
        ),
    ],
)
def test_create_root_component(rooted_tmp_path: RootedPath, name: str, expected_purl: str) -> None:
    subproject = rooted_tmp_path.join_within_root("ui", "frontend")
    subproject.path.mkdir(parents=True)

    package_json = subproject.path.joinpath("package.json")
    package_json.write_text(json.dumps({"name": name, "version": "0.1.0"}))
    vcs_qualifiers = {"vcs_url": "git+https://github.com/org/repo@abc"}
    component = _create_root_component(subproject, vcs_qualifiers)

    assert component.purl == expected_purl
