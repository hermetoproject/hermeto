# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
from collections.abc import Collection
from pathlib import Path
from textwrap import dedent
from typing import Any, Literal
from unittest import mock

import pytest
from git import Repo

from hermeto import APP_NAME
from hermeto.core.checksum import ChecksumInfo
from hermeto.core.constants import Mode
from hermeto.core.errors import (
    InvalidChecksum,
    InvalidVCSReference,
    LockfileNotFound,
    MissingChecksum,
    NotAGitRepo,
    PackageRejected,
    UnpinnedPackage,
    UnrecognizedFileExtension,
    UnsupportedFeature,
)
from hermeto.core.models.input import CargoPackageInput, Request
from hermeto.core.package_managers.cargo.main import PackageWithCorruptLockfileRejected
from hermeto.core.package_managers.pip import main as pip
from hermeto.core.package_managers.pip.packages import PipPackageInfo, URLPackage, VCSPackage
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF

CUSTOM_PYPI_ENDPOINT = "https://my-pypi.org/simple/"


def mock_distribution_package_info(
    name: str,
    version: str = "1.0",
    package_type: Literal["sdist", "wheel"] = "sdist",
    path: Path = Path(""),
    url: str = "",
    is_yanked: bool = False,
    pypi_checksum: Collection[ChecksumInfo] = (),
    req_file_checksums: Collection[ChecksumInfo] = (),
) -> pip.DistributionPackageInfo:
    return pip.DistributionPackageInfo(
        name=name,
        version=version,
        package_type=package_type,
        path=path,
        url=url,
        is_yanked=is_yanked,
        pypi_checksums=set(pypi_checksum),
        req_file_checksums=set(req_file_checksums),
    )


def mock_requirement(
    package: Any,
    kind: Any,
    version_specs: Any = None,
    download_line: Any = None,
    hashes: Any = None,
    qualifiers: Any = None,
    url: Any = None,
) -> Any:
    """Mock a requirements.txt item. By default should pass validation."""
    if url is None and kind == "vcs":
        url = f"git+https://github.com/example@{GIT_REF}"
    elif url is None and kind == "url":
        url = "https://example.org/file.tar.gz"

    if hashes is None and qualifiers is None and kind == "url":
        hashes = ["sha256:abcdef"]

    return mock.Mock(
        package=package,
        kind=kind,
        version_specs=version_specs if version_specs is not None else [("==", "1")],
        download_line=download_line or package,
        hashes=hashes or [],
        qualifiers=qualifiers or {},
        url=url,
    )


def mock_requirements_file(requirements: list | None = None, options: list | None = None) -> Any:
    """Mock a requirements.txt file."""
    return mock.Mock(requirements=requirements or [], options=options or [])


@pytest.mark.parametrize(
    "mock_target",
    [
        pytest.param("PyProjectTOML", id="pyproject_toml"),
        pytest.param("SetupPY", id="setup_py"),
        pytest.param("SetupCFG", id="setup_cfg"),
    ],
)
def test_get_pip_metadata_from_project_file(
    mock_target: str,
    rooted_tmp_path: RootedPath,
) -> None:
    with mock.patch(f"hermeto.core.package_managers.pip.main.{mock_target}") as mock_cls:
        instance = mock_cls.return_value
        instance.exists.return_value = True
        instance.get_name.return_value = "foo"
        instance.get_version.return_value = "0.1.0"

        name, version = pip._get_pip_metadata(rooted_tmp_path)
        assert name == "foo"
        assert version == "0.1.0"


@mock.patch("hermeto.core.package_managers.pip.main.PyProjectTOML")
@mock.patch("hermeto.core.package_managers.pip.main.SetupCFG")
@mock.patch("hermeto.core.package_managers.pip.main.SetupPY")
def test_extract_metadata_from_config_files_with_fallbacks(
    mock_setup_py: mock.Mock,
    mock_setup_cfg: mock.Mock,
    mock_pyproject_toml: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Case 1: Only pyproject.toml exists with name but no version
    pyproject_toml = mock_pyproject_toml.return_value
    pyproject_toml.exists.return_value = True
    pyproject_toml.get_name.return_value = "name_from_pyproject_toml"
    pyproject_toml.get_version.return_value = None

    setup_cfg = mock_setup_cfg.return_value
    setup_cfg.exists.return_value = False

    setup_py = mock_setup_py.return_value
    setup_py.exists.return_value = False

    name, version = pip._extract_metadata_from_config_files(rooted_tmp_path)
    assert name == "name_from_pyproject_toml"
    assert version is None
    assert "Checking pyproject.toml for metadata" in caplog.messages

    # Case 2: pyproject.toml exists but without a name; fallback to setup.py with name and version
    pyproject_toml.get_name.return_value = None

    setup_py.exists.return_value = True
    setup_py.get_name.return_value = "name_from_setup_py"
    setup_py.get_version.return_value = "0.1.0"

    name, version = pip._extract_metadata_from_config_files(rooted_tmp_path)
    assert name == "name_from_setup_py"
    assert version == "0.1.0"
    assert "Checking setup.py for metadata" in caplog.messages

    # Case 3: Both pyproject.toml and setup.py lack names; fallback to setup.cfg with complete metadata
    setup_py.get_name.return_value = None

    setup_cfg.exists.return_value = True
    setup_cfg.get_name.return_value = "name_from_setup_cfg"
    setup_cfg.get_version.return_value = "0.2.0"

    name, version = pip._extract_metadata_from_config_files(rooted_tmp_path)
    assert name == "name_from_setup_cfg"
    assert version == "0.2.0"
    assert "Checking setup.cfg for metadata" in caplog.messages

    # Case 4: None of the config files have names, resulting in None, None
    setup_cfg.get_name.return_value = None

    name, version = pip._extract_metadata_from_config_files(rooted_tmp_path)
    assert name is None
    assert version is None


@pytest.mark.parametrize(
    "origin_exists",
    [True, False],
)
@mock.patch("hermeto.core.package_managers.pip.main.PyProjectTOML")
@mock.patch("hermeto.core.package_managers.pip.main.SetupPY")
@mock.patch("hermeto.core.package_managers.pip.main.SetupCFG")
def test_get_pip_metadata_from_remote_origin(
    mock_setup_cfg: mock.Mock,
    mock_setup_py: mock.Mock,
    mock_pyproject_toml: mock.Mock,
    origin_exists: bool,
    rooted_tmp_path_repo: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pyproject_toml = mock_pyproject_toml.return_value
    pyproject_toml.exists.return_value = False

    setup_py = mock_setup_py.return_value
    setup_py.exists.return_value = False

    setup_cfg = mock_setup_cfg.return_value
    setup_cfg.exists.return_value = False

    if origin_exists:
        repo = Repo(rooted_tmp_path_repo)
        repo.create_remote("origin", "git@github.com:user/repo.git")

        name, version = pip._get_pip_metadata(rooted_tmp_path_repo)
        assert name == "repo"
        assert version is None

        assert f"Resolved name repo for package at {rooted_tmp_path_repo}" in caplog.messages
        assert f"Could not resolve version for package at {rooted_tmp_path_repo}" in caplog.messages
    else:
        with pytest.raises(PackageRejected) as exc_info:
            pip._get_pip_metadata(rooted_tmp_path_repo)

        assert str(exc_info.value) == "Unable to infer package name from origin URL"


class TestDownload:
    """Tests for dependency downloading."""

    @mock.patch("hermeto.core.package_managers.pip.main.clone_as_tarball")
    def test_download_vcs_package(
        self,
        mock_clone_as_tarball: Any,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Test downloading of a single VCS package."""
        vcs_url = f"git+https://github.com/spam/eggs@{GIT_REF}"

        req = mock_requirement("eggs", "vcs", url=vcs_url, download_line=f"eggs @ {vcs_url}")
        req_file = mock_requirements_file(requirements=[req])

        result = pip._download_vcs_package(req, req_file, rooted_tmp_path)

        assert isinstance(result, VCSPackage)
        assert result.name == "eggs"
        assert (
            result.path == rooted_tmp_path.join_within_root(f"eggs-gitcommit-{GIT_REF}.tar.gz").path
        )
        assert result.requirement_file == str(req_file.file_path.subpath_from_root)
        assert result.missing_req_file_checksum is True
        assert result.package_type == ""
        assert result.url == "https://github.com/spam/eggs"
        assert result.ref == GIT_REF

        mock_clone_as_tarball.assert_called_once_with(
            "https://github.com/spam/eggs", GIT_REF, to_path=result.path
        )

    @pytest.mark.parametrize(
        "host_in_url, trusted_hosts, host_is_trusted",
        [
            ("example.org", [], False),
            ("example.org", ["example.org"], True),
            ("example.org:443", ["example.org:443"], True),
            # 'host' in URL does not match 'host:port' in trusted hosts
            ("example.org", ["example.org:443"], False),
            # 'host:port' in URL *does* match 'host' in trusted hosts
            ("example.org:443", ["example.org"], True),
        ],
    )
    @mock.patch(
        "hermeto.core.package_managers.pip.main._checksum_must_match_or_path_unlink",
        return_value=True,
    )
    @mock.patch("hermeto.core.package_managers.pip.main.download_binary_file")
    def test_download_url_package(
        self,
        mock_download_file: Any,
        mock_checksum: Any,
        host_in_url: bool,
        trusted_hosts: list[str],
        host_is_trusted: bool,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Test downloading of a single URL package."""
        original_url = f"https://{host_in_url}/foo.tar.gz"

        req = mock_requirement(
            "foo",
            "url",
            url=original_url,
            download_line=f"foo @ {original_url}",
            hashes=["sha256:abcdef"],
        )
        req_file = mock_requirements_file(requirements=[req])

        result = pip._download_url_package(
            req,
            req_file,
            rooted_tmp_path,
            set(trusted_hosts),
        )

        assert isinstance(result, URLPackage)
        assert result.name == "foo"
        assert result.path == rooted_tmp_path.join_within_root("foo-abcdef.tar.gz").path
        assert result.requirement_file == str(req_file.file_path.subpath_from_root)
        assert result.missing_req_file_checksum is False
        assert result.package_type == ""
        assert result.original_url == original_url
        assert result.checksum == "sha256:abcdef"

        mock_download_file.assert_called_once_with(
            original_url, result.path, insecure=host_is_trusted
        )

    @pytest.mark.parametrize(
        "url_path, expected_type",
        [
            pytest.param("/pkg-1.0-py3-none-any.whl", "wheel", id="wheel"),
            pytest.param("/pkg-1.0-py3-none-any.whl#sha256=abc", "wheel", id="wheel_with_fragment"),
            pytest.param(
                "/pkg-1.0-py3-none-any.whl?v=1#sha256=abc", "wheel", id="wheel_with_query"
            ),
            pytest.param("/pkg-1.0.tar.gz", "", id="sdist"),
        ],
    )
    @mock.patch(
        "hermeto.core.package_managers.pip.main._checksum_must_match_or_path_unlink",
        return_value=True,
    )
    @mock.patch("hermeto.core.package_managers.pip.main.download_binary_file")
    def test_download_url_package_identifies_wheel_from_url(
        self,
        mock_download_file: Any,
        mock_checksum: Any,
        url_path: str,
        expected_type: str,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Wheel detection works even when the URL contains fragments or query strings."""
        url = f"https://example.org{url_path}"
        req = mock_requirement(
            "foo", "url", url=url, download_line=f"foo @ {url}", hashes=["sha256:abcdef"]
        )
        req_file = mock_requirements_file(requirements=[req])

        result = pip._download_url_package(req, req_file, rooted_tmp_path, set())

        assert isinstance(result, URLPackage)
        assert result.package_type == expected_type

    def test_ignored_and_rejected_options(self, caplog: pytest.LogCaptureFixture) -> None:
        """
        Test ignored and rejected options.

        All ignored options should be logged, all rejected options should be in error message.
        """
        all_rejected = [
            "--extra-index-url",
            "--no-index",
            "-f",
            "--find-links",
            "--only-binary",
        ]
        options = all_rejected + ["-c", "constraints.txt", "--use-feature", "some_feature", "--foo"]
        req_file = mock_requirements_file(options=options)
        with pytest.raises(UnsupportedFeature) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        err_msg = (
            f"{APP_NAME} does not support the following options: --extra-index-url, "
            "--no-index, -f, --find-links, --only-binary"
        )
        assert str(exc_info.value) == err_msg

        log_msg = f"{APP_NAME} will ignore the following options: -c, --use-feature, --foo"
        assert log_msg in caplog.text

    @pytest.mark.parametrize(
        "req_kwargs, exc_type",
        [
            pytest.param(
                {"package": "foo", "kind": "pypi", "version_specs": []},
                UnpinnedPackage,
                id="pypi_unpinned_no_specs",
            ),
            pytest.param(
                {"package": "foo", "kind": "pypi", "version_specs": [("<", "1")]},
                UnpinnedPackage,
                id="pypi_unpinned_less_than",
            ),
            pytest.param(
                {"package": "foo", "kind": "pypi", "version_specs": [("==", "1"), ("<", "2")]},
                UnpinnedPackage,
                id="pypi_unpinned_mixed_specs",
            ),
            pytest.param(
                {"package": "foo", "kind": "pypi", "version_specs": [("==", "1"), ("==", "1")]},
                UnpinnedPackage,
                id="pypi_unpinned_duplicate_eq",
            ),
            pytest.param(
                {
                    "package": "eggs",
                    "kind": "vcs",
                    "url": "git+https://github.com/spam/eggs",
                    "download_line": "eggs @ git+https://github.com/spam/eggs",
                },
                InvalidVCSReference,
                id="vcs_no_ref",
            ),
            pytest.param(
                {
                    "package": "eggs",
                    "kind": "vcs",
                    "url": "git+https://github.com/spam/eggs@",
                    "download_line": "eggs @ git+https://github.com/spam/eggs@",
                },
                InvalidVCSReference,
                id="vcs_empty_ref",
            ),
            pytest.param(
                {
                    "package": "eggs",
                    "kind": "vcs",
                    "url": "git+https://github.com/spam/eggs@abcdef",
                    "download_line": "eggs @ git+https://github.com/spam/eggs@abcdef",
                },
                InvalidVCSReference,
                id="vcs_short_ref",
            ),
            pytest.param(
                {
                    "package": "eggs",
                    "kind": "vcs",
                    "url": f"git+https://github.com@{GIT_REF}/spam/eggs",
                    "download_line": f"eggs @ git+https://github.com@{GIT_REF}/spam/eggs",
                },
                InvalidVCSReference,
                id="vcs_ref_in_host",
            ),
            pytest.param(
                {
                    "package": "eggs",
                    "kind": "vcs",
                    "url": f"git+https://github.com/spam/eggs#@{GIT_REF}",
                    "download_line": f"eggs @ git+https://github.com/spam/eggs#@{GIT_REF}",
                },
                InvalidVCSReference,
                id="vcs_ref_in_fragment",
            ),
            pytest.param(
                {
                    "package": "eggs",
                    "kind": "vcs",
                    "url": "svn://example.org/spam/eggs",
                    "download_line": "eggs @ svn://example.org/spam/eggs",
                },
                UnsupportedFeature,
                id="vcs_svn_scheme",
            ),
            pytest.param(
                {
                    "package": "eggs",
                    "kind": "vcs",
                    "url": "svn+https://example.org/spam/eggs",
                    "download_line": "eggs @ svn+https://example.org/spam/eggs",
                },
                UnsupportedFeature,
                id="vcs_svn_https_scheme",
            ),
            pytest.param(
                {
                    "package": "foo",
                    "kind": "url",
                    "hashes": [],
                    "qualifiers": {},
                    "url": "http://example.org/foo.tar.gz",
                    "download_line": "foo @ http://example.org/foo.tar.gz",
                },
                InvalidChecksum,
                id="url_no_hash",
            ),
            pytest.param(
                {
                    "package": "foo",
                    "kind": "url",
                    "hashes": ["sha256:123456", "sha256:abcdef"],
                    "qualifiers": {},
                    "url": "http://example.org/foo.tar.gz",
                    "download_line": "foo @ http://example.org/foo.tar.gz",
                },
                InvalidChecksum,
                id="url_two_hashes",
            ),
            pytest.param(
                {
                    "package": "foo",
                    "kind": "url",
                    "url": "http://example.org/file.rar",
                    "download_line": "foo @ http://example.org/file.rar",
                },
                UnrecognizedFileExtension,
                id="url_rar_extension",
            ),
            pytest.param(
                {
                    "package": "foo",
                    "kind": "url",
                    "url": "https://example.org/file.wheel",
                    "download_line": "foo @ https://example.org/file.wheel",
                },
                UnrecognizedFileExtension,
                id="url_wheel_extension",
            ),
            pytest.param(
                {
                    "package": "foo",
                    "kind": "url",
                    "url": "http://example.tar.gz/file",
                    "download_line": "foo @ http://example.tar.gz/file",
                },
                UnrecognizedFileExtension,
                id="url_ext_in_host",
            ),
            pytest.param(
                {
                    "package": "foo",
                    "kind": "url",
                    "url": "http://example.org/file?filename=file.tar.gz",
                    "download_line": "foo @ http://example.org/file?filename=file.tar.gz",
                },
                UnrecognizedFileExtension,
                id="url_ext_in_query",
            ),
        ],
    )
    def test_download_rejects_invalid_dependency(
        self, req_kwargs: dict[str, Any], exc_type: type[Exception]
    ) -> None:
        """Test that invalid dependencies (unpinned, bad VCS ref, bad URL) are rejected."""
        req = mock_requirement(**req_kwargs)
        req_file = mock_requirements_file(requirements=[req])
        with pytest.raises(exc_type):
            pip._download_dependencies(RootedPath("/output"), req_file)

    @pytest.mark.parametrize(
        "requirements, options, exc_type",
        [
            pytest.param(
                [
                    ("foo", "pypi", {"hashes": ["sha256:abcdef"]}),
                    ("bar", "pypi", {}),
                ],
                [],
                MissingChecksum,
                id="pypi_local_hash_triggers_missing",
            ),
            pytest.param(
                [
                    ("foo", "vcs", {"hashes": ["sha256:abcdef"]}),
                    ("bar", "vcs", {}),
                ],
                [],
                UnsupportedFeature,
                id="vcs_local_hash_triggers_unsupported",
            ),
            pytest.param(
                [
                    ("foo", "pypi", {}),
                    ("bar", "pypi", {}),
                ],
                ["--require-hashes"],
                MissingChecksum,
                id="pypi_global_require_hashes",
            ),
            pytest.param(
                [
                    ("foo", "vcs", {}),
                    ("bar", "vcs", {}),
                ],
                ["--require-hashes"],
                UnsupportedFeature,
                id="vcs_global_require_hashes",
            ),
            pytest.param(
                [
                    ("foo", "pypi", {"hashes": ["sha256:abcdef"]}),
                    ("bar", "pypi", {}),
                ],
                ["--require-hashes"],
                MissingChecksum,
                id="pypi_both_global_and_local_hash",
            ),
            pytest.param(
                [
                    ("foo", "vcs", {"hashes": ["sha256:abcdef"]}),
                    ("bar", "vcs", {}),
                ],
                ["--require-hashes"],
                UnsupportedFeature,
                id="vcs_both_global_and_local_hash",
            ),
            pytest.param(
                [("foo", "pypi", {"hashes": ["malformed"]})],
                [],
                InvalidChecksum,
                id="pypi_malformed_hash",
            ),
            pytest.param(
                [("foo", "vcs", {"hashes": ["malformed"]})],
                [],
                UnsupportedFeature,
                id="vcs_malformed_hash_triggers_unsupported",
            ),
            pytest.param(
                [("foo", "url", {"hashes": ["malformed"]})],
                [],
                InvalidChecksum,
                id="url_malformed_hash",
            ),
        ],
    )
    def test_download_rejects_missing_or_malformed_hash(
        self,
        requirements: list[tuple[str, str, dict[str, Any]]],
        options: list[str],
        exc_type: type[Exception],
    ) -> None:
        """Test that missing or malformed hashes cause the expected validation error."""
        reqs = [mock_requirement(pkg, kind, **kwargs) for pkg, kind, kwargs in requirements]
        req_file = mock_requirements_file(requirements=reqs, options=options)
        with pytest.raises(exc_type):
            pip._download_dependencies(RootedPath("/output"), req_file)


@pytest.mark.parametrize(
    "file_kwarg",
    [
        pytest.param("requirement_files", id="requirement_files"),
        pytest.param("build_requirement_files", id="build_requirement_files"),
    ],
)
@mock.patch("hermeto.core.package_managers.pip.main._get_pip_metadata")
def test_resolve_pip_invalid_file_path(
    mock_metadata: mock.Mock, rooted_tmp_path: RootedPath, file_kwarg: str
) -> None:
    mock_metadata.return_value = ("foo", "1.0")
    invalid_path = Path("foo/bar.txt")
    output_dir = rooted_tmp_path.join_within_root("output")
    kwargs = {file_kwarg: [invalid_path]}
    with pytest.raises(LockfileNotFound):
        pip._resolve_pip(
            package_path=rooted_tmp_path,
            output_dir=output_dir,
            **kwargs,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "component_kind, url",
    (
        ["vcs", f"git+https://github.com/hermeto/mypkg.git@{'f' * 40}?egg=mypkg"],
        ["url", "https://files.hermeto.rocks/mypkg.tar.gz"],
    ),
)
def test_get_external_requirement_filepath(component_kind: str, url: str) -> None:
    requirement = mock.Mock(
        kind=component_kind, url=url, package="package", hashes=["sha256:noRealHash"]
    )
    filepath = pip._get_external_requirement_filepath(requirement)
    if component_kind == "url":
        assert filepath == Path("package-noRealHash.tar.gz")
    elif component_kind == "vcs":
        assert filepath == Path(f"mypkg-gitcommit-{'f' * 40}.tar.gz")
    else:
        raise AssertionError()


@pytest.mark.parametrize(
    "sdist_filename, exc_type, expected_error",
    [
        pytest.param(
            "myapp-0.1.tar.fake.zip", PackageRejected, "a Zip file. Error:", id="fake_zip"
        ),
        pytest.param(
            "myapp-0.1.zip.fake.tar", PackageRejected, "a Tar file. Error:", id="fake_tar"
        ),
        pytest.param(
            "myapp-without-pkg-info.tar.gz",
            PackageRejected,
            "not include metadata",
            id="missing_pkg_info",
        ),
        pytest.param(
            "myapp-0.2.tar.ZZZ", ValueError, "Cannot check metadata", id="invalid_extension"
        ),
    ],
)
def test_metadata_check_fails_from_sdist(
    sdist_filename: str,
    exc_type: type[Exception],
    expected_error: str,
    data_dir: Path,
) -> None:
    sdist_path = data_dir / "archives" / sdist_filename
    with pytest.raises(exc_type, match=expected_error):
        pip._check_metadata_in_sdist(sdist_path)


@pytest.mark.parametrize(
    "original_content, expect_replaced",
    [
        (
            dedent(
                """\
                foo==1.0.0
                bar==2.0.0
                """
            ),
            None,
        ),
        (
            dedent(
                f"""\
                foo==1.0.0
                bar @ git+https://github.com/org/bar@{GIT_REF}
                """
            ),
            dedent(
                f"""\
                foo==1.0.0
                bar @ file://${{output_dir}}/deps/pip/bar-gitcommit-{GIT_REF}.tar.gz
                """
            ),
        ),
        (
            dedent(
                """\
                --require-hashes
                foo==1.0.0 --hash=sha256:abcdef
                bar @ https://github.com/org/bar/archive/refs/tags/bar-2.0.0.zip --hash=sha256:fedcba
                """
            ),
            dedent(
                """\
                --require-hashes
                foo==1.0.0 --hash=sha256:abcdef
                bar @ file://${output_dir}/deps/pip/bar-fedcba.zip --hash=sha256:fedcba
                """
            ),
        ),
    ],
)
def test_replace_external_requirements(
    original_content: str, expect_replaced: str | None, rooted_tmp_path: RootedPath
) -> None:
    requirements_file = rooted_tmp_path.join_within_root("requirements.txt")
    requirements_file.path.write_text(original_content)

    replaced_file = pip._replace_external_requirements(requirements_file)
    if expect_replaced is None:
        assert replaced_file is None
    else:
        assert replaced_file is not None
        assert replaced_file.template == expect_replaced
        assert replaced_file.abspath == requirements_file.path


@pytest.mark.parametrize(
    "subpath, expected_purl",
    [
        (
            ".",
            f"pkg:pypi/foo@1.0.0?vcs_url=git%2Bssh://git%40github.com/my-org/my-repo%40{'f' * 40}",
        ),
        (
            "path/to/package",
            f"pkg:pypi/foo@1.0.0?vcs_url=git%2Bssh://git%40github.com/my-org/my-repo%40{'f' * 40}#path/to/package",
        ),
    ],
)
@mock.patch("hermeto.core.scm.GitRepo")
def test_generate_purl_main_package(
    mock_git_repo: Any, subpath: Path, expected_purl: str, rooted_tmp_path: RootedPath
) -> None:
    package = PipPackageInfo(
        name="foo",
        version="1.0.0",
        requires=[],
        build_requires=[],
        requirements=[],
        packages_containing_rust_code=[],
    )

    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = "ssh://git@github.com/my-org/my-repo"
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    purl = pip._generate_purl_main_package(package, rooted_tmp_path.join_within_root(subpath))

    assert purl == expected_purl


@pytest.mark.parametrize(
    "subpath, expected_purl",
    [
        (
            ".",
            "pkg:pypi/foo@1.0.0",
        ),
        (
            "path/to/package",
            "pkg:pypi/foo@1.0.0#path/to/package",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.pip.main.get_config")
@mock.patch("hermeto.core.package_managers.pip.main.get_repo_id")
def test_generate_purl_main_package_permissive_mode_without_vcs_url(
    mock_handle_get_repo_id: mock.Mock,
    mock_get_config: mock.Mock,
    subpath: Path,
    expected_purl: str,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_handle_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.PERMISSIVE
    package = PipPackageInfo(
        name="foo",
        version="1.0.0",
        requires=[],
        build_requires=[],
        requirements=[],
        packages_containing_rust_code=[],
    )

    purl = pip._generate_purl_main_package(package, rooted_tmp_path.join_within_root(subpath))

    assert purl == expected_purl


@mock.patch("hermeto.core.package_managers.pip.main.get_config")
@mock.patch("hermeto.core.package_managers.pip.main.get_repo_id")
def test_generate_purl_main_package_strict_mode_raises_without_git_repo(
    mock_get_repo_id: mock.Mock,
    mock_get_config: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.STRICT
    package = PipPackageInfo(
        name="foo",
        version="1.0.0",
        requires=[],
        build_requires=[],
        requirements=[],
        packages_containing_rust_code=[],
    )

    with pytest.raises(NotAGitRepo):
        pip._generate_purl_main_package(package, rooted_tmp_path.join_within_root("."))


@pytest.mark.parametrize(
    "subpath, expected_purl",
    [
        (
            ".",
            f"pkg:pypi/foo@1.0.0?vcs_url=git%2Bssh://git%40github.com/my-org/my-repo%40{'f' * 40}",
        ),
        (
            "path/to/package",
            f"pkg:pypi/foo@1.0.0?vcs_url=git%2Bssh://git%40github.com/my-org/my-repo%40{'f' * 40}#path/to/package",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.pip.main.get_config")
@mock.patch("hermeto.core.package_managers.pip.main.get_repo_id")
@mock.patch("hermeto.core.scm.GitRepo")
def test_generate_purl_main_package_permissive_mode_with_vcs_url(
    mock_git_repo: mock.Mock,
    mock_get_repo_id: mock.Mock,
    mock_get_config: mock.Mock,
    subpath: Path,
    expected_purl: str,
    rooted_tmp_path: RootedPath,
) -> None:
    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = "ssh://git@github.com/my-org/my-repo"
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    mock_get_config.return_value.mode = Mode.PERMISSIVE
    package = PipPackageInfo(
        name="foo",
        version="1.0.0",
        requires=[],
        build_requires=[],
        requirements=[],
        packages_containing_rust_code=[],
    )

    purl = pip._generate_purl_main_package(package, rooted_tmp_path.join_within_root(subpath))

    assert purl == expected_purl


@mock.patch("hermeto.core.package_managers.pip.main.get_repo_id")
def test_infer_package_name_raises_without_git_repo(
    mock_handle_get_repo_id: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_handle_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")

    with pytest.raises(PackageRejected):
        pip._infer_package_name_from_origin_url(rooted_tmp_path)


@mock.patch("hermeto.core.scm.GitRepo")
@mock.patch("hermeto.core.package_managers.pip.main._replace_external_requirements")
@mock.patch("hermeto.core.package_managers.pip.main._resolve_pip")
@mock.patch("hermeto.core.package_managers.cargo.main.run_cmd")
@mock.patch("hermeto.core.package_managers.cargo.main._verify_lockfile_is_present")
def test_fetch_pip_source_correctly_reraises_when_there_is_a_dependency_cargo_lock_mismatch(
    mock_verify_lockfile_present: mock.Mock,
    mock_run_cmd: mock.Mock,
    mock_resolve_pip: mock.Mock,
    mock_replace_requirements: mock.Mock,
    mock_git_repo: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    # Making this a pip test since it is pip who is affected by the problem the most.
    source_dir = rooted_tmp_path.re_root("source")
    output_dir = rooted_tmp_path.re_root("output")
    source_dir.path.mkdir()

    request = Request(
        source_dir=source_dir,
        output_dir=output_dir,
        packages=[{"type": "pip", "requirements_files": ["requirements.txt"]}],
    )

    mock_run_cmd.side_effect = subprocess.CalledProcessError(
        cmd="test",
        returncode=101,
        stderr="... failed to sync ... because --locked was passed to prevent this ...",
    )
    mock_verify_lockfile_present.return_value = None

    resolved = PipPackageInfo(
        name="foo",
        version="1.0",
        requires=[],
        build_requires=[],
        requirements=[],
        packages_containing_rust_code=[CargoPackageInput(type="cargo", path=".")],
    )

    mock_resolve_pip.return_value = resolved

    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = "https://github.com/my-org/my-repo"
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    with pytest.raises(PackageWithCorruptLockfileRejected):
        pip.fetch_pip_source(request)
