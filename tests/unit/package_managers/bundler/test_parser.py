# SPDX-License-Identifier: GPL-3.0-only
import re
import subprocess
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest import mock

import pydantic
import pytest
from git.repo import Repo

from hermeto.core.errors import LockfileNotFound, PackageManagerError, UnexpectedFormat
from hermeto.core.models.input import BundlerBinaryFilters
from hermeto.core.package_managers.bundler.gem_models import (
    GemDependency,
    GemPlatformSpecificDependency,
    GitDependency,
    PathDependency,
)
from hermeto.core.package_managers.bundler.parser import (
    BundlerDependency,
    _run_lockfile_parser,
    parse_lockfile,
)
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF

RegexpStr = str  # a string representing a regular expression.


def some_message_contains_substring(substring: RegexpStr, messages: Iterable[str]) -> bool:
    """Check if substring-matching regexp could be found in any message.

    This produces a bit less coupling between tests and code than
    checking for a full message.
    """
    r = re.compile(substring)
    return any(r.match(m) is not None for m in messages)


SAMPLE_PARSER_OUTPUT = {
    "bundler_version": "2.5.10",
    "dependencies": [{"name": "example", "version": "0.1.0"}],
}


@pytest.fixture
def sample_parser_output() -> dict[str, Any]:
    return deepcopy(SAMPLE_PARSER_OUTPUT)


def test_parse_lockfile_without_bundler_files(rooted_tmp_path: RootedPath) -> None:
    with pytest.raises(LockfileNotFound):
        parse_lockfile(rooted_tmp_path)


@mock.patch("subprocess.run")
def test_run_lockfile_parser_hides_bundle_directory(
    mock_subprocess: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PATH", "/opt/homebrew/bin")
    monkeypatch.setenv("BUNDLE_APP_CONFIG", ".blunder")

    bundle_dir = tmp_path / ".bundle"
    bundle_dir.mkdir()
    bundle_config = bundle_dir / "config"
    bundle_config.write_text("BUNDLE_FOO: bar\n")

    def _assert_bundle_is_hidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert not bundle_dir.exists()
        assert kwargs["env"] == {"PATH": "/opt/homebrew/bin"}
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr=None)

    mock_subprocess.side_effect = _assert_bundle_is_hidden
    _run_lockfile_parser(tmp_path)

    assert bundle_dir.is_dir()
    assert bundle_config.read_text() == "BUNDLE_FOO: bar\n"


@mock.patch("hermeto.core.package_managers.bundler.parser.run_cmd")
def test_run_lockfile_parser_raises_exception_on_os_error(
    mock_run_cmd: mock.MagicMock,
    tmp_path: Path,
) -> None:
    mock_run_cmd.side_effect = subprocess.CalledProcessError(returncode=1, cmd="cmd")
    with pytest.raises(PackageManagerError) as exc_info:
        _run_lockfile_parser(tmp_path)

    assert "Failed to parse Gemfile.lock" in exc_info.value.friendly_msg()


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser._run_lockfile_parser")
@pytest.mark.parametrize(
    "error, expected_error_msg",
    [
        ("LOCKFILE_INVALID_URL", "Input should be a valid URL"),
        ("LOCKFILE_INVALID_URL_SCHEME", "URL scheme should be 'https'"),
        ("LOCKFILE_INVALID_REVISION", "String should match pattern '^[a-fA-F0-9]{40}$'"),
        ("LOCKFILE_INVALID_PATH", "PATH dependencies should be within the package root"),
    ],
)
def test_parse_lockfile_invalid_format(
    mock_run_lockfile_parser: mock.MagicMock,
    mock_ensure_bundler_files_exist: mock.MagicMock,
    error: str,
    expected_error_msg: str,
    sample_parser_output: dict[str, Any],
    rooted_tmp_path: RootedPath,
) -> None:
    if error == "LOCKFILE_INVALID_URL":
        sample_parser_output["dependencies"][0].update(
            {
                "type": "git",
                "url": "github",
                "ref": GIT_REF,
            }
        )
    elif error == "LOCKFILE_INVALID_URL_SCHEME":
        sample_parser_output["dependencies"][0].update(
            {
                "type": "git",
                "url": "http://github.com/3scale/json-schema.git",
                "ref": GIT_REF,
            }
        )
    elif error == "LOCKFILE_INVALID_REVISION":
        sample_parser_output["dependencies"][0].update(
            {
                "type": "git",
                "url": "https://github.com/3scale/json-schema.git",
                "ref": "abcd",
            }
        )
    elif error == "LOCKFILE_INVALID_PATH":
        sample_parser_output["dependencies"][0].update(
            {
                "type": "path",
                "subpath": "/root/pathgem",
            }
        )

    mock_run_lockfile_parser.return_value = sample_parser_output
    with pytest.raises((pydantic.ValidationError, UnexpectedFormat)) as exc_info:
        parse_lockfile(rooted_tmp_path)

    assert expected_error_msg in str(exc_info.value)


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser._run_lockfile_parser")
def test_parse_gemlock(
    mock_run_lockfile_parser: mock.MagicMock,
    mock_ensure_bundler_files_exist: mock.MagicMock,
    sample_parser_output: dict[str, Any],
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base_dep: dict[str, str] = sample_parser_output["dependencies"][0]
    mocked_checksum: str = "sha256:bd2d213996ff7b3b364cd342a585fbee9797dbc1c0c6d868dc4150cc75739781"
    sample_parser_output["dependencies"] = [
        {
            "type": "git",
            "url": "https://github.com/3scale/json-schema.git",
            "ref": GIT_REF,
            **base_dep,
        },
        {
            "type": "path",
            "subpath": "vendor/pathgem",
            **base_dep,
        },
        {
            "type": "rubygems",
            "source": "https://rubygems.org/",
            "platforms": ["ruby"],
            "checksums": {"ruby": mocked_checksum},
            **base_dep,
        },
    ]

    mock_run_lockfile_parser.return_value = sample_parser_output
    result = parse_lockfile(rooted_tmp_path)

    expected_deps = [
        GitDependency(
            name="example",
            version="0.1.0",
            url="https://github.com/3scale/json-schema.git",
            ref=GIT_REF,
        ),
        PathDependency(
            name="example",
            version="0.1.0",
            root=str(rooted_tmp_path),
            subpath="vendor/pathgem",
        ),
        GemDependency(
            name="example",
            version="0.1.0",
            source="https://rubygems.org/",
            checksum=mocked_checksum,
        ),
    ]

    assert f"Package {rooted_tmp_path.path.name} is bundled with version 2.5.10" in caplog.messages
    assert result == expected_deps


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser._run_lockfile_parser")
def test_parse_gemlock_empty(
    mock_run_lockfile_parser: mock.MagicMock,
    mock_ensure_bundler_files_exist: mock.MagicMock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_run_lockfile_parser.return_value = {"bundler_version": "2.5.10", "dependencies": []}
    result = parse_lockfile(rooted_tmp_path)

    assert f"Package {rooted_tmp_path.path.name} is bundled with version 2.5.10" in caplog.messages
    assert result == []


@mock.patch("hermeto.core.package_managers.bundler.gem_models.GitRepo.clone_from")
def test_download_git_dependency_works(
    mock_git_clone: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dep = GitDependency(
        name="example",
        version="0.1.0",
        url="https://github.com/user/repo.git",
        ref=GIT_REF,
    )
    dep_path = rooted_tmp_path.join_within_root(f"{dep.repo_name}-{dep.ref[:12]}").path

    dep.download_to(deps_dir=rooted_tmp_path)
    assert f"Cloning git repository {dep.url}" in caplog.messages

    mock_git_clone.assert_called_once_with(
        url=str(dep.url),
        to_path=dep_path,
        bare=True,
        env={"GIT_TERMINAL_PROMPT": "0"},
    )
    assert dep_path.exists()


@mock.patch("hermeto.core.package_managers.bundler.gem_models.GitRepo.clone_from")
def test_download_duplicate_git_dependency_is_skipped(
    mock_git_clone: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dep = GitDependency(
        name="example",
        version="0.1.0",
        url="https://github.com/user/repo.git",
        ref=GIT_REF,
    )
    dep_path = rooted_tmp_path.join_within_root(f"{dep.repo_name}-{dep.ref[:12]}").path

    dep.download_to(deps_dir=rooted_tmp_path)
    dep.download_to(deps_dir=rooted_tmp_path)
    assert f"Skipping existing git repository {dep.url}" in caplog.messages

    mock_git_clone.assert_called_once_with(
        url=str(dep.url),
        to_path=dep_path,
        bare=True,
        env={"GIT_TERMINAL_PROMPT": "0"},
    )
    assert dep_path.exists()


def test_purls(rooted_tmp_path_repo: RootedPath) -> None:
    repo = Repo(rooted_tmp_path_repo)
    repo.create_remote("origin", "git@github.com:user/repo.git")
    repo_commit = repo.head.commit

    deps: list[tuple[BundlerDependency, str]] = [
        (
            GemDependency(
                name="my-gem-dep",
                version="0.1.0",
                source="https://rubygems.org",
            ),
            "pkg:gem/my-gem-dep@0.1.0",
        ),
        (
            GitDependency(
                name="my-git-dep",
                version="0.1.0",
                url="https://github.com/rubygems/example.git",
                ref=GIT_REF,
            ),
            f"pkg:gem/my-git-dep@0.1.0?vcs_url=git%2Bhttps://github.com/rubygems/example.git%40{GIT_REF}",
        ),
        (
            PathDependency(
                name="my-path-dep",
                version="0.1.0",
                root=rooted_tmp_path_repo,
                subpath="vendor",
            ),
            f"pkg:gem/my-path-dep@0.1.0?vcs_url=git%2Bssh://git%40github.com/user/repo.git%40{repo_commit.hexsha}#vendor",
        ),
    ]

    for dep, expected_purl in deps:
        assert dep.purl == expected_purl


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser._run_lockfile_parser")
def test_parse_gemlock_detects_binaries_and_adds_to_parse_result_when_allowed_to(
    mock_run_lockfile_parser: mock.MagicMock,
    mock_ensure_bundler_files_exist: mock.MagicMock,
    sample_parser_output: dict[str, Any],
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base_dep: dict[str, str] = sample_parser_output["dependencies"][0]
    mocked_checksum: str = "sha256:bd2d213996ff7b3b364cd342a585fbee9797dbc1c0c6d868dc4150cc75739781"
    sample_parser_output["dependencies"] = [
        {
            "type": "rubygems",
            "source": "https://rubygems.org/",
            "platforms": ["i8080_cpm"],
            "checksums": {"i8080_cpm": mocked_checksum},
            **base_dep,
        },
    ]

    mock_run_lockfile_parser.return_value = sample_parser_output
    result = parse_lockfile(
        rooted_tmp_path, binary_filters=BundlerBinaryFilters.with_allow_binary_behavior()
    )

    expected_deps = [
        GemPlatformSpecificDependency(
            name="example",
            version="0.1.0",
            source="https://rubygems.org/",
            platform="i8080_cpm",
            checksum=mocked_checksum,
        ),
    ]

    assert some_message_contains_substring("Found a binary dependency", caplog.messages)
    assert some_message_contains_substring("Will download binary dependency", caplog.messages)
    assert result == expected_deps


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser._run_lockfile_parser")
def test_parse_gemlock_detects_binaries_and_skips_then_when_instructed_to_skip(
    mock_run_lockfile_parser: mock.MagicMock,
    mock_ensure_bundler_files_exist: mock.MagicMock,
    sample_parser_output: dict[str, Any],
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base_dep: dict[str, str] = sample_parser_output["dependencies"][0]
    sample_parser_output["dependencies"] = [
        {
            "type": "rubygems",
            "source": "https://rubygems.org/",
            "platforms": ["i8080_cpm"],
            **base_dep,
        },
    ]

    mock_run_lockfile_parser.return_value = sample_parser_output
    result = parse_lockfile(rooted_tmp_path)

    expected_deps: list = []  # mypy demanded this annotation and is content with it.

    assert some_message_contains_substring("Found a binary dependency", caplog.messages)
    assert some_message_contains_substring("Skipping binary dependency", caplog.messages)

    assert result == expected_deps
