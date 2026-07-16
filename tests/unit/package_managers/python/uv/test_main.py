# SPDX-License-Identifier: GPL-3.0-only
import logging
import subprocess
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import (
    LockfileNotFound,
    PackageManagerError,
    PackageRejected,
)
from hermeto.core.models.output import ProjectFile
from hermeto.core.package_managers.python.uv.main import (
    _download_dependencies,
    _download_git_package,
    _get_pyproject_metadata,
    _rewrite_lockfile,
    _validate_lockfile,
)
from hermeto.core.package_managers.python.uv.models import (
    ArtifactSdist,
    ArtifactWheel,
    PackageSource,
    PackageSourceGit,
    PackageSourceLocal,
    PackageSourceRegistry,
    UvLock,
    UvPackage,
    load_lockfile_document,
)
from hermeto.core.rooted_path import RootedPath

SDIST = ArtifactSdist(url="https://example.org/example-1.0.0.tar.gz", hash="sha256:1234")
URL_SOURCE = "https://example.org/downloads/example-1.0.0.tar.gz"
GIT_TARBALL = "flask-gitcommit-7ef2946f5e6e1e573bb9796d47b09a3c0a94f973.tar.gz"


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
    _download_dependencies(
        rooted_tmp_path,
        [hashed_registry_package, unhashed_registry_package, git_package, local_package],
    )

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
    _download_dependencies(rooted_tmp_path, [package])

    mock_download.assert_not_called()
    mock_checksum.assert_called_once_with(
        deps_dir / "registry-pkg-1.0.0.tar.gz", [ChecksumInfo("sha256", "1234")]
    )


@mock.patch(
    "hermeto.core.package_managers.python.uv.models.UvPackage.artifacts_to_download",
    new_callable=mock.PropertyMock,
)
def test_download_dependencies_artifact_without_url_should_not_happen(
    mock_artifacts: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    """artifacts_to_download always records a URL, so only a faked one reaches the guard."""
    source = PackageSourceRegistry(kind="registry", location="https://pypi.org/simple")
    mock_artifacts.return_value = [ArtifactSdist(hash="sha256:1234")]

    with pytest.raises(RuntimeError, match="has no download URL"):
        _download_dependencies(rooted_tmp_path, [make_package(source, sdist=SDIST)])


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
            None,
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
    expected_log: str | None,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_pyproject_toml(rooted_tmp_path, pyproject)

    assert _get_pyproject_metadata(rooted_tmp_path) == expected_metadata
    if expected_log is None:
        assert "Could not resolve version" not in caplog.text
    else:
        assert expected_log in caplog.text


def test_get_pyproject_metadata_missing_name(rooted_tmp_path: RootedPath) -> None:
    write_pyproject_toml(rooted_tmp_path, "[project]\n")
    with pytest.raises(PackageRejected, match="does not declare a project name"):
        _get_pyproject_metadata(rooted_tmp_path)


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
