# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path
from unittest import mock

import pytest

from hermeto.core.constants import Mode
from hermeto.core.errors import InvalidLockfileFormat, NotAGitRepo, PackageRejected
from hermeto.core.models.sbom import PROXY_COMMENT, ExternalReference, Patch, PatchDiff, Pedigree
from hermeto.core.package_managers.javascript.npm import NPM_REGISTRY_URL
from hermeto.core.package_managers.javascript.pnpm.project import PnpmLock, PnpmPackage
from hermeto.core.package_managers.javascript.pnpm.resolver import (
    JSR_REGISTRY_URL,
    _create_dependency_components,
    _create_root_component,
    _create_workspace_components,
    _find_non_dev_dependencies,
    _generate_pedigree_for,
    _generate_purl_for,
    _read_workspace_globs,
    generate_sbom_components,
)
from hermeto.core.rooted_path import RootedPath
from tests.unit.package_managers.javascript.pnpm.test_main import FAKE_PROXY_URL

VCS_URL = "git+https://github.com/org/repo@abc"
ENQUOTED_VCS_URL = "git%2Bhttps://github.com/org/repo%40abc"
VCS_QUALIFIERS = {"vcs_url": VCS_URL}


@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_vcs_qualifiers")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_config")
def test_generate_sbom_components_in_strict_mode_without_git_repo(
    mock_get_config: mock.Mock,
    mock_get_vcs_qualifiers: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_get_config.return_value = mock.Mock()
    mock_get_config.return_value.mode = Mode.STRICT
    mock_get_vcs_qualifiers.side_effect = NotAGitRepo("")

    with pytest.raises(NotAGitRepo):
        generate_sbom_components(rooted_tmp_path, [], mock.Mock())


@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_config")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver._find_non_dev_dependencies")
def test_create_lockfile_components_without_proxy(
    mock_find_non_dev_dependencies: mock.Mock,
    mock_get_config: mock.Mock,
) -> None:
    mock_get_config.return_value = mock.Mock()
    mock_get_config.return_value.pnpm.proxy_url = None

    pkg = PnpmPackage("pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz")
    components = _create_dependency_components([pkg], {}, mock.Mock())
    assert components[0].external_references is None


@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_config")
@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver._find_non_dev_dependencies")
def test_create_lockfile_components_with_proxy(
    mock_find_non_dev_dependencies: mock.Mock,
    mock_get_config: mock.Mock,
) -> None:
    mock_get_config.return_value = mock.Mock()
    mock_get_config.return_value.pnpm.proxy_url = FAKE_PROXY_URL

    registry_pkg = PnpmPackage(
        "pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz"
    )
    non_registry_pkg = PnpmPackage(
        "pkg@1.0.0", "", "pkg", "1.0.0", "https://example.com/pkg-1.0.0.tgz"
    )
    components = _create_dependency_components([registry_pkg, non_registry_pkg], {}, mock.Mock())

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
            VCS_QUALIFIERS,
            f"pkg:npm/local@1.0.0?vcs_url={ENQUOTED_VCS_URL}#packages/local.tgz",
            id="local",
        ),
    ],
)
def test_generate_purl_for(
    package: PnpmPackage, vcs_qualifiers: dict[str, str], expected_purl: str
) -> None:
    assert _generate_purl_for(package, vcs_qualifiers).to_string() == expected_purl


@pytest.mark.parametrize(
    ("package", "vcs_qualifiers", "patches", "url"),
    [
        pytest.param(
            PnpmPackage("pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz"),
            {},
            {"pkg@1.0.0": {"path": "patches/pkg.patch"}},
            None,
            id="no_vcs_qualifiers",
        ),
        pytest.param(
            PnpmPackage("pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz"),
            VCS_QUALIFIERS,
            {},
            None,
            id="no_patches",
        ),
        pytest.param(
            PnpmPackage("pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz"),
            VCS_QUALIFIERS,
            {"pkg@1.0.0": {"path": "patches/pkg@1.0.0.patch"}},
            f"{VCS_URL}#patches/pkg@1.0.0.patch",
            id="match_by_package_id",
        ),
        pytest.param(
            PnpmPackage("pkg@1.0.0", "", "pkg", "1.0.0", f"{NPM_REGISTRY_URL}/pkg/-/pkg-1.0.0.tgz"),
            VCS_QUALIFIERS,
            {"pkg": {"path": "patches/pkg.patch"}},
            f"{VCS_URL}#patches/pkg.patch",
            id="match_by_package_name",
        ),
        pytest.param(
            PnpmPackage(
                "@scope/pkg@1.0.0",
                "scope",
                "pkg",
                "1.0.0",
                f"{NPM_REGISTRY_URL}/@scope/pkg/-/pkg-1.0.0.tgz",
            ),
            VCS_QUALIFIERS,
            {"@scope/pkg": {"path": "patches/@scope/pkg.patch"}},
            f"{VCS_URL}#patches/@scope/pkg.patch",
            id="match_by_package_scope_and_name",
        ),
    ],
)
def test_generate_pedigree_for(
    package: PnpmPackage,
    vcs_qualifiers: dict[str, str],
    patches: dict[str, dict[str, str]],
    url: str | None,
    tmp_path: Path,
) -> None:
    lockfile = PnpmLock(tmp_path, {"lockfileVersion": "9.0", "patchedDependencies": patches})
    expected_pedigree = Pedigree(patches=[Patch(diff=PatchDiff(url=url))]) if url else None
    assert _generate_pedigree_for(package, vcs_qualifiers, lockfile) == expected_pedigree


def test_generate_pedigree_fails_when_path_is_missing(tmp_path: Path) -> None:
    lockfile = PnpmLock(
        tmp_path, {"lockfileVersion": "9.0", "patchedDependencies": {"pkg@1.0.0": {}}}
    )
    pkg = PnpmPackage("pkg@1.0.0", "", "pkg", "1.0.0", "")

    with pytest.raises(InvalidLockfileFormat):
        _generate_pedigree_for(pkg, VCS_QUALIFIERS, lockfile)


@pytest.mark.parametrize(
    ("name", "expected_purl"),
    [
        (
            "app",
            f"pkg:npm/app@0.1.0?vcs_url={ENQUOTED_VCS_URL}#ui/frontend",
        ),
        (
            "@scope/app",
            f"pkg:npm/%40scope/app@0.1.0?vcs_url={ENQUOTED_VCS_URL}#ui/frontend",
        ),
    ],
)
def test_create_root_component(rooted_tmp_path: RootedPath, name: str, expected_purl: str) -> None:
    subproject = rooted_tmp_path.join_within_root("ui", "frontend")
    subproject.path.mkdir(parents=True)

    package_json = subproject.path.joinpath("package.json")
    package_json.write_text(json.dumps({"name": name, "version": "0.1.0"}))
    component = _create_root_component(subproject, VCS_QUALIFIERS)

    assert component.purl == expected_purl


@pytest.mark.parametrize(
    ("project_relpath", "expected_subpath"),
    [
        pytest.param(".", "packages/w", id="project_at_repo_root"),
        pytest.param("ui/frontend", "ui/frontend/packages/w", id="nested_project_path"),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_workspace_paths")
def test_create_workspace_components(
    mock_get_workspace_paths: mock.Mock,
    rooted_tmp_path: RootedPath,
    project_relpath: str,
    expected_subpath: str,
) -> None:
    project_dir = rooted_tmp_path.join_within_root(project_relpath)
    project_dir.path.mkdir(parents=True, exist_ok=True)

    w = project_dir.path.joinpath("packages", "w")
    w.mkdir(parents=True)
    w.joinpath("package.json").write_text(json.dumps({"name": "hermelean", "version": "1.0.0"}))

    mock_get_workspace_paths.return_value = [w]

    components = _create_workspace_components(project_dir, VCS_QUALIFIERS)
    assert len(components) == 1

    assert components[0].name == "hermelean"
    assert components[0].version == "1.0.0"
    assert (
        components[0].purl
        == f"pkg:npm/hermelean@1.0.0?vcs_url={ENQUOTED_VCS_URL}#{expected_subpath}"
    )


@mock.patch("hermeto.core.package_managers.javascript.pnpm.resolver.get_workspace_paths")
def test_create_workspace_components_without_package_json(
    mock_get_workspace_paths: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    w = rooted_tmp_path.path.joinpath("packages", "w")
    w.mkdir(parents=True)

    mock_get_workspace_paths.return_value = [w]
    assert _create_workspace_components(rooted_tmp_path, {}) == []


def test_read_workspace_globs_rejects_invalid_yaml(rooted_tmp_path: RootedPath) -> None:
    pnpm_workspace_path = rooted_tmp_path.path.joinpath("pnpm-workspace.yaml")
    pnpm_workspace_path.write_text(":")

    with pytest.raises(PackageRejected):
        _read_workspace_globs(rooted_tmp_path)


def test_find_non_dev_dependencies(tmp_path: Path) -> None:
    lockfile = PnpmLock(
        path=tmp_path,
        data={
            "lockfileVersion": "9.0",
            "importers": {
                ".": {
                    "dependencies": {
                        "runtime-dep": {"specifier": "^1.7.0", "version": "1.7.9"},
                        "shared-dep": {"specifier": "^7.6.0", "version": "7.6.3"},
                        "runtime-dep-with-peers": {
                            "specifier": "^2.0.0",
                            "version": "2.0.0(peer-dep@1.1.0)",
                        },
                    },
                    "devDependencies": {
                        "shared-dep": {"specifier": "^7.6.0", "version": "7.6.3"},
                        "dev-dep": {"specifier": "^3.0.0", "version": "3.0.5"},
                    },
                    "optionalDependencies": {
                        "optional-dep": {"specifier": "~2.3.3", "version": "2.3.3"},
                    },
                },
                "another-importer": {
                    "dependencies": {
                        "workspace-runtime-dep": {
                            "specifier": "^2.30.1",
                            "version": "2.30.1",
                        },
                    },
                },
            },
            "snapshots": {
                "workspace-runtime-dep@2.30.1": {},
                "runtime-dep@1.7.9": {
                    "dependencies": {
                        "transitive-dep-with-peers": "4.4.3(peer-dep@1.1.0)",
                    },
                    "optionalDependencies": {
                        "optional-transitive-dep": "4.0.1",
                    },
                },
                "transitive-dep-with-peers@4.4.3(peer-dep@1.1.0)": {
                    "dependencies": {"deep-transitive-dep": "2.1.3"},
                },
                "deep-transitive-dep@2.1.3": {},
                "optional-transitive-dep@4.0.1": {},
                "runtime-dep-with-peers@2.0.0(peer-dep@1.1.0)": {
                    "dependencies": {"transitive-runtime-dep-c": "3.0.0"},
                },
                "transitive-runtime-dep-c@3.0.0": {},
                "shared-dep@7.6.3": {},
                "dev-dep@3.0.5": {},
                "optional-dep@2.3.3": {},
            },
        },
    )

    expected = {
        "runtime-dep@1.7.9",
        "transitive-dep-with-peers@4.4.3",
        "optional-transitive-dep@4.0.1",
        "deep-transitive-dep@2.1.3",
        "runtime-dep-with-peers@2.0.0",
        "transitive-runtime-dep-c@3.0.0",
        "shared-dep@7.6.3",
        "optional-dep@2.3.3",
        "workspace-runtime-dep@2.30.1",
    }
    assert _find_non_dev_dependencies(lockfile) == expected
