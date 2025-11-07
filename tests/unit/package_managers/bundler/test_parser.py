import json
import re
import subprocess
from collections.abc import Iterable
from copy import deepcopy
from typing import Any
from unittest import mock

import pydantic
import pytest
from git.repo import Repo

from hermeto.core.errors import PackageManagerError, PackageRejected, UnexpectedFormat
from hermeto.core.models.input import BundlerBinaryFilters
from hermeto.core.package_managers.bundler.gem_models import (
    GemDependency,
    GemPlatformSpecificDependency,
    GitDependency,
    PathDependency,
)
from hermeto.core.package_managers.bundler.parser import BundlerDependency, parse_lockfile
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
    with pytest.raises(PackageRejected) as exc_info:
        parse_lockfile(rooted_tmp_path)

    assert (
        "Gemfile and Gemfile.lock must be present in the package directory"
        in exc_info.value.friendly_msg()
    )


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser.run_cmd")
def test_parse_lockfile_os_error(
    mock_run_cmd: mock.MagicMock,
    mock_ensure_bundler_files_exist: mock.MagicMock,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_run_cmd.side_effect = subprocess.CalledProcessError(returncode=1, cmd="cmd")

    with pytest.raises(PackageManagerError) as exc_info:
        parse_lockfile(rooted_tmp_path)

    assert "Failed to parse Gemfile.lock" in exc_info.value.friendly_msg()


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser.run_cmd")
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
    mock_run_cmd: mock.MagicMock,
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

    mock_run_cmd.return_value = json.dumps(sample_parser_output)
    with pytest.raises((pydantic.ValidationError, UnexpectedFormat)) as exc_info:
        parse_lockfile(rooted_tmp_path)

    assert expected_error_msg in str(exc_info.value)


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser.run_cmd")
def test_parse_gemlock(
    mock_run_cmd: mock.MagicMock,
    mock_ensure_bundler_files_exist: mock.MagicMock,
    sample_parser_output: dict[str, Any],
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base_dep: dict[str, str] = sample_parser_output["dependencies"][0]
    sample_parser_output["dependencies"] = [
        {
            "type": "git",
            "url": "https://github.com/3scale/json-schema.git",
            "branch": "devel",
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
            **base_dep,
        },
    ]

    mock_run_cmd.return_value = json.dumps(sample_parser_output)
    result = parse_lockfile(rooted_tmp_path)

    expected_deps = [
        GitDependency(
            name="example",
            version="0.1.0",
            url="https://github.com/3scale/json-schema.git",
            branch="devel",
            ref=GIT_REF,
        ),
        PathDependency(
            name="example",
            version="0.1.0",
            root=str(rooted_tmp_path),
            subpath="vendor/pathgem",
        ),
        GemDependency(name="example", version="0.1.0", source="https://rubygems.org/"),
    ]

    assert f"Package {rooted_tmp_path.path.name} is bundled with version 2.5.10" in caplog.messages
    assert result == expected_deps


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser.run_cmd")
def test_parse_gemlock_empty(
    mock_run_cmd: mock.MagicMock,
    mock_ensure_bundler_files_exist: mock.MagicMock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_run_cmd.return_value = '{"bundler_version": "2.5.10", "dependencies": []}'
    result = parse_lockfile(rooted_tmp_path)

    assert f"Package {rooted_tmp_path.path.name} is bundled with version 2.5.10" in caplog.messages
    assert result == []


@pytest.mark.parametrize(
    "source",
    [
        "https://rubygems.org",
        "https://dedicatedprivategemrepo.com",
    ],
)
@mock.patch("hermeto.core.package_managers.bundler.gem_models.download_binary_file")
def test_source_gem_dependencies_could_be_downloaded(
    mock_downloader: mock.MagicMock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
    source: str,
) -> None:
    dependency = GemDependency(name="foo", version="0.0.2", source=source)
    expected_source_url = f"{source}/downloads/foo-0.0.2.gem"
    expected_destination = rooted_tmp_path.join_within_root("foo-0.0.2.gem")

    dependency.download_to(rooted_tmp_path)

    assert f"Downloading gem {dependency.name}" in caplog.messages
    mock_downloader.assert_called_once_with(expected_source_url, expected_destination)


@mock.patch("hermeto.core.package_managers.bundler.gem_models.download_binary_file")
def test_binary_gem_dependencies_could_be_downloaded(
    mock_downloader: mock.MagicMock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = "https://rubygems.org/"
    platform = "m6502_wm"
    dependency = GemPlatformSpecificDependency(
        name="foo",
        version="0.0.2",
        source=source,
        platform=platform,
    )
    expected_source_url = f"{source}downloads/foo-0.0.2-{platform}.gem"
    expected_destination = rooted_tmp_path.join_within_root(f"foo-0.0.2-{platform}.gem")

    dependency.download_to(rooted_tmp_path)

    assert some_message_contains_substring("Downloading platform-specific gem", caplog.messages)
    mock_downloader.assert_called_once_with(expected_source_url, expected_destination)


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
@mock.patch("hermeto.core.package_managers.bundler.parser.run_cmd")
def test_parse_gemlock_detects_binaries_and_adds_to_parse_result_when_allowed_to(
    mock_run_cmd: mock.MagicMock,
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

    mock_run_cmd.return_value = json.dumps(sample_parser_output)
    result = parse_lockfile(
        rooted_tmp_path, binary_filters=BundlerBinaryFilters.with_allow_binary_behavior()
    )

    expected_deps = [
        GemPlatformSpecificDependency(
            name="example",
            version="0.1.0",
            source="https://rubygems.org/",
            platform="i8080_cpm",
        ),
    ]

    assert some_message_contains_substring("Found a binary dependency", caplog.messages)
    assert some_message_contains_substring("Will download binary dependency", caplog.messages)
    assert result == expected_deps


@mock.patch("hermeto.core.package_managers.bundler.parser._ensure_bundler_files_exist")
@mock.patch("hermeto.core.package_managers.bundler.parser.run_cmd")
def test_parse_gemlock_detects_binaries_and_skips_then_when_instructed_to_skip(
    mock_run_cmd: mock.MagicMock,
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

    mock_run_cmd.return_value = json.dumps(sample_parser_output)
    result = parse_lockfile(rooted_tmp_path)

    expected_deps: list = []  # mypy demanded this annotation and is content with it.

    assert some_message_contains_substring("Found a binary dependency", caplog.messages)
    assert some_message_contains_substring("Skipping binary dependency", caplog.messages)

    assert result == expected_deps
