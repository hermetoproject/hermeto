# SPDX-License-Identifier: GPL-3.0-only
import json
from enum import Enum
from itertools import zip_longest
from typing import Any
from unittest import mock

import pytest
import semver

from hermeto.core.constants import Mode
from hermeto.core.errors import (
    PackageManagerError,
    PackageRejected,
    UnexpectedFormat,
    UnsupportedFeature,
)
from hermeto.core.models.input import Request
from hermeto.core.models.output import (
    Component,
    EnvironmentVariable,
)
from hermeto.core.package_managers.javascript.yarn import fetch_yarn_source
from hermeto.core.package_managers.javascript.yarn.main import (
    GitDep,
    _build_clone_url,
    _build_vcs_url,
    _clone_and_resolve_git_deps,
    _configure_yarn_version,
    _git_deps_from_lockfile,
    _resolve_yarn_project,
    _set_yarnrc_configuration,
    _strip_workspace_scripts,
    _verify_corepack_yarn_version,
)
from hermeto.core.package_managers.javascript.yarn.project import (
    PackageJson,
    Plugin,
    Project,
    YarnRc,
)
from hermeto.core.package_managers.javascript.yarn.utils import VersionsRange
from hermeto.core.rooted_path import RootedPath
from hermeto.core.utils import first_for


@pytest.fixture(scope="module")
def yarn_env_variables() -> list[EnvironmentVariable]:
    return [
        EnvironmentVariable(name="YARN_ENABLE_GLOBAL_CACHE", value="false"),
        EnvironmentVariable(name="YARN_ENABLE_IMMUTABLE_CACHE", value="false"),
        EnvironmentVariable(name="YARN_ENABLE_MIRROR", value="true"),
        EnvironmentVariable(name="YARN_GLOBAL_FOLDER", value="${output_dir}/deps/yarn"),
    ]


class YarnVersions(Enum):
    YARN_V1 = semver.VersionInfo(1, 0, 0)
    YARN_V2 = semver.VersionInfo(2, 0, 0)

    YARN_V3_RC1 = semver.VersionInfo(3, 0, 0, prerelease="rc1")
    YARN_V3 = semver.VersionInfo(3, 0, 0)
    YARN_V36_RC1 = semver.VersionInfo(3, 6, 0, prerelease="rc1")

    YARN_V4_RC1 = semver.VersionInfo(4, 0, 0, prerelease="rc1")
    YARN_V4 = semver.VersionInfo(4, 0, 0)

    YARN_V5_RC1 = semver.VersionInfo(5, 0, 0, prerelease="rc1")
    YARN_V5 = semver.VersionInfo(5, 0, 0)

    @classmethod
    def supported(cls) -> list["YarnVersions"]:
        return [cls.YARN_V3, cls.YARN_V36_RC1, cls.YARN_V4, cls.YARN_V4_RC1]

    @classmethod
    def unsupported(cls) -> list["YarnVersions"]:
        return sorted(
            set(cls.__members__.values()).difference(set(cls.supported())),
            key=lambda v: v.value,
        )


SAMPLE_PLUGINS = """
plugins:
  - path: .yarn/plugins/@yarnpkg/plugin-typescript.cjs
    spec: "@yarnpkg/plugin-typescript"
  - path: .yarn/plugins/@yarnpkg/plugin-exec.cjs
    spec: "@yarnpkg/plugin-exec"
"""


@pytest.mark.parametrize(
    "yarn_path_version, package_manager_version",
    [
        pytest.param(YarnVersions.YARN_V3.value, None, id="valid-yarnpath-no-packagemanager"),
        pytest.param(YarnVersions.YARN_V36_RC1.value, None, id="minor-version-with-prerelease"),
        pytest.param(None, YarnVersions.YARN_V3.value, id="no-yarnpath-valid-packagemanager"),
        pytest.param(
            YarnVersions.YARN_V3.value,
            YarnVersions.YARN_V3.value,
            id="matching-yarnpath-and-packagemanager",
        ),
        pytest.param(
            semver.VersionInfo(3, 0, 0),
            semver.VersionInfo(
                3, 0, 0, build="sha224.953c8233f7a92884eee2de69a1b92d1f2ec1655e66d08071ba9a02fa"
            ),
            id="matching-yarnpath-and-packagemanager-with-build",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn.main._verify_corepack_yarn_version")
@mock.patch("hermeto.core.package_managers.javascript.yarn.main.get_semver_from_package_manager")
@mock.patch("hermeto.core.package_managers.javascript.yarn.main.get_semver_from_yarn_path")
@mock.patch("hermeto.core.package_managers.javascript.yarn.project.PackageJson.write")
def test_configure_yarn_version(
    mock_package_json_write: mock.Mock,
    mock_yarn_path_semver: mock.Mock,
    mock_package_manager_semver: mock.Mock,
    mock_verify_corepack: mock.Mock,
    yarn_path_version: semver.version.Version | None,
    package_manager_version: semver.version.Version | None,
) -> None:
    mock_project = mock.Mock()
    mock_project.yarn_rc = mock.MagicMock()
    mock_project.package_json = PackageJson(mock.Mock(), {})
    mock_yarn_path_semver.return_value = yarn_path_version
    mock_package_manager_semver.return_value = package_manager_version

    _configure_yarn_version(mock_project)

    if package_manager_version is None:
        assert mock_project.package_json["packageManager"] == f"yarn@{yarn_path_version}"
        mock_package_json_write.assert_called_once()
    else:
        assert mock_project.package_json.get("packageManager") is None
        mock_package_json_write.assert_not_called()

    mock_verify_corepack.assert_called_once_with(
        yarn_path_version or package_manager_version, mock_project.source_dir
    )


@pytest.mark.parametrize(
    "corepack_yarn_version",
    [
        pytest.param("2.0.0", id="yarn_versions_do_not_match"),
        pytest.param("2", id="invalid_semver"),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_yarn_cmd")
def test_corepack_installed_correct_yarn_version_fail(
    mock_run_yarn_cmd: mock.Mock,
    corepack_yarn_version: str,
    rooted_tmp_path: RootedPath,
) -> None:
    expected_yarn_version = YarnVersions.YARN_V1.value
    mock_run_yarn_cmd.return_value = corepack_yarn_version

    with pytest.raises(PackageManagerError):
        _verify_corepack_yarn_version(expected_yarn_version, rooted_tmp_path)

    mock_run_yarn_cmd.assert_called_once_with(
        ["--version"],
        rooted_tmp_path,
        env={"COREPACK_ENABLE_DOWNLOAD_PROMPT": "0", "YARN_IGNORE_PATH": "true"},
    )


@pytest.mark.parametrize(
    "yarn_path_version, package_manager_version, expected_error",
    [
        pytest.param(
            None,
            None,
            PackageRejected(
                "Unable to determine the yarn version to use to process the request",
                solution="Ensure that either yarnPath is defined in .yarnrc or that packageManager is defined in package.json",
            ),
            id="no-yarnpath-no-packagemanager",
        ),
        pytest.param(
            None,
            UnexpectedFormat("some error about packageManager formatting"),
            UnexpectedFormat("some error about packageManager formatting"),
            id="exception-parsing-packagemanager",
        ),
        pytest.param(
            semver.VersionInfo(3, 0, 1),
            semver.VersionInfo(3, 0, 0),
            PackageRejected(
                "Mismatch between the yarn versions specified by yarnPath (yarn@3.0.1) and packageManager (yarn@3.0.0)",
                solution="Ensure that the yarnPath version in .yarnrc and the packageManager version in package.json agree",
            ),
            id="yarnpath-packagemanager-mismatch",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn.main.get_semver_from_package_manager")
@mock.patch("hermeto.core.package_managers.javascript.yarn.main.get_semver_from_yarn_path")
def test_configure_yarn_version_fail(
    mock_yarn_path_semver: mock.Mock,
    mock_package_manager_semver: mock.Mock,
    yarn_path_version: semver.version.Version | None,
    package_manager_version: semver.version.Version | None | Exception,
    expected_error: Exception,
) -> None:
    mock_project = mock.Mock()
    mock_project.yarn_rc = mock.MagicMock()
    mock_project.package_json = mock.MagicMock()
    mock_yarn_path_semver.return_value = yarn_path_version
    mock_package_manager_semver.side_effect = [package_manager_version]

    with pytest.raises(type(expected_error)):
        _configure_yarn_version(mock_project)


YARN_VERSIONS = [yarn_version.value for yarn_version in YarnVersions.unsupported()]


@pytest.mark.parametrize(
    "package_manager_version, yarn_path_version",
    [
        pytest.param(
            pkg_mgr_version,
            yarn_path_version,
            id=f"package_manager,yarn_path-({str(pkg_mgr_version)}, {str(yarn_path_version)})",
        )
        for pkg_mgr_version, yarn_path_version in zip_longest(YARN_VERSIONS, YARN_VERSIONS[:1])
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn.main.get_semver_from_package_manager")
@mock.patch("hermeto.core.package_managers.javascript.yarn.main.get_semver_from_yarn_path")
def test_yarn_unsupported_version_fail(
    mock_yarn_path_semver: mock.Mock,
    mock_package_manager_semver: mock.Mock,
    package_manager_version: semver.version.Version | None | Exception,
    yarn_path_version: semver.version.Version,
) -> None:
    mock_project = mock.Mock()
    mock_project.yarn_rc = mock.MagicMock()
    mock_project.package_json = mock.MagicMock()
    mock_yarn_path_semver.return_value = None
    mock_package_manager_semver.return_value = package_manager_version

    with pytest.raises(PackageRejected):
        _configure_yarn_version(mock_project)


@pytest.mark.parametrize(
    "yarn_rc_content, expected_plugins, yarn_version",
    [
        pytest.param("", [], "3.0.0", id="empty_yarn_rc"),
        pytest.param(
            SAMPLE_PLUGINS,
            [
                {
                    "path": ".yarn/plugins/@yarnpkg/plugin-exec.cjs",
                    "spec": "@yarnpkg/plugin-exec",
                },
            ],
            "3.0.0",
            id="yarn_rc_with_default_plugins",
        ),
        pytest.param("", [], "4.0.0", id="yarn_v4"),
        pytest.param("", [], "4.0.0-rc1", id="yarn_v4_rc1"),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn.project.YarnRc.write")
def test_set_yarnrc_configuration(
    mock_write: mock.Mock,
    yarn_rc_content: str,
    expected_plugins: list[Plugin],
    yarn_version: semver.Version,
    rooted_tmp_path: RootedPath,
) -> None:
    yarn_rc_path = rooted_tmp_path.join_within_root(".yarnrc.yml")
    with open(yarn_rc_path, "w") as f:
        f.write(yarn_rc_content)
    yarn_rc = YarnRc.from_file(yarn_rc_path)

    project = mock.Mock()
    project.yarn_rc = yarn_rc
    project.package_json = mock.MagicMock()
    output_dir = rooted_tmp_path.join_within_root("output")

    _set_yarnrc_configuration(project, output_dir, yarn_version)

    expected_data = {
        "checksumBehavior": "throw",
        "enableGlobalCache": True,
        "enableImmutableInstalls": True,
        "enableMirror": False,
        "enableScripts": False,
        "enableStrictSsl": True,
        "enableTelemetry": False,
        "globalFolder": f"{output_dir}/deps/yarn",
        "ignorePath": True,
        "unsafeHttpWhitelist": [],
        "pnpMode": "strict",
        "plugins": expected_plugins,
    }

    if yarn_version in VersionsRange("4.0.0-rc1", "5.0.0"):
        expected_data["enableConstraintsChecks"] = False

    assert yarn_rc.data == expected_data
    mock_write.assert_called_once()


@mock.patch("hermeto.core.package_managers.javascript.yarn.main._configure_yarn_version")
def test_workspace_focus_rejected_for_yarn_v3(
    mock_configure_version: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    """Workspace focus is rejected when the project uses Yarn v3."""
    mock_configure_version.return_value = semver.Version.parse("3.6.1")
    project = mock.Mock(source_dir=rooted_tmp_path)

    with pytest.raises(PackageRejected):
        _resolve_yarn_project(project, rooted_tmp_path, Mode.STRICT, workspaces=["app"])


def test_strip_workspace_scripts(rooted_tmp_path: RootedPath) -> None:
    """Scripts are removed from workspace package.json files matched by workspaces globs."""
    root_json = rooted_tmp_path.join_within_root("package.json")
    root_json.path.write_text(json.dumps({"name": "root", "workspaces": ["packages/app"]}))

    pkg_a_dir = rooted_tmp_path.join_within_root("packages", "app")
    pkg_a_dir.path.mkdir(parents=True)
    pkg_a_json = pkg_a_dir.join_within_root("package.json")
    pkg_a_json.path.write_text(
        json.dumps({"name": "app", "scripts": {"build": "tsc", "postinstall": "echo hi"}})
    )

    pkg_b_dir = rooted_tmp_path.join_within_root("packages", "unrelated")
    pkg_b_dir.path.mkdir(parents=True)
    pkg_b_json = pkg_b_dir.join_within_root("package.json")
    pkg_b_json.path.write_text(
        json.dumps({"name": "unrelated", "scripts": {"postinstall": "echo bad"}})
    )

    _strip_workspace_scripts(rooted_tmp_path)

    data_a = json.loads(pkg_a_json.path.read_text())
    assert "scripts" not in data_a

    # Workspace not matched by the workspaces glob is left untouched
    data_b = json.loads(pkg_b_json.path.read_text())
    assert data_b["scripts"] == {"postinstall": "echo bad"}


# --- Tests for git dependency support ---


@pytest.mark.parametrize(
    "input_request",
    [pytest.param([{"type": "yarn", "path": "."}], id="single_package")],
    indirect=["input_request"],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn.main._resolve_yarn_project")
@mock.patch("hermeto.core.package_managers.javascript.yarn.project.Project.from_source_dir")
def test_fetch_yarn_source_git_dep_annotation(
    mock_project_from_source_dir: mock.Mock,
    mock_resolve_yarn: mock.Mock,
    input_request: Request,
    yarn_env_variables: list[EnvironmentVariable],
) -> None:
    mock_project = mock.Mock()
    mock_project_from_source_dir.return_value = mock_project

    components = [
        Component(
            name="foo",
            purl="pkg:npm/foo@1.0.0?vcs_url=git+https://github.com/owner/foo.git@abc123",
            version="1.0.0",
        )
    ]
    git_deps = [GitDep(name="foo", clone_url="https://github.com/owner/foo.git", ref="abc123")]
    mock_resolve_yarn.return_value = (components, [], git_deps)

    output = fetch_yarn_source(input_request)

    git_ann = first_for(
        lambda a: a.text == "hermeto:permissive-mode:yarn:using-git-dependencies",
        output.annotations,
        None,
    )
    assert git_ann is not None
    assert components[0].bom_ref in git_ann.subjects


def _make_project(package_json_data: dict[str, Any] | None = None) -> Project:
    """Build an in-memory Project without writing files to disk."""
    source_dir = RootedPath("/fake/project")
    yarn_rc = YarnRc(source_dir.join_within_root(".yarnrc.yml"), {})
    package_json = PackageJson(
        source_dir.join_within_root("package.json").path,
        package_json_data or {"name": "test-project", "version": "1.0.0"},
    )
    return Project(source_dir=source_dir, yarn_rc=yarn_rc, package_json=package_json)


# A comprehensive lockfile that exercises multiple code paths in one test:
# HTTPS git dep, SSH git dep, scoped git dep, npm dep (skipped), __metadata (skipped).
MIXED_LOCKFILE = {
    "__metadata": {"version": 8, "cacheKey": "10c0"},
    "lodash@npm:4.17.21": {
        "version": "4.17.21",
        "resolution": "lodash@npm:4.17.21",
    },
    "c2-wo-deps@https://bitbucket.org/cachi-testing/cachi2-without-deps.git#commit=9e164b97": {
        "version": "1.0.0",
        "resolution": "c2-wo-deps@https://bitbucket.org/cachi-testing/cachi2-without-deps.git#commit=9e164b97",
    },
    "ccto-wo-deps@git@github.com:cachito-testing/cachito-npm-without-deps.git#commit=2f0ce1d7": {
        "version": "1.0.0",
        "resolution": "ccto-wo-deps@git@github.com:cachito-testing/cachito-npm-without-deps.git#commit=2f0ce1d7",
    },
    "@databricks/json-bigint@https://github.com/databricks/json-bigint.git#commit=a1defaf9": {
        "version": "0.2.3",
        "resolution": "@databricks/json-bigint@https://github.com/databricks/json-bigint.git#commit=a1defaf9",
    },
}


class TestParseLockfileGitDeps:
    def test_parses_mixed_lockfile(self) -> None:
        result = _git_deps_from_lockfile(MIXED_LOCKFILE)

        # Should find 3 git deps (HTTPS, SSH, scoped) and skip __metadata + npm
        assert len(result) == 3

        by_name = {d.name: d for d in result}

        # HTTPS git dep
        assert by_name["c2-wo-deps"].clone_url == (
            "https://bitbucket.org/cachi-testing/cachi2-without-deps.git"
        )
        assert by_name["c2-wo-deps"].ref == "9e164b97"

        # SSH (SCP-style) git dep
        assert by_name["ccto-wo-deps"].clone_url == (
            "git@github.com:cachito-testing/cachito-npm-without-deps.git"
        )
        assert by_name["ccto-wo-deps"].ref == "2f0ce1d7"

        # Scoped git dep
        assert by_name["@databricks/json-bigint"].clone_url == (
            "https://github.com/databricks/json-bigint.git"
        )
        assert by_name["@databricks/json-bigint"].ref == "a1defaf9"

    def test_empty_lockfile(self) -> None:
        assert _git_deps_from_lockfile({}) == []

    @pytest.mark.parametrize(
        "lockfile",
        [
            {
                "ccto-wo-deps@patch:ccto-wo-deps@git@github.com%3Acachito-testing/cachito-npm-without-deps.git%23commit=2f0ce1d7b1f8b35572d919428b965285a69583f6#./.yarn/patches/ccto-wo-deps-git@github.com-e0fce8c89c.patch::version=1.0.0&hash=51a91f&locator=berryscary%40workspace%3A.": {
                    "version": "1.0.0",
                    "resolution": "ccto-wo-deps@patch:ccto-wo-deps@git@github.com%3Acachito-testing/cachito-npm-without-deps.git%23commit=2f0ce1d7b1f8b35572d919428b965285a69583f6#./.yarn/patches/ccto-wo-deps-git@github.com-e0fce8c89c.patch::version=1.0.0&hash=51a91f&locator=berryscary%40workspace%3A.",
                },
            },
            {
                "npm-lifecycle-scripts@https://github.com/chmeliik/js-lifecycle-scripts.git#workspace=my-workspace&commit=0e786c88d5aca79a68428dadaed4b096bf2ae3e0": {
                    "version": "1.0.0",
                    "resolution": "npm-lifecycle-scripts@https://github.com/chmeliik/js-lifecycle-scripts.git#workspace=my-workspace&commit=0e786c88d5aca79a68428dadaed4b096bf2ae3e0",
                },
            },
        ],
        ids=["patched-git", "workspace-git"],
    )
    def test_rejects_unsupported_git_deps(self, lockfile: dict) -> None:
        with pytest.raises(
            UnsupportedFeature,
            match="cannot process this Yarn Berry Git dependency",
        ):
            _git_deps_from_lockfile(lockfile)

    def test_ignores_patches_of_non_git_deps(self) -> None:
        lockfile = {
            "left-pad@patch:left-pad@npm%3A1.3.0#./.yarn/patches/left-pad.patch"
            "::version=1.3.0&hash=abc123&locator=berryscary%40workspace%3A.": {
                "version": "1.3.0",
                "resolution": (
                    "left-pad@patch:left-pad@npm%3A1.3.0#./.yarn/patches/left-pad.patch"
                    "::version=1.3.0&hash=abc123&locator=berryscary%40workspace%3A."
                ),
            },
        }

        assert _git_deps_from_lockfile(lockfile) == []

    def test_extracts_plain_git_despite_npm_patches(self) -> None:
        lockfile = {
            "foo@https://github.com/owner/foo.git#commit=abc123": {
                "version": "1.0.0",
                "resolution": "foo@https://github.com/owner/foo.git#commit=abc123",
            },
            "left-pad@patch:left-pad@npm%3A1.3.0#./.yarn/patches/left-pad.patch"
            "::version=1.3.0&hash=abc123&locator=berryscary%40workspace%3A.": {
                "version": "1.3.0",
                "resolution": (
                    "left-pad@patch:left-pad@npm%3A1.3.0#./.yarn/patches/left-pad.patch"
                    "::version=1.3.0&hash=abc123&locator=berryscary%40workspace%3A."
                ),
            },
        }

        result = _git_deps_from_lockfile(lockfile)
        assert len(result) == 1
        assert result[0].name == "foo"


@pytest.mark.parametrize(
    "protocol, source, expected",
    [
        ("https", "//github.com/owner/repo.git", "https://github.com/owner/repo.git"),
        ("git@github.com", "cachito-testing/repo.git", "git@github.com:cachito-testing/repo.git"),
        ("git+ssh", "//git@github.com/owner/repo.git", "ssh://git@github.com/owner/repo.git"),
        ("git+https", "//github.com/owner/repo.git", "https://github.com/owner/repo.git"),
    ],
)
def test_build_clone_url(protocol: str, source: str, expected: str) -> None:
    assert _build_clone_url(protocol, source) == expected


def test_build_clone_url_invalid() -> None:
    with pytest.raises(PackageRejected, match="Cannot construct clone URL"):
        _build_clone_url(None, "//github.com/owner/repo.git")


@pytest.mark.parametrize(
    "clone_url, ref, expected",
    [
        (
            "https://github.com/owner/foo.git",
            "abc123",
            "git+https://github.com/owner/foo.git@abc123",
        ),
        (
            "git@github.com:owner/bar.git",
            "def456",
            "git+ssh://git@github.com/owner/bar.git@def456",
        ),
        (
            "ssh://git@github.com/owner/baz.git",
            "abc999",
            "git+ssh://git@github.com/owner/baz.git@abc999",
        ),
    ],
)
def test_build_vcs_url(clone_url: str, ref: str, expected: str) -> None:
    dep = GitDep(name="pkg", clone_url=clone_url, ref=ref)
    assert _build_vcs_url(dep) == expected


class TestCloneAndResolveGitDeps:
    @mock.patch("hermeto.core.package_managers.javascript.yarn.main.clone_repo_pack_archive")
    def test_clones_writes_relative_resolutions_and_dedupes(self, mock_clone: mock.Mock) -> None:
        project = _make_project()
        output_dir = RootedPath("/fake/output")
        tarball = output_dir.join_within_root(
            "deps/yarn/github.com/owner/my-dep/my-dep-external-gitcommit-abc123.tgz"
        )
        mock_clone.return_value = tarball

        git_deps: list[GitDep] = [
            GitDep(
                name="my-dep",
                clone_url="https://github.com/owner/my-dep.git",
                ref="abc123",
            ),
            GitDep(
                name="my-dep-alias",
                clone_url="https://github.com/owner/my-dep.git",
                ref="abc123",
            ),
        ]

        with mock.patch.object(project.package_json, "write") as mock_write:
            project_files, tarball_vcs_url_map = _clone_and_resolve_git_deps(
                project, git_deps, output_dir
            )

        # clone_repo_pack_archive called only once for the deduped source
        mock_clone.assert_called_once()
        tarball_path = str(tarball.path)
        assert "my-dep-external-gitcommit-abc123.tgz" in tarball_path

        # Verify package.json resolutions were updated in memory
        resolutions = project.package_json.data["resolutions"]
        for name in ("my-dep", "my-dep-alias"):
            assert name in resolutions
            assert resolutions[name].startswith("file:")
        mock_write.assert_called_once()

        # Verify ProjectFile template uses ${output_dir}
        assert len(project_files) == 1
        pf_template = json.loads(project_files[0].template)
        assert "${output_dir}" in pf_template["resolutions"]["my-dep"]
        assert "${output_dir}" in pf_template["resolutions"]["my-dep-alias"]

        assert len(tarball_vcs_url_map) == 1
        assert tarball_path in tarball_vcs_url_map
        assert tarball_vcs_url_map[tarball_path] == "git+https://github.com/owner/my-dep.git@abc123"

    def test_rejects_name_collision(self) -> None:
        project = _make_project()
        output_dir = RootedPath("/fake/output")

        git_deps: list[GitDep] = [
            GitDep(name="my-dep", clone_url="https://github.com/owner/repo-a.git", ref="aaa"),
            GitDep(name="my-dep", clone_url="https://github.com/owner/repo-b.git", ref="bbb"),
        ]

        with pytest.raises(PackageRejected, match="Multiple git dependencies share the name"):
            _clone_and_resolve_git_deps(project, git_deps, output_dir)

    @mock.patch("hermeto.core.package_managers.javascript.yarn.main.clone_repo_pack_archive")
    def test_dedupes_alternate_url_spellings(self, mock_clone: mock.Mock) -> None:
        """https and SSH URLs for the same host/ns/repo/ref share one tarball and vcs_url."""
        project = _make_project()
        output_dir = RootedPath("/fake/output")
        tarball = output_dir.join_within_root(
            "deps/yarn/github.com/owner/my-dep/my-dep-external-gitcommit-abc123.tgz"
        )
        mock_clone.return_value = tarball

        git_deps: list[GitDep] = [
            GitDep(
                name="my-dep",
                clone_url="https://github.com/owner/my-dep.git",
                ref="abc123",
            ),
            GitDep(
                name="my-dep-ssh",
                clone_url="git@github.com:owner/my-dep.git",
                ref="abc123",
            ),
        ]

        with mock.patch.object(project.package_json, "write"):
            _, tarball_vcs_url_map = _clone_and_resolve_git_deps(project, git_deps, output_dir)

        mock_clone.assert_called_once()
        tarball_path = str(tarball.path)
        assert tarball_vcs_url_map == {
            tarball_path: "git+https://github.com/owner/my-dep.git@abc123",
        }
        resolutions = project.package_json.data["resolutions"]
        assert "my-dep" in resolutions
        assert "my-dep-ssh" in resolutions


class TestSetYarnrcConfigurationGitDeps:
    def test_immutable_installs_always_true(self) -> None:
        project = _make_project()
        output_dir = RootedPath("/fake/output")
        version = semver.Version.parse("4.0.0")

        with mock.patch.object(project.yarn_rc, "write") as mock_write:
            _set_yarnrc_configuration(project, output_dir, version)

        assert project.yarn_rc["enableImmutableInstalls"] is True
        mock_write.assert_called_once()


class TestResolveYarnProjectGitDepsStrictMode:
    @mock.patch("hermeto.core.package_managers.javascript.yarn.main._parse_lockfile_git_deps")
    @mock.patch("hermeto.core.package_managers.javascript.yarn.main._verify_corepack_yarn_version")
    @mock.patch(
        "hermeto.core.package_managers.javascript.yarn.main.get_semver_from_package_manager"
    )
    @mock.patch("hermeto.core.package_managers.javascript.yarn.main.get_semver_from_yarn_path")
    def test_rejects_git_deps_in_strict_mode(
        self,
        mock_yarn_path: mock.Mock,
        mock_pkg_mgr: mock.Mock,
        mock_verify_corepack: mock.Mock,
        mock_parse_git_deps: mock.Mock,
    ) -> None:
        mock_yarn_path.return_value = semver.Version.parse("4.0.0")
        mock_pkg_mgr.return_value = semver.Version.parse("4.0.0")
        mock_parse_git_deps.return_value = [
            GitDep(
                name="my-dep",
                clone_url="https://github.com/owner/my-dep.git",
                ref="abc123",
            )
        ]

        project = _make_project()
        output_dir = RootedPath("/fake/output")

        with pytest.raises(PackageRejected, match="cannot be processed in strict mode"):
            _resolve_yarn_project(project, output_dir, Mode.STRICT)
