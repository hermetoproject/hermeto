# SPDX-License-Identifier: GPL-3.0-only
import io
import json
import re
import tarfile
from unittest import mock
from urllib.parse import quote

import git
import pytest
from pyarn.lockfile import Package as PYarnPackage

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo, PackageRejected, UnexpectedFormat
from hermeto.core.package_managers.yarn_classic.main import MIRROR_DIR
from hermeto.core.package_managers.yarn_classic.project import PackageJson
from hermeto.core.package_managers.yarn_classic.resolver import (
    FilePackage,
    GitPackage,
    LinkPackage,
    RegistryPackage,
    UrlPackage,
    WorkspacePackage,
    YarnClassicPackage,
    _get_main_package,
    _get_packages_from_lockfile,
    _get_workspace_packages,
    _is_from_npm_registry,
    _is_git_url,
    _is_tarball_url,
    _read_name_from_tarball,
    _YarnClassicPackageFactory,
    resolve_packages,
)
from hermeto.core.package_managers.yarn_classic.workspaces import Workspace
from hermeto.core.rooted_path import PathOutsideRoot, RootedPath
from hermeto.core.scm import get_repo_id

VALID_GIT_URLS = [
    "git://git.host.com/some/path",
    "ssh://git.host.com/some/path",
    "git+http://git.host.com/some/path",
    "git+https://git.host.com/some/path",
    "git+ssh://git.host.com/some/path",
    "git+file://git.host.com/some/path",
    "git+file://git.host.com/some/path",
    "http://git.host.com/some/path.git",
    "https://git.host.com/some/path.git",
    "http://git.host.com/some/path.git#fffffff",
    "https://github.com/some/path",
    "https://gitlab.com/some/path",
    "https://bitbucket.com/some/path",
    "https://bitbucket.org/some/path",
]
VALID_TARBALL_URLS = [
    "https://foo.com/bar.tar.gz",
    "https://foo.com/bar.tgz",
    "https://foo.com/bar.tar",
    "http://foo.com/bar.tar.gz",
    "http://foo.com/bar.tgz",
    "http://foo.com/bar.tar",
    "https://codeload.github.com/org/foo/tar.gz/fffffff",
]
INVALID_GIT_URLS = [
    "https://github.com/some/path/file",
    "ftp://foo.com/bar.tar",
    "https://foo.com/bar",
    "https://foo.com/bar.txt",
    *VALID_TARBALL_URLS,
]
INVALID_TARBALL_URLS = [
    "ftp://foo.com/bar.tar",
    "git+https://git.host.com/some/path",
    "https://foo.com/bar",
    "https://foo.com/bar.txt",
    *VALID_GIT_URLS,
]


@pytest.mark.parametrize("url", VALID_TARBALL_URLS)
def test_is_tarball_url_can_parse_correct_tarball_urls(url: str) -> None:
    assert _is_tarball_url(url)


@pytest.mark.parametrize("url", INVALID_TARBALL_URLS)
def test_is_tarball_url_rejects_incorrect_tarball_urls(url: str) -> None:
    assert not _is_tarball_url(url)


@pytest.mark.parametrize("url", VALID_GIT_URLS)
def test_is_git_url_can_parse_correct_git_urls(url: str) -> None:
    assert _is_git_url(url)


@pytest.mark.parametrize("url", INVALID_GIT_URLS)
def test_is_git_url_rejects_incorrect_git_urls(url: str) -> None:
    assert not _is_git_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://registry.npmjs.org/chai/-/chai-4.2.0.tgz",
        "https://registry.yarnpkg.com/chai/-/chai-4.2.0.tgz",
    ],
)
def test_is_from_npm_registry_can_parse_correct_registry_urls(url: str) -> None:
    assert _is_from_npm_registry(url)


def test_is_from_npm_registry_can_parse_incorrect_registry_urls() -> None:
    assert not _is_from_npm_registry("https://example.org/fecha.tar.gz")


@mock.patch("hermeto.core.package_managers.yarn_classic.resolver._read_name_from_tarball")
def test_create_package_from_pyarn_package(
    mock_read_name_from_tarball: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    test_cases: list[tuple[PYarnPackage, YarnClassicPackage]] = [
        (
            PYarnPackage(
                name="foo",
                version="1.0.0",
                url="https://registry.yarnpkg.com/foo/-/foo-1.0.0.tgz#fffffff",
                checksum="sha512-fffffff",
            ),
            RegistryPackage(
                name="foo",
                version="1.0.0",
                url="https://registry.yarnpkg.com/foo/-/foo-1.0.0.tgz#fffffff",
                dev=False,
                integrity="sha512-fffffff",
            ),
        ),
        (
            PYarnPackage(
                name="foo",
                version="1.0.0",
                url="./path/foo-1.0.0.tgz#fffffff",
                path="path/foo-1.0.0.tgz",
            ),
            FilePackage(
                name="foo",
                version="1.0.0",
                dev=False,
                path=rooted_tmp_path.join_within_root("path/foo-1.0.0.tgz"),
            ),
        ),
        (
            PYarnPackage(
                name="foo",
                version="0.0.0",
                path="link",
            ),
            LinkPackage(
                name="foo",
                version="0.0.0",
                dev=False,
                path=rooted_tmp_path.join_within_root("link"),
            ),
        ),
        (
            PYarnPackage(
                name="foo",
                version="1.0.0",
                url="https://github.com/org/foo.git#fffffff",
            ),
            GitPackage(
                name="foo",
                version="1.0.0",
                dev=True,
                url="https://github.com/org/foo.git#fffffff",
            ),
        ),
        (
            PYarnPackage(
                name="foo",
                version="1.0.0",
                url="https://example.com/foo-1.0.0.tgz",
            ),
            UrlPackage(
                name="foo",
                version="1.0.0",
                dev=True,
                url="https://example.com/foo-1.0.0.tgz",
            ),
        ),
    ]

    for pyarn_package, expected_package in test_cases:
        mock_read_name_from_tarball.return_value = expected_package.name
        runtime_deps = (
            set()
            if expected_package.dev
            else set({f"{pyarn_package.name}@{pyarn_package.version}"})
        )

        package_factory = _YarnClassicPackageFactory(rooted_tmp_path, rooted_tmp_path, runtime_deps)
        assert package_factory.create_package_from_pyarn_package(pyarn_package) == expected_package


def test_create_package_from_pyarn_package_fail_absolute_path(rooted_tmp_path: RootedPath) -> None:
    pyarn_package = PYarnPackage(
        name="foo",
        version="1.0.0",
        path="/root/some/path",
    )
    error_msg = (
        f"The package {pyarn_package.name}@{pyarn_package.version} has an absolute path "
        f"({pyarn_package.path}), which is not permitted."
    )

    package_factory = _YarnClassicPackageFactory(rooted_tmp_path, rooted_tmp_path, set())
    with pytest.raises(PackageRejected, match=re.escape(error_msg)):
        package_factory.create_package_from_pyarn_package(pyarn_package)


def test_create_package_from_pyarn_package_fail_path_outside_root(
    rooted_tmp_path: RootedPath,
) -> None:
    pyarn_package = PYarnPackage(
        name="foo",
        version="1.0.0",
        path="../path/outside/root",
    )

    package_factory = _YarnClassicPackageFactory(rooted_tmp_path, rooted_tmp_path, set())
    with pytest.raises(PathOutsideRoot):
        package_factory.create_package_from_pyarn_package(pyarn_package)


def test_create_package_from_pyarn_package_fail_unexpected_format(
    rooted_tmp_path: RootedPath,
) -> None:
    pyarn_package = PYarnPackage(
        name="foo",
        version="1.0.0",
        url="ftp://some-tarball.tgz",
    )

    package_factory = _YarnClassicPackageFactory(rooted_tmp_path, rooted_tmp_path, set())
    with pytest.raises(UnexpectedFormat):
        package_factory.create_package_from_pyarn_package(pyarn_package)


@mock.patch(
    "hermeto.core.package_managers.yarn_classic.resolver._YarnClassicPackageFactory.create_package_from_pyarn_package"
)
def test_get_packages_from_lockfile(
    mock_create_package: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    # Setup lockfile instance
    mock_pyarn_lockfile = mock.Mock()
    mock_yarn_lock = mock.Mock(yarn_lockfile=mock_pyarn_lockfile)
    mock_pyarn_package_1 = mock.Mock()
    mock_pyarn_package_2 = mock.Mock()
    mock_pyarn_lockfile.packages.return_value = [mock_pyarn_package_1, mock_pyarn_package_2]

    # Setup classifier
    mock_package_1 = mock.Mock()
    mock_package_2 = mock.Mock()
    mock_create_package.side_effect = [mock_package_1, mock_package_2]
    create_package_expected_calls = [
        mock.call(mock_pyarn_package_1),
        mock.call(mock_pyarn_package_2),
    ]

    output = _get_packages_from_lockfile(rooted_tmp_path, rooted_tmp_path, mock_yarn_lock, set())

    mock_pyarn_lockfile.packages.assert_called_once()
    mock_create_package.assert_has_calls(create_package_expected_calls)
    assert output == [mock_package_1, mock_package_2]


@mock.patch("hermeto.core.package_managers.yarn_classic.project.YarnLock.from_file")
@mock.patch("hermeto.core.package_managers.yarn_classic.resolver._get_workspace_packages")
@mock.patch("hermeto.core.package_managers.yarn_classic.resolver.extract_workspace_metadata")
@mock.patch("hermeto.core.package_managers.yarn_classic.resolver._get_packages_from_lockfile")
@mock.patch("hermeto.core.package_managers.yarn_classic.resolver._get_main_package")
@mock.patch("hermeto.core.package_managers.yarn_classic.resolver.find_runtime_deps")
def test_resolve_packages(
    find_runtime_deps: mock.Mock,
    mock_get_main_package: mock.Mock,
    mock_get_lockfile_packages: mock.Mock,
    mock_extract_workspaces: mock.Mock,
    mock_get_workspace_packages: mock.Mock,
    mock_get_yarn_lock: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    project = mock.Mock(source_dir=rooted_tmp_path)
    yarn_lock_path = rooted_tmp_path.join_within_root("yarn.lock")

    main_package = mock.Mock()
    workspace_packages = [mock.Mock()]
    lockfile_packages = [mock.Mock(), mock.Mock()]
    expected_output = [main_package, *workspace_packages, *lockfile_packages]

    find_runtime_deps.return_value = set()
    mock_get_main_package.return_value = main_package
    mock_get_lockfile_packages.return_value = lockfile_packages
    mock_get_workspace_packages.return_value = workspace_packages

    output = resolve_packages(project, rooted_tmp_path.join_within_root(MIRROR_DIR))
    mock_extract_workspaces.assert_called_once_with(rooted_tmp_path)
    mock_get_yarn_lock.assert_called_once_with(yarn_lock_path)
    mock_get_main_package.assert_called_once_with(project.source_dir, project.package_json)
    mock_get_workspace_packages.assert_called_once_with(
        rooted_tmp_path, mock_extract_workspaces.return_value
    )
    mock_get_lockfile_packages.assert_called_once_with(
        rooted_tmp_path,
        rooted_tmp_path.join_within_root(MIRROR_DIR),
        mock_get_yarn_lock.return_value,
        find_runtime_deps.return_value,
    )
    assert list(output) == expected_output


def test_get_main_package(rooted_tmp_path: RootedPath) -> None:
    package_json = PackageJson(
        _path=rooted_tmp_path.join_within_root("package.json"),
        _data={"name": "foo", "version": "1.0.0"},
    )
    expected_output = WorkspacePackage(
        name="foo",
        version="1.0.0",
        path=rooted_tmp_path,
    )

    output = _get_main_package(rooted_tmp_path, package_json)
    assert output == expected_output


def test_get_main_package_no_name(rooted_tmp_path: RootedPath) -> None:
    package_json = PackageJson(
        _path=rooted_tmp_path.join_within_root("package.json"),
        _data={},
    )
    error_msg = (
        f"The package.json file located at {package_json._path.path} is missing the name field"
    )

    with pytest.raises(PackageRejected, match=error_msg):
        _get_main_package(rooted_tmp_path, package_json)


def test_get_workspace_packages(rooted_tmp_path: RootedPath) -> None:
    workspace_path = rooted_tmp_path.join_within_root("foo")
    workspace_path.path.mkdir()

    package_json_path = workspace_path.join_within_root("package.json")
    package_json_path.path.write_text('{"name": "foo", "version": "1.0.0"}')

    package_json = PackageJson.from_file(package_json_path)
    workspace = Workspace(
        path=workspace_path.path,
        package_json=package_json,
    )

    expected = [
        WorkspacePackage(
            name="foo",
            version="1.0.0",
            path=workspace_path,
        )
    ]

    output = _get_workspace_packages(rooted_tmp_path, [workspace])
    assert output == expected


def test_package_purl(rooted_tmp_path_repo: RootedPath) -> None:
    repo = git.Repo(rooted_tmp_path_repo)
    repo.create_remote("origin", "https://github.com/org/repo.git")

    example_repo_id = get_repo_id(repo)
    example_vcs_url = example_repo_id.as_vcs_url_qualifier()
    purl_vcs_url = quote(example_vcs_url, safe=":/")

    example_sri_integrity = "sha512-GRaAEriuT4zp9N4p1i8BDBYmEyfo+xQ3yHjJU4eiK5NDa1RmUZG+unZABUTK4/Ox/M+GaHwb6Ow8rUITrtjszA=="
    example_checksum = ChecksumInfo.from_sri(example_sri_integrity)

    yarn_classic_packages: list[tuple[YarnClassicPackage, str]] = [
        (
            RegistryPackage(
                name="npm-registry-pkg",
                version="1.0.0",
                integrity=example_sri_integrity,
                url="https://registry.npmjs.org",
            ),
            f"pkg:npm/npm-registry-pkg@1.0.0?checksum={str(example_checksum)}",
        ),
        (
            RegistryPackage(
                name="yarn-registry-pkg",
                version="2.0.0",
                integrity=example_sri_integrity,
                url="https://registry.yarnpkg.com",
            ),
            f"pkg:npm/yarn-registry-pkg@2.0.0?checksum={str(example_checksum)}&repository_url=https://registry.yarnpkg.com",
        ),
        (
            GitPackage(
                name="git-pkg",
                version="3.0.0",
                url=f"https://github.com/org/repo.git#{repo.head.commit.hexsha}",
            ),
            f"pkg:npm/git-pkg@3.0.0?vcs_url={purl_vcs_url}",
        ),
        (
            UrlPackage(
                name="url-pkg",
                version="4.0.0",
                url="https://example.com/package.tar.gz",
            ),
            "pkg:npm/url-pkg@4.0.0?download_url=https://example.com/package.tar.gz",
        ),
        (
            FilePackage(
                name="file-pkg",
                version="5.0.0",
                path=rooted_tmp_path_repo.join_within_root("path/to/package"),
            ),
            f"pkg:npm/file-pkg@5.0.0?vcs_url={purl_vcs_url}#path/to/package",
        ),
        (
            WorkspacePackage(
                name="workspace-pkg",
                version="6.0.0",
                path=rooted_tmp_path_repo.join_within_root("workspace/package"),
            ),
            f"pkg:npm/workspace-pkg@6.0.0?vcs_url={purl_vcs_url}#workspace/package",
        ),
        (
            LinkPackage(
                name="link-pkg",
                version="7.0.0",
                path=rooted_tmp_path_repo.join_within_root("link/to/package"),
            ),
            f"pkg:npm/link-pkg@7.0.0?vcs_url={purl_vcs_url}#link/to/package",
        ),
    ]

    for package, expected_purl in yarn_classic_packages:
        assert package.purl == expected_purl


def mock_tarball(path: RootedPath, package_json_content: dict[str, str]) -> RootedPath:
    tarball_path = path.join_within_root("package.tar.gz")

    if not package_json_content:
        with tarfile.open(tarball_path, mode="w:gz") as tar:
            tar.addfile(tarfile.TarInfo(name="foo"), io.BytesIO())

        return tarball_path

    with tarfile.open(tarball_path, mode="w:gz") as tar:
        package_json_bytes = json.dumps(package_json_content).encode("utf-8")
        info = tarfile.TarInfo(name="package.json")
        info.size = len(package_json_bytes)
        tar.addfile(info, io.BytesIO(package_json_bytes))

    return tarball_path


def test_successful_name_extraction(rooted_tmp_path: RootedPath) -> None:
    tarball_path = mock_tarball(path=rooted_tmp_path, package_json_content={"name": "foo"})
    assert _read_name_from_tarball(tarball_path) == "foo"


def test_no_package_json(rooted_tmp_path: RootedPath) -> None:
    tarball_path = mock_tarball(path=rooted_tmp_path, package_json_content={})
    with pytest.raises(ValueError, match="No package.json found"):
        _read_name_from_tarball(tarball_path)


def test_missing_name_field(rooted_tmp_path: RootedPath) -> None:
    tarball_path = mock_tarball(path=rooted_tmp_path, package_json_content={"key": "foo"})
    with pytest.raises(ValueError, match="No 'name' field found"):
        _read_name_from_tarball(tarball_path)


@mock.patch("hermeto.core.package_managers.yarn_classic.resolver.get_config")
@mock.patch("hermeto.core.package_managers.yarn_classic.resolver.get_vcs_qualifiers")
def test_package_purl_permissive_mode_without_vcs_url(
    mock_get_vcs_qualifiers: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """Test that file/workspace/link packages omit vcs_url when not in a git repo."""
    mock_get_vcs_qualifiers.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.PERMISSIVE

    yarn_classic_packages: list[tuple[YarnClassicPackage, str]] = [
        (
            FilePackage(
                name="file-pkg",
                version="5.0.0",
                path=rooted_tmp_path.join_within_root("path/to/package"),
            ),
            "pkg:npm/file-pkg@5.0.0#path/to/package",
        ),
        (
            WorkspacePackage(
                name="workspace-pkg",
                version="6.0.0",
                path=rooted_tmp_path.join_within_root("workspace/package"),
            ),
            "pkg:npm/workspace-pkg@6.0.0#workspace/package",
        ),
        (
            LinkPackage(
                name="link-pkg",
                version="7.0.0",
                path=rooted_tmp_path.join_within_root("link/to/package"),
            ),
            "pkg:npm/link-pkg@7.0.0#link/to/package",
        ),
    ]

    for package, expected_purl in yarn_classic_packages:
        assert package.purl == expected_purl


@mock.patch("hermeto.core.package_managers.yarn_classic.resolver.get_config")
@mock.patch("hermeto.core.package_managers.yarn_classic.resolver.get_vcs_qualifiers")
def test_package_purl_strict_mode_raises_without_git_repo(
    mock_get_vcs_qualifiers: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """Test that file/workspace/link packages re-raise NotAGitRepo in STRICT mode."""
    mock_get_vcs_qualifiers.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.STRICT

    package = FilePackage(
        name="file-pkg",
        version="5.0.0",
        path=rooted_tmp_path.join_within_root("path/to/package"),
    )

    with pytest.raises(NotAGitRepo):
        _ = package.purl


@mock.patch("hermeto.core.package_managers.yarn_classic.resolver.get_config")
@mock.patch("hermeto.core.package_managers.yarn_classic.resolver.get_vcs_qualifiers")
def test_package_purl_permissive_mode_with_vcs_url(
    mock_get_vcs_qualifiers: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path_repo: RootedPath,
) -> None:
    """Test that file/workspace/link packages include vcs_url in PERMISSIVE mode when git repo is available."""
    repo = git.Repo(rooted_tmp_path_repo)
    repo.create_remote("origin", "https://github.com/org/repo.git")

    example_repo_id = get_repo_id(repo)
    example_vcs_url = example_repo_id.as_vcs_url_qualifier()
    purl_vcs_url = quote(example_vcs_url, safe=":/")

    mock_get_vcs_qualifiers.return_value = {"vcs_url": example_vcs_url}
    mock_get_config.return_value.mode = Mode.PERMISSIVE

    yarn_classic_packages: list[tuple[YarnClassicPackage, str]] = [
        (
            FilePackage(
                name="file-pkg",
                version="5.0.0",
                path=rooted_tmp_path_repo.join_within_root("path/to/package"),
            ),
            f"pkg:npm/file-pkg@5.0.0?vcs_url={purl_vcs_url}#path/to/package",
        ),
        (
            WorkspacePackage(
                name="workspace-pkg",
                version="6.0.0",
                path=rooted_tmp_path_repo.join_within_root("workspace/package"),
            ),
            f"pkg:npm/workspace-pkg@6.0.0?vcs_url={purl_vcs_url}#workspace/package",
        ),
        (
            LinkPackage(
                name="link-pkg",
                version="7.0.0",
                path=rooted_tmp_path_repo.join_within_root("link/to/package"),
            ),
            f"pkg:npm/link-pkg@7.0.0?vcs_url={purl_vcs_url}#link/to/package",
        ),
    ]

    for package, expected_purl in yarn_classic_packages:
        assert package.purl == expected_purl
