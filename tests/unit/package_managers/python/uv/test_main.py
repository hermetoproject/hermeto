# SPDX-License-Identifier: GPL-3.0-only
import logging
import subprocess
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.constants import Mode
from hermeto.core.errors import (
    LockfileNotFound,
    NotAGitRepo,
    PackageManagerError,
    PackageRejected,
)
from hermeto.core.models.output import ProjectFile
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.package_managers.python.uv.main import (
    _download_dependencies,
    _download_git_package,
    _generate_dependency_component,
    _generate_dependency_components,
    _generate_purl_git_package,
    _generate_purl_local_package,
    _generate_purl_main_package,
    _generate_purl_registry_package,
    _generate_purl_url_package,
    _get_project_vcs_qualifiers,
    _get_pyproject_metadata,
    _rewrite_lockfile,
    _validate_lockfile,
)
from hermeto.core.package_managers.python.uv.models import (
    ArtifactSdist,
    ArtifactWheel,
    PackageArtifact,
    PackageSource,
    PackageSourceGit,
    PackageSourceLocal,
    PackageSourceRegistry,
    PackageSourceUrl,
    UvLock,
    UvPackage,
    load_lockfile_document,
)
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF

SDIST = ArtifactSdist(url="https://example.org/example-1.0.0.tar.gz", hash="sha256:1234")
URL_SOURCE = "https://example.org/downloads/example-1.0.0.tar.gz"
GIT_TARBALL = "flask-gitcommit-7ef2946f5e6e1e573bb9796d47b09a3c0a94f973.tar.gz"
WHEEL_URL = "https://example.org/example-1.0.0-py3-none-any.whl"


def make_package(
    source: PackageSource,
    sdist: ArtifactSdist | None = None,
    wheels: list[ArtifactWheel] | None = None,
) -> UvPackage:
    return UvPackage(
        name="example", version="1.0.0", source=source, sdist=sdist, wheels=wheels or []
    )


def write_pyproject_toml(rooted_path: RootedPath, content: str) -> None:
    (rooted_path.path / "pyproject.toml").write_text(textwrap.dedent(content))


@mock.patch("hermeto.core.package_managers.python.uv.main.run_cmd")
def test_validate_lockfile_passes_when_uv_exits_0(
    mock_run_cmd: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    """--no-cache and --no-python-downloads are load-bearing, so pin the whole invocation."""
    (rooted_tmp_path.path / "uv.lock").touch()
    _validate_lockfile(rooted_tmp_path)
    mock_run_cmd.assert_called_once_with(
        ["uv", "lock", "--check", "--no-cache", "--no-python-downloads"],
        params={"cwd": rooted_tmp_path.path},
        suppress_errors=True,
    )


@mock.patch("hermeto.core.package_managers.python.uv.main.run_cmd")
def test_validate_lockfile_fails_before_running_uv_when_lockfile_is_missing(
    mock_run_cmd: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    """uv reports this as well, but names the temporary copy of the source instead."""
    with pytest.raises(LockfileNotFound, match="uv.lock"):
        _validate_lockfile(rooted_tmp_path)
    mock_run_cmd.assert_not_called()


UV_STDERR_NO_INTERPRETER = (
    "error: No interpreter found for Python >=3.99 in managed installations or search path\n"
)


@mock.patch("hermeto.core.package_managers.python.uv.main.run_cmd")
def test_validate_lockfile_rejects_when_uv_exits_1(
    mock_run_cmd: mock.Mock, rooted_tmp_path: RootedPath, caplog: pytest.LogCaptureFixture
) -> None:
    """uv exits 1 once the check ran, whether the lockfile is stale or cannot resolve at all."""
    (rooted_tmp_path.path / "uv.lock").touch()
    mock_run_cmd.side_effect = subprocess.CalledProcessError(1, ["uv"], stderr="error: nope\n")

    with pytest.raises(PackageRejected) as exc_info:
        _validate_lockfile(rooted_tmp_path)

    assert "`uv lock --check` rejected uv.lock" in exc_info.value.friendly_msg()
    # hermeto states no reason of its own, so uv's has to reach the user
    assert "error: nope" in caplog.text


@pytest.mark.parametrize(
    "stderr, expected_detail",
    [
        pytest.param(
            UV_STDERR_NO_INTERPRETER,
            "No interpreter found for Python >=3.99 in managed installations or search path",
            id="uv_headline_names_the_constraint_that_failed",
        ),
        pytest.param(
            "No interpreter found for Python >=3.99\n",
            "no Python interpreter satisfies requires-python",
            id="fallback_when_uv_drops_the_error_prefix",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.python.uv.main.run_cmd")
def test_validate_lockfile_skips_when_uv_finds_no_interpreter(
    mock_run_cmd: mock.Mock,
    stderr: str,
    expected_detail: str,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing the user can act on, and prefetching needs no interpreter of its own."""
    (rooted_tmp_path.path / "uv.lock").touch()
    mock_run_cmd.side_effect = subprocess.CalledProcessError(2, ["uv"], stderr=stderr)

    _validate_lockfile(rooted_tmp_path)

    assert f"Skipped validating uv.lock: {expected_detail}" in caplog.text
    # a tolerated state must not be dressed up as a failure
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@mock.patch("hermeto.core.package_managers.python.uv.main.run_cmd")
def test_validate_lockfile_fails_on_any_other_uv_error(
    mock_run_cmd: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    """uv never got as far as the lockfile, so it is unproven; do not prefetch blindly."""
    (rooted_tmp_path.path / "uv.lock").touch()
    mock_run_cmd.side_effect = subprocess.CalledProcessError(
        2, ["uv"], stderr="error: Failed to parse `uv.lock`\n"
    )

    with pytest.raises(PackageManagerError) as exc_info:
        _validate_lockfile(rooted_tmp_path)

    assert "`uv lock --check` failed for uv.lock with rc=2" in exc_info.value.friendly_msg()


@mock.patch("hermeto.core.package_managers.python.uv.main.clone_as_tarball")
def test_download_git_package(mock_clone: mock.Mock, rooted_tmp_path: RootedPath) -> None:
    source = PackageSourceGit(kind="git", location="https://github.com/org/repo?rev=main#0123abcd")
    _download_git_package(make_package(source), source, rooted_tmp_path)
    mock_clone.assert_called_once_with(
        "https://github.com/org/repo",
        "0123abcd",
        to_path=rooted_tmp_path.join_within_root("example-gitcommit-0123abcd.tar.gz").path,
    )


@mock.patch("hermeto.core.package_managers.python.uv.main.clone_as_tarball")
def test_download_git_package_skips_existing_tarball(
    mock_clone: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    """the tarball already exists, so it should be skipped"""
    (rooted_tmp_path.path / "example-gitcommit-0123abcd.tar.gz").touch()
    source = PackageSourceGit(kind="git", location="https://github.com/org/repo?rev=main#0123abcd")
    _download_git_package(make_package(source), source, rooted_tmp_path)
    mock_clone.assert_not_called()


@mock.patch("hermeto.core.package_managers.python.uv.main.clone_as_tarball")
@mock.patch("hermeto.core.package_managers.python.uv.main.must_match_any_checksum")
@mock.patch("hermeto.core.package_managers.python.uv.main.async_download_files")
def test_download_dependencies(
    mock_download: mock.Mock,
    mock_checksum: mock.Mock,
    mock_clone: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hashed_registry_package = UvPackage(
        name="registry-pkg",
        version="1.0.0",
        source=PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
        sdist=ArtifactSdist(
            url="https://example.org/registry-pkg-1.0.0.tar.gz", hash="sha256:1234"
        ),
    )
    unhashed_registry_package = UvPackage(
        name="unhashed-pkg",
        version="2.0.0",
        source=PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
        sdist=ArtifactSdist(url="https://example.org/unhashed-pkg-2.0.0.tar.gz"),
    )
    git_package = UvPackage(
        name="git-pkg",
        version="3.0.0",
        source=PackageSourceGit(kind="git", location="https://github.com/org/repo#0123abcd"),
    )
    local_package = UvPackage(
        name="local-pkg",
        version="4.0.0",
        source=PackageSourceLocal(kind="editable", location="."),
    )
    lock = UvLock(
        version=1,
        package=[
            hashed_registry_package,
            unhashed_registry_package,
            git_package,
            local_package,
        ],
    )

    _download_dependencies(rooted_tmp_path, lock)

    deps_dir = rooted_tmp_path.path / "deps" / "uv"
    mock_download.assert_awaited_once_with(
        {
            "https://example.org/registry-pkg-1.0.0.tar.gz": deps_dir / "registry-pkg-1.0.0.tar.gz",
            "https://example.org/unhashed-pkg-2.0.0.tar.gz": deps_dir / "unhashed-pkg-2.0.0.tar.gz",
        },
        mock.ANY,
    )
    mock_checksum.assert_called_once_with(
        deps_dir / "registry-pkg-1.0.0.tar.gz", [ChecksumInfo("sha256", "1234")]
    )
    mock_clone.assert_called_once()
    assert "Missing checksum for unhashed-pkg==2.0.0" in caplog.text


@mock.patch("hermeto.core.package_managers.python.uv.main.must_match_any_checksum")
@mock.patch("hermeto.core.package_managers.python.uv.main.async_download_files")
def test_download_dependencies_skips_existing_files(
    mock_download: mock.Mock,
    mock_checksum: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    deps_dir = rooted_tmp_path.path / "deps" / "uv"
    deps_dir.mkdir(parents=True)
    (deps_dir / "registry-pkg-1.0.0.tar.gz").touch()

    package = UvPackage(
        name="registry-pkg",
        version="1.0.0",
        source=PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
        sdist=ArtifactSdist(
            url="https://example.org/registry-pkg-1.0.0.tar.gz", hash="sha256:1234"
        ),
    )
    lock = UvLock(version=1, package=[package])

    _download_dependencies(rooted_tmp_path, lock)

    mock_download.assert_not_called()
    mock_checksum.assert_called_once_with(
        deps_dir / "registry-pkg-1.0.0.tar.gz", [ChecksumInfo("sha256", "1234")]
    )


@pytest.mark.parametrize(
    "pyproject, expected_metadata, expected_log",
    [
        pytest.param(
            """
            [project]
            name = "example"
            version = "1.0.0"
            """,
            ("example", "1.0.0"),
            "",
            id="name_and_version_both_declared",
        ),
        pytest.param(
            """
            [project]
            name = "example"
            """,
            ("example", None),
            "Could not resolve version",
            id="undeclared_version_is_reported_as_none",
        ),
    ],
)
def test_get_pyproject_metadata(
    pyproject: str,
    expected_metadata: tuple[str, str | None],
    expected_log: str,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_pyproject_toml(rooted_tmp_path, pyproject)

    assert _get_pyproject_metadata(rooted_tmp_path) == expected_metadata
    assert expected_log in caplog.text


def test_get_pyproject_metadata_missing_name(rooted_tmp_path: RootedPath) -> None:
    write_pyproject_toml(rooted_tmp_path, "[project]\n")
    with pytest.raises(PackageRejected, match="does not declare a project name"):
        _get_pyproject_metadata(rooted_tmp_path)


@mock.patch("hermeto.core.scm.GitRepo")
def test_get_project_vcs_qualifiers(mock_git_repo: mock.Mock, rooted_tmp_path: RootedPath) -> None:
    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = "ssh://git@github.com/my-org/my-repo"
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    qualifiers = _get_project_vcs_qualifiers(rooted_tmp_path)

    assert qualifiers == {"vcs_url": f"git+ssh://git@github.com/my-org/my-repo@{GIT_REF}"}


@mock.patch("hermeto.core.package_managers.python.uv.main.get_config")
def test_get_project_vcs_qualifiers_permissive_mode_without_git_repo(
    mock_get_config: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    """rooted_tmp_path is not a git repo, so get_vcs_qualifiers genuinely raises NotAGitRepo."""
    mock_get_config.return_value.mode = Mode.PERMISSIVE

    assert _get_project_vcs_qualifiers(rooted_tmp_path) is None


@mock.patch("hermeto.core.package_managers.python.uv.main.get_config")
def test_get_project_vcs_qualifiers_strict_mode_raises_without_git_repo(
    mock_get_config: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    mock_get_config.return_value.mode = Mode.STRICT

    with pytest.raises(NotAGitRepo):
        _get_project_vcs_qualifiers(rooted_tmp_path)


@pytest.mark.parametrize(
    "subpath, vcs_qualifiers, expected_purl",
    [
        pytest.param(".", None, "pkg:pypi/foo@1.0.0", id="no_qualifiers_no_subpath"),
        pytest.param(
            "path/to/package",
            None,
            "pkg:pypi/foo@1.0.0#path/to/package",
            id="no_qualifiers_with_subpath",
        ),
        pytest.param(
            ".",
            {"vcs_url": f"git+ssh://git@github.com/my-org/my-repo@{GIT_REF}"},
            f"pkg:pypi/foo@1.0.0?vcs_url=git%2Bssh://git%40github.com/my-org/my-repo%40{GIT_REF}",
            id="qualifiers_no_subpath",
        ),
        pytest.param(
            "path/to/package",
            {"vcs_url": f"git+ssh://git@github.com/my-org/my-repo@{GIT_REF}"},
            f"pkg:pypi/foo@1.0.0?vcs_url=git%2Bssh://git%40github.com/my-org/my-repo%40{GIT_REF}"
            "#path/to/package",
            id="qualifiers_with_subpath",
        ),
    ],
)
def test_generate_purl_main_package(
    subpath: Path,
    vcs_qualifiers: dict[str, str] | None,
    expected_purl: str,
    rooted_tmp_path: RootedPath,
) -> None:
    purl = _generate_purl_main_package(
        "foo", "1.0.0", rooted_tmp_path.join_within_root(subpath), vcs_qualifiers
    )

    assert purl == expected_purl


@pytest.mark.parametrize(
    "index_url, expected_purl",
    [
        pytest.param(
            "https://pypi.org/simple",
            "pkg:pypi/example@1.0.0",
            id="default_pypi_index_needs_no_repository_url",
        ),
        pytest.param(
            "https://example.com/simple",
            "pkg:pypi/example@1.0.0?repository_url=https://example.com/simple",
            id="custom_index_is_recorded_as_repository_url",
        ),
    ],
)
def test_generate_purl_registry_package(index_url: str, expected_purl: str) -> None:
    package = make_package(PackageSourceRegistry(kind="registry", location=index_url))
    assert _generate_purl_registry_package(package) == expected_purl


def test_generate_purl_git_package() -> None:
    source = PackageSourceGit(
        kind="git", location=f"https://github.com/org/repo?rev=main#{GIT_REF}"
    )
    assert _generate_purl_git_package(make_package(source), source) == (
        f"pkg:pypi/example@1.0.0?vcs_url=git%2Bhttps://github.com/org/repo%40{GIT_REF}"
    )


def test_generate_purl_url_package() -> None:
    package = make_package(
        PackageSourceUrl(kind="url", location="https://example.org/example-1.0.0.tar.gz")
    )
    artifact = PackageArtifact(hash="sha256:1234")
    assert _generate_purl_url_package(package, artifact) == (
        "pkg:pypi/example@1.0.0?checksum=sha256:1234"
        "&download_url=https://example.org/example-1.0.0.tar.gz"
    )


def test_generate_purl_url_package_missing_hash_should_not_happen() -> None:
    """artifacts_to_download always rejects a hashless url artifact before this is reached."""
    package = make_package(
        PackageSourceUrl(kind="url", location="https://example.org/example-1.0.0.tar.gz")
    )
    with pytest.raises(RuntimeError, match="has no hash"):
        _generate_purl_url_package(
            package, PackageArtifact(url="https://example.org/example-1.0.0.tar.gz")
        )


@pytest.mark.parametrize(
    "package, vcs_qualifiers, expected_purl",
    [
        pytest.param(
            make_package(PackageSourceLocal(kind="directory", location="libs/vendored-lib")),
            {"vcs_url": f"git+https://github.com/acme/monorepo@{GIT_REF}"},
            f"pkg:pypi/example@1.0.0?vcs_url=git%2Bhttps://github.com/acme/monorepo%40{GIT_REF}"
            "#libs/vendored-lib",
            id="repo_vcs_url_plus_subpath_to_the_dependency",
        ),
        pytest.param(
            make_package(PackageSourceLocal(kind="directory", location="libs/vendored-lib")),
            None,
            "pkg:pypi/example@1.0.0#libs/vendored-lib",
            id="permissive_mode_without_a_git_repo_omits_vcs_url",
        ),
        pytest.param(
            make_package(PackageSourceLocal(kind="editable", location=".")),
            None,
            "pkg:pypi/example@1.0.0",
            id="dependency_at_the_project_root_gets_no_subpath",
        ),
        pytest.param(
            UvPackage(
                name="ws-root",
                source=PackageSourceLocal(kind="editable", location="packages/member"),
            ),
            None,
            "pkg:pypi/ws-root#packages/member",
            id="dynamic_version_locks_without_one_so_purl_omits_it",
        ),
    ],
)
def test_generate_purl_local_package(
    package: UvPackage,
    vcs_qualifiers: dict[str, str] | None,
    expected_purl: str,
    rooted_tmp_path: RootedPath,
) -> None:
    assert _generate_purl_local_package(package, rooted_tmp_path, vcs_qualifiers) == expected_purl


def test_generate_purl_local_package_escapes_repo_root(rooted_tmp_path: RootedPath) -> None:
    package = make_package(PackageSourceLocal(kind="directory", location="../outside-the-repo"))

    with pytest.raises(PackageRejected, match="escapes the repository root"):
        _generate_purl_local_package(package, rooted_tmp_path, None)


@pytest.mark.parametrize(
    "package, expected_purl",
    [
        pytest.param(
            make_package(
                PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
                sdist=SDIST,
            ),
            "pkg:pypi/example@1.0.0",
            id="registry_source_uses_the_registry_purl",
        ),
        pytest.param(
            make_package(
                PackageSourceGit(
                    kind="git", location=f"https://github.com/org/repo?rev=main#{GIT_REF}"
                )
            ),
            f"pkg:pypi/example@1.0.0?vcs_url=git%2Bhttps://github.com/org/repo%40{GIT_REF}",
            id="git_source_uses_the_vcs_url_purl",
        ),
        pytest.param(
            make_package(PackageSourceLocal(kind="directory", location="libs/vendored-lib")),
            "pkg:pypi/example@1.0.0#libs/vendored-lib",
            id="local_source_uses_the_subpath_purl",
        ),
    ],
)
def test_generate_dependency_component_purl(
    package: UvPackage, expected_purl: str, rooted_tmp_path: RootedPath
) -> None:
    """Every source kind has to reach its own _generate_purl_* helper."""
    component = _generate_dependency_component(
        package, package.artifacts_to_download, rooted_tmp_path, None, "uv.lock"
    )

    assert component.purl == expected_purl


@pytest.mark.parametrize(
    "package, expected",
    [
        pytest.param(
            make_package(
                PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
                sdist=SDIST,
            ),
            frozenset(),
            id="registry_sdist_whose_index_published_a_hash",
        ),
        pytest.param(
            make_package(
                PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
                sdist=ArtifactSdist(url="https://example.org/example-1.0.0.tar.gz"),
            ),
            frozenset({"uv.lock"}),
            id="registry_sdist_whose_index_published_no_hash",
        ),
        pytest.param(
            make_package(PackageSourceUrl(kind="url", location=URL_SOURCE), sdist=SDIST),
            frozenset(),
            id="url_source_always_carries_a_hash",
        ),
        pytest.param(
            make_package(
                PackageSourceGit(
                    kind="git", location=f"https://github.com/org/repo?rev=main#{GIT_REF}"
                )
            ),
            frozenset({"uv.lock"}),
            id="git_source_records_no_hash_like_pips_vcs_deps",
        ),
    ],
)
def test_generate_dependency_component_missing_hash_property(
    package: UvPackage, expected: frozenset[str], rooted_tmp_path: RootedPath
) -> None:
    component = _generate_dependency_component(
        package, package.artifacts_to_download, rooted_tmp_path, None, "uv.lock"
    )

    props = PropertySet.from_properties(component.properties)
    assert props.missing_hash_in_file == expected


@pytest.mark.parametrize("kind", ["path", "directory", "editable", "virtual"])
def test_generate_dependency_component_local_has_no_missing_hash(
    kind: str, rooted_tmp_path: RootedPath
) -> None:
    """Nothing is fetched for an in-tree source, so there is no checksum to miss."""
    package = make_package(PackageSourceLocal(kind=kind, location="libs/vendored-lib"))

    component = _generate_dependency_component(
        package, package.artifacts_to_download, rooted_tmp_path, None, "uv.lock"
    )

    props = PropertySet.from_properties(component.properties)
    assert props.missing_hash_in_file == frozenset()


@pytest.mark.parametrize(
    "package, expected",
    [
        pytest.param(
            make_package(
                PackageSourceUrl(kind="url", location=WHEEL_URL),
                wheels=[ArtifactWheel(url=WHEEL_URL, hash="sha256:5678")],
            ),
            True,
            id="url_wheel",
        ),
        pytest.param(
            make_package(
                PackageSourceUrl(kind="url", location="https://example.org/example-1.0.0.tar.gz"),
                sdist=ArtifactSdist(hash="sha256:1234"),
            ),
            False,
            id="url_sdist",
        ),
        pytest.param(
            make_package(
                PackageSourceLocal(kind="path", location="dist/example-1.0.0-py3-none-any.whl"),
                wheels=[
                    ArtifactWheel(filename="example-1.0.0-py3-none-any.whl", hash="sha256:5678")
                ],
            ),
            True,
            id="path_wheel",
        ),
        pytest.param(
            make_package(
                PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"),
                sdist=SDIST,
            ),
            False,
            id="registry_sdist",
        ),
        pytest.param(
            make_package(
                PackageSourceGit(
                    kind="git", location=f"https://github.com/org/repo?rev=main#{GIT_REF}"
                )
            ),
            False,
            id="git",
        ),
    ],
)
def test_generate_dependency_component_binary_property(
    package: UvPackage, expected: bool, rooted_tmp_path: RootedPath
) -> None:
    """A dependency pinned to a wheel is marked binary, like pip marks its own."""
    component = _generate_dependency_component(
        package, package.artifacts_to_download, rooted_tmp_path, None, "uv.lock"
    )

    assert PropertySet.from_properties(component.properties).uv_package_binary is expected


def test_generate_dependency_components_excludes_root_entry(
    rooted_tmp_path: RootedPath,
) -> None:
    root_entry = UvPackage(
        name="root-app", version="0.1.0", source=PackageSourceLocal(kind="editable", location=".")
    )
    dep = make_package(
        PackageSourceRegistry(kind="registry", location="https://pypi.org/simple"), sdist=SDIST
    )
    lock = UvLock(version=1, packages=[root_entry, dep])

    components = _generate_dependency_components(lock, rooted_tmp_path, None)

    assert len(components) == 1
    assert components[0].purl == "pkg:pypi/example@1.0.0"


REWRITE_LOCKFILE = """\
version = 1
revision = 2
requires-python = ">=3.12"

# retained-comment
[[package]]
name = "myproject"
version = "0.1.0"
source = { virtual = "." }
dependencies = [{ name = "anyio" }]

[[package]]
name = "anyio"
version = "4.13.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/aa/anyio-4.13.0.tar.gz", hash = "sha256:aaaa", size = 231622, upload-time = "2026-03-24T12:59:09.671Z" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/bb/anyio-4.13.0-py3-none-any.whl", hash = "sha256:bbbb", size = 114353 },
]

[[package]]
name = "flask"
version = "3.2.0.dev0"
source = { git = "https://github.com/pallets/flask?rev=main#7ef2946f5e6e1e573bb9796d47b09a3c0a94f973" }

[[package]]
name = "httpx"
version = "0.28.1"
source = { url = "https://example.com/dist/httpx-0.28.1.tar.gz" }
sdist = { hash = "sha256:cccc" }
"""


def rewrite_lockfile(
    rooted_tmp_path: RootedPath, lockfile: str = REWRITE_LOCKFILE
) -> ProjectFile | None:
    rooted_tmp_path.join_within_root("uv.lock").path.write_text(lockfile)
    doc = load_lockfile_document(rooted_tmp_path)
    lock = UvLock.from_toml(doc, rooted_tmp_path.join_within_root("uv.lock").path)
    return _rewrite_lockfile(doc, rooted_tmp_path, lock)


@pytest.mark.parametrize(
    "expected",
    [
        pytest.param(
            'url = "file://${output_dir}/deps/uv/anyio-4.13.0.tar.gz"',
            id="registry_sdist_url_points_at_the_deps_dir",
        ),
        pytest.param(
            'url = "file://${output_dir}/deps/uv/anyio-4.13.0-py3-none-any.whl"',
            id="registry_wheel_url_points_at_the_deps_dir",
        ),
        pytest.param('hash = "sha256:aaaa"', id="registry_hash_survives_the_rewrite"),
        pytest.param("size = 231622", id="registry_size_survives_the_rewrite"),
        pytest.param(
            'upload-time = "2026-03-24T12:59:09.671Z"',
            id="registry_upload_time_survives_the_rewrite",
        ),
        pytest.param(
            f'path = "${{output_dir}}/deps/uv/{GIT_TARBALL}"',
            id="git_source_replaced_by_the_cloned_tarball",
        ),
        pytest.param(
            'source = { path = "${output_dir}/deps/uv/httpx-0.28.1.tar.gz" }',
            id="url_source_replaced_keeping_uvs_brace_padding",
        ),
        pytest.param(
            'sdist = { hash = "sha256:cccc" }', id="hash_only_distribution_entry_is_untouched"
        ),
        pytest.param("# retained-comment", id="unmodelled_comment_survives"),
        pytest.param("revision = 2", id="unmodelled_revision_survives"),
        pytest.param('requires-python = ">=3.12"', id="unmodelled_requires_python_survives"),
        pytest.param('dependencies = [{ name = "anyio" }]', id="unmodelled_dep_edges_survive"),
        pytest.param('source = { virtual = "." }', id="local_source_is_left_alone"),
    ],
)
def test_rewrite_lockfile_template_contains(expected: str, rooted_tmp_path: RootedPath) -> None:
    """Each param is one line the rewrite must produce or leave alone.

    The redirects prove the edits landed; the rest prove they stayed surgical,
    since the raw tomlkit document carries what the lossy UvLock model drops.
    """
    project_file = rewrite_lockfile(rooted_tmp_path)
    assert project_file is not None
    assert expected in project_file.template


@pytest.mark.parametrize(
    "forbidden",
    [
        pytest.param("github.com/pallets/flask", id="git_remote_no_longer_reachable"),
        pytest.param("https://example.com", id="url_source_host_no_longer_reachable"),
    ],
)
def test_rewrite_lockfile_template_drops(forbidden: str, rooted_tmp_path: RootedPath) -> None:
    """Every remote reference has to be gone, or the offline install would still hit the network."""
    project_file = rewrite_lockfile(rooted_tmp_path)
    assert project_file is not None
    assert forbidden not in project_file.template


def test_rewrite_lockfile_abspath(rooted_tmp_path: RootedPath) -> None:
    project_file = rewrite_lockfile(rooted_tmp_path)
    assert project_file is not None
    assert project_file.abspath == rooted_tmp_path.path / "uv.lock"


def test_rewrite_lockfile_all_local_returns_none(rooted_tmp_path: RootedPath) -> None:
    all_local = textwrap.dedent(
        """\
        version = 1

        [[package]]
        name = "myproject"
        version = "0.1.0"
        source = { editable = "." }
        """
    )
    assert rewrite_lockfile(rooted_tmp_path, all_local) is None


def test_rewrite_lockfile_url_source_without_distribution_should_not_happen(
    rooted_tmp_path: RootedPath,
) -> None:
    """artifacts_to_download rejects such a package during the download phase."""
    no_distribution = textwrap.dedent(
        """\
        version = 1

        [[package]]
        name = "httpx"
        version = "0.28.1"
        source = { url = "https://example.com/httpx-0.28.1.tar.gz" }
        """
    )
    with pytest.raises(RuntimeError, match="records no sdist and no wheels"):
        rewrite_lockfile(rooted_tmp_path, no_distribution)


def test_rewrite_lockfile_resolves_output_dir(rooted_tmp_path: RootedPath) -> None:
    project_file = rewrite_lockfile(rooted_tmp_path)
    assert project_file is not None
    resolved = project_file.resolve_content(Path("/hermeto-output"))
    assert "file:///hermeto-output/deps/uv/anyio-4.13.0.tar.gz" in resolved
    assert 'path = "/hermeto-output/deps/uv/httpx-0.28.1.tar.gz"' in resolved
