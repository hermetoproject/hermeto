# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
from collections.abc import Collection
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any, Literal
from unittest import mock
from urllib.parse import urlparse

import pypi_simple
import pytest
from git import Repo

from hermeto import APP_NAME
from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import (
    InvalidChecksum,
    LockfileNotFound,
    MissingChecksum,
    PackageRejected,
    UnsupportedFeature,
)
from hermeto.core.models.input import CargoPackageInput, PackageInput, PipBinaryFilters, Request
from hermeto.core.models.output import ProjectFile
from hermeto.core.models.sbom import Annotation, Component, Property
from hermeto.core.package_managers.cargo.main import PackageWithCorruptLockfileRejected
from hermeto.core.package_managers.pip import main as pip
from hermeto.core.package_managers.pip.models import PyPIArtifact, URLArtifact, VCSArtifact
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF

CUSTOM_PYPI_ENDPOINT = "https://my-pypi.org/simple/"


def mock_distribution_package_info(
    name: str,
    version: str = "1.0",
    package_type: Literal["sdist", "wheel"] = "sdist",
    path: Path = Path(""),
    url: str = "",
    index_url: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
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
        index_url=index_url,
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
        qualifiers = {"cachito_hash": "sha256:abcdef"}

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


@mock.patch("hermeto.core.package_managers.pip.main.PyProjectTOML")
def test_get_pip_metadata_from_pyproject_toml(
    mock_pyproject_toml: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pyproject_toml = mock_pyproject_toml.return_value
    pyproject_toml.exists.return_value = True
    pyproject_toml.get_name.return_value = "foo"
    pyproject_toml.get_version.return_value = "0.1.0"

    name, version = pip._get_pip_metadata(rooted_tmp_path)
    assert name == "foo"
    assert version == "0.1.0"
    assert "Checking pyproject.toml for metadata" in caplog.messages

    # check logs
    assert f"Resolved name {name} for package at {rooted_tmp_path}" in caplog.messages
    assert f"Resolved version {version} for package at {rooted_tmp_path}" in caplog.messages


@mock.patch("hermeto.core.package_managers.pip.main.SetupPY")
def test_get_pip_metadata_from_setup_py(
    mock_setup_py: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    setup_py = mock_setup_py.return_value
    setup_py.exists.return_value = True
    setup_py.get_name.return_value = "foo"
    setup_py.get_version.return_value = "0.1.0"

    name, version = pip._get_pip_metadata(rooted_tmp_path)
    assert name == "foo"
    assert version == "0.1.0"

    # check logs
    assert "Checking setup.py for metadata" in caplog.messages
    assert f"Resolved name {name} for package at {rooted_tmp_path}" in caplog.messages
    assert f"Resolved version {version} for package at {rooted_tmp_path}" in caplog.messages


@mock.patch("hermeto.core.package_managers.pip.main.SetupCFG")
def test_get_pip_metadata_from_setup_cfg(
    mock_setup_cfg: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    setup_cfg = mock_setup_cfg.return_value
    setup_cfg.exists.return_value = True
    setup_cfg.get_name.return_value = "foo"
    setup_cfg.get_version.return_value = "0.1.0"

    name, version = pip._get_pip_metadata(rooted_tmp_path)
    assert name == "foo"
    assert version == "0.1.0"

    # check logs
    assert "Checking setup.cfg for metadata" in caplog.messages
    assert f"Resolved name {name} for package at {rooted_tmp_path}" in caplog.messages
    assert f"Resolved version {version} for package at {rooted_tmp_path}" in caplog.messages


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

        download_info = pip._download_vcs_package(req, rooted_tmp_path)

        assert download_info == {
            "package": "eggs",
            "path": rooted_tmp_path.join_within_root(f"eggs-gitcommit-{GIT_REF}.tar.gz").path,
            "url": "https://github.com/spam/eggs",
            "ref": GIT_REF,
            "namespace": "spam",
            "repo": "eggs",
            "host": "github.com",
        }

        download_path = download_info["path"]

        mock_clone_as_tarball.assert_called_once_with(
            "https://github.com/spam/eggs", GIT_REF, to_path=download_path
        )

    @pytest.mark.parametrize("hash_as_qualifier", [True, False])
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
    @mock.patch("hermeto.core.package_managers.pip.main.download_binary_file")
    def test_download_url_package(
        self,
        mock_download_file: Any,
        hash_as_qualifier: bool,
        host_in_url: bool,
        trusted_hosts: list[str],
        host_is_trusted: bool,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Test downloading of a single URL package."""
        # Add the #cachito_package fragment to make sure the .tar.gz extension
        # will be found even if the URL does not end with it
        original_url = f"https://{host_in_url}/foo.tar.gz#cachito_package=foo"
        url_with_hash = f"{original_url}&cachito_hash=sha256:abcdef"
        if hash_as_qualifier:
            original_url = url_with_hash

        req = mock_requirement(
            "foo",
            "url",
            url=original_url,
            download_line=f"foo @ {original_url}",
            hashes=["sha256:abcdef"] if not hash_as_qualifier else [],
            qualifiers={"cachito_hash": "sha256:abcdef"} if hash_as_qualifier else {},
        )

        download_info = pip._download_url_package(
            req,
            rooted_tmp_path,
            set(trusted_hosts),
        )

        assert download_info == {
            "package": "foo",
            "path": rooted_tmp_path.join_within_root("foo-abcdef.tar.gz").path,
            "original_url": original_url,
            "url_with_hash": url_with_hash,
        }

        download_path = download_info["path"]
        mock_download_file.assert_called_once_with(
            original_url, download_path, insecure=host_is_trusted
        )

    @pytest.mark.parametrize(
        "original_url, url_with_hash",
        [
            (
                "http://example.org/file.zip",
                "http://example.org/file.zip#cachito_hash=sha256:abcdef",
            ),
            (
                "http://example.org/file.zip#egg=spam",
                "http://example.org/file.zip#egg=spam&cachito_hash=sha256:abcdef",
            ),
        ],
    )
    def test_add_cachito_hash_to_url(self, original_url: str, url_with_hash: str) -> None:
        """Test adding the #cachito_hash fragment to URLs."""
        hsh = "sha256:abcdef"
        assert pip._add_cachito_hash_to_url(urlparse(original_url), hsh) == url_with_hash

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
        "version_specs",
        [
            [],
            [("<", "1")],
            [("==", "1"), ("<", "2")],
            [("==", "1"), ("==", "1")],  # Probably no reason to handle this?
        ],
    )
    def test_pypi_dep_not_pinned(self, version_specs: list[str]) -> None:
        """Test that unpinned PyPI deps cause a PackageRejected error."""
        req = mock_requirement("foo", "pypi", version_specs=version_specs)
        req_file = mock_requirements_file(requirements=[req])
        with pytest.raises(PackageRejected) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)
        msg = f"Requirement must be pinned to an exact version: {req.download_line}"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize(
        "url",
        [
            # there is no ref
            "git+https://github.com/spam/eggs",
            "git+https://github.com/spam/eggs@",
            # ref is too short
            "git+https://github.com/spam/eggs@abcdef",
            # ref is in the wrong place
            f"git+https://github.com@{GIT_REF}/spam/eggs",
            f"git+https://github.com/spam/eggs#@{GIT_REF}",
        ],
    )
    def test_vcs_dep_no_git_ref(self, url: str) -> None:
        """Test that VCS deps with no git ref cause a PackageRejected error."""
        req = mock_requirement("eggs", "vcs", url=url, download_line=f"eggs @ {url}")
        req_file = mock_requirements_file(requirements=[req])

        with pytest.raises(PackageRejected) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        msg = f"No git ref in {req.download_line} (expected 40 hexadecimal characters)"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize("scheme", ["svn", "svn+https"])
    def test_vcs_dep_not_git(self, scheme: str) -> None:
        """Test that VCS deps not from git cause an UnsupportedFeature error."""
        url = f"{scheme}://example.org/spam/eggs"
        req = mock_requirement("eggs", "vcs", url=url, download_line=f"eggs @ {url}")
        req_file = mock_requirements_file(requirements=[req])

        with pytest.raises(UnsupportedFeature) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        msg = f"Unsupported VCS for {req.download_line}: {scheme} (only git is supported)"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize(
        "hashes, cachito_hash, total",
        [
            ([], None, 0),  # No --hash, no #cachito_hash
            (["sha256:123456", "sha256:abcdef"], None, 2),  # 2x --hash
            (["sha256:123456"], "sha256:abcdef", 2),  # 1x --hash, #cachito_hash
        ],
    )
    def test_url_dep_invalid_hash_count(
        self, hashes: list[str], cachito_hash: str | None, total: int
    ) -> None:
        """Test that if URL requirement specifies 0 or more than 1 hash, validation fails."""
        if cachito_hash:
            qualifiers = {"cachito_hash": cachito_hash}
        else:
            qualifiers = {}

        url = "http://example.org/foo.tar.gz"
        req = mock_requirement(
            "foo", "url", hashes=hashes, qualifiers=qualifiers, download_line=f"foo @ {url}"
        )
        req_file = mock_requirements_file(requirements=[req])

        with pytest.raises(InvalidChecksum):
            pip._download_dependencies(RootedPath("/output"), req_file)

    @pytest.mark.parametrize(
        "url",
        [
            # .rar is not a valid sdist extension
            "http://example.org/file.rar",
            # .wheel is not a valid extension
            "https://example.org/file.wheel",
            # extension is in the wrong place
            "http://example.tar.gz/file",
            "http://example.org/file?filename=file.tar.gz",
        ],
    )
    def test_url_dep_unknown_file_ext(self, url: str) -> None:
        """Test that missing / unknown file extension in URL causes a validation error."""
        req = mock_requirement("foo", "url", url=url, download_line=f"foo @ {url}")
        req_file = mock_requirements_file(requirements=[req])

        match = "URL for requirement does not contain any recognized file extension:"
        with pytest.raises(PackageRejected, match=match):
            pip._download_dependencies(RootedPath("/output"), req_file)

    @pytest.mark.parametrize(
        "global_require_hash, local_hash", [(True, False), (False, True), (True, True)]
    )
    @pytest.mark.parametrize("requirement_kind", ["pypi", "vcs"])
    def test_requirement_missing_hash(
        self,
        global_require_hash: bool,
        local_hash: bool,
        requirement_kind: str,
    ) -> None:
        """Test that missing hashes cause a validation error."""
        if global_require_hash:
            options = ["--require-hashes"]
        else:
            options = []

        if local_hash:
            req_1 = mock_requirement("foo", requirement_kind, hashes=["sha256:abcdef"])
        else:
            req_1 = mock_requirement("foo", requirement_kind)

        req_2 = mock_requirement("bar", requirement_kind)
        req_file = mock_requirements_file(requirements=[req_1, req_2], options=options)

        with pytest.raises(MissingChecksum):
            pip._download_dependencies(RootedPath("/output"), req_file)

    @pytest.mark.parametrize(
        "requirement_kind, hash_in_url",
        [("pypi", False), ("vcs", False), ("url", True), ("url", False)],
    )
    def test_malformed_hash(self, requirement_kind: str, hash_in_url: bool) -> None:
        """Test that invalid hash specifiers cause a validation error."""
        if hash_in_url:
            hashes = []
            qualifiers = {"cachito_hash": "malformed"}
        else:
            hashes = ["malformed"]
            qualifiers = {}

        req = mock_requirement("foo", requirement_kind, hashes=hashes, qualifiers=qualifiers)
        req_file = mock_requirements_file(requirements=[req])

        with pytest.raises(InvalidChecksum):
            pip._download_dependencies(RootedPath("/output"), req_file)

    @pytest.mark.parametrize(
        "binary_filters", (PipBinaryFilters.with_allow_binary_behavior(), None)
    )
    @pytest.mark.parametrize(
        "index_url", [None, pypi_simple.PYPI_SIMPLE_ENDPOINT, CUSTOM_PYPI_ENDPOINT]
    )
    @pytest.mark.parametrize("missing_req_file_checksum", [True, False])
    @mock.patch("hermeto.core.package_managers.pip.main.process_package_distributions")
    @mock.patch("hermeto.core.package_managers.pip.main.must_match_any_checksum")
    @mock.patch.object(Path, "unlink")
    @mock.patch("hermeto.core.package_managers.pip.main.async_download_files")
    @mock.patch("hermeto.core.package_managers.pip.main._check_metadata_in_sdist")
    def test_download_dependencies_pypi(
        self,
        mock_check_metadata_in_sdist: mock.Mock,
        mock_async_download_files: mock.Mock,
        mock_unlink: mock.Mock,
        mock_must_match_any_checksum: mock.Mock,
        mock_process_package_distributions: mock.Mock,
        missing_req_file_checksum: bool,
        index_url: str | None,
        binary_filters: PipBinaryFilters | None,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """
        Test dependency downloading.

        Mock the helper functions used for downloading here, test them properly elsewhere.
        """
        # <setup>
        req = mock_requirement(
            "foo", "pypi", download_line="foo==1.0", version_specs=[("==", "1.0")]
        )
        # match sdist hash, match wheel0 hash, mismatch wheel1 hash, no hash
        # for wheel2
        req.hashes = ["sha256:abcdef", "sha256:defabc", "sha256:feebaa"]

        pypi_checksum_sdist = ChecksumInfo("sha256", "abcdef")
        pypi_checksum_wheels = [
            ChecksumInfo("sha256", "defabc"),
            ChecksumInfo("sha256", "fedbac"),
            ChecksumInfo("sha256", "cbafed"),
        ]
        req_file_checksum_sdist: ChecksumInfo = pypi_checksum_sdist
        # This isn't being auto-created as expected, due to mocking
        # wheel0 hash, mismatch wheel1 hash, no hash for wheel2
        req_file_checksums_wheels = {
            pypi_checksum_wheels[0],
            pypi_checksum_wheels[1],
        }

        options = []
        if index_url:
            options.append("--index-url")
            options.append(index_url)

        req_file = mock_requirements_file(
            requirements=[req],
            options=options,
        )

        expect_index_url = index_url or pypi_simple.PYPI_SIMPLE_ENDPOINT

        pip_deps = rooted_tmp_path.join_within_root("deps", "pip")

        sdist_download = pip_deps.join_within_root("foo-1.0.tar.gz").path

        sdist_DPI = mock_distribution_package_info(
            "foo",
            path=sdist_download,
            index_url=expect_index_url,
            pypi_checksum={pypi_checksum_sdist},
            req_file_checksums=set() if missing_req_file_checksum else {req_file_checksum_sdist},
        )
        sdist_d_i = PyPIArtifact(
            package="foo",
            path=sdist_download,
            requirement_file=str(req_file.file_path.subpath_from_root),
            missing_req_file_checksum=missing_req_file_checksum,
            build_dependency=False,
            index_url=expect_index_url,
            package_type="sdist",
            _version="1.0",
        )
        verify_sdist_checksum_call = mock.call(sdist_download, {pypi_checksum_sdist})
        expected_downloads = [sdist_d_i]

        wheels_DPI: list[pip.DistributionPackageInfo] = []
        if binary_filters is not None:
            wheel_0_download = pip_deps.join_within_root("foo-1.0-cp35-many-linux.whl").path
            wheel_1_download = pip_deps.join_within_root("foo-1.0-cp25-win32.whl").path
            wheel_2_download = pip_deps.join_within_root("foo-1.0-any.whl").path
            wheel_downloads: list[PyPIArtifact] = []

            for wheel_path, pypi_checksum in zip(
                [wheel_0_download, wheel_1_download, wheel_2_download],
                pypi_checksum_wheels,
            ):
                dpi = mock_distribution_package_info(
                    "foo",
                    package_type="wheel",
                    path=wheel_path,
                    index_url=expect_index_url,
                    pypi_checksum={pypi_checksum},
                    req_file_checksums=(
                        set() if missing_req_file_checksum else req_file_checksums_wheels
                    ),
                )
                wheels_DPI.append(dpi)
                wheel_downloads.append(
                    PyPIArtifact(
                        package="foo",
                        path=wheel_path,
                        requirement_file=str(req_file.file_path.subpath_from_root),
                        missing_req_file_checksum=missing_req_file_checksum,
                        build_dependency=False,
                        index_url=expect_index_url,
                        package_type="wheel",
                        _version="1.0",
                    )
                )

            verify_wheel0_checksum_call = mock.call(
                wheel_0_download, {ChecksumInfo("sha256", "defabc")}
            )
            verify_wheel1_checksum_call = mock.call(
                wheel_1_download, {ChecksumInfo("sha256", "fedbac")}
            )
            verify_wheel2_checksum_call = mock.call(
                wheel_2_download, {ChecksumInfo("sha256", "cbafed")}
            )
            expected_downloads.extend(wheel_downloads)

        mock_process_package_distributions.return_value = [sdist_DPI] + wheels_DPI

        if binary_filters is not None:
            mock_must_match_any_checksum.side_effect = [
                None,  # sdist_download
                None,  # wheel_0_download - checksums OK
                PackageRejected("", solution=None),  # wheel_1_download - checksums NOK
                PackageRejected("", solution=None),  # wheel_2_download - no checksums to verify
            ]
        else:
            mock_must_match_any_checksum.side_effect = [
                None,  # sdist_download
            ]
        # </setup>

        # <call>
        found_downloads = pip._download_dependencies(rooted_tmp_path, req_file, binary_filters)
        assert found_downloads == expected_downloads
        assert pip_deps.path.is_dir()
        # </call>

        # <check calls that must always be made>
        mock_check_metadata_in_sdist.assert_called_once_with(sdist_DPI.path)
        mock_process_package_distributions.assert_called_once_with(
            req, pip_deps, binary_filters, expect_index_url
        )
        # </check calls that must always be made>

        verify_checksums_calls = [
            verify_sdist_checksum_call,
        ]

        if binary_filters is not None:
            if missing_req_file_checksum:
                verify_checksums_calls.extend(
                    [
                        verify_wheel0_checksum_call,
                        verify_wheel1_checksum_call,
                        verify_wheel2_checksum_call,
                    ]
                )
            # req file checksums exist
            else:
                verify_checksums_calls.extend(
                    [
                        verify_wheel0_checksum_call,
                        verify_wheel1_checksum_call,
                    ]
                )

        mock_must_match_any_checksum.assert_has_calls(verify_checksums_calls)
        assert mock_must_match_any_checksum.call_count == len(verify_checksums_calls)

        # </check calls to checksum verification method>

        # <check basic logging output>
        assert f"-- Processing requirement line '{req.download_line}'" in caplog.text
        assert (
            f"Successfully processed '{req.download_line}' in path 'deps/pip/foo-1.0.tar.gz'"
        ) in caplog.text
        # </check basic logging output>

        # <check downloaded wheels>
        if binary_filters is not None:
            # wheel 1 does not match any checksums
            assert (
                f"Download '{wheel_1_download.name}' was removed from the output directory"
            ) in caplog.text
        # </check downloaded wheels>

    @pytest.mark.parametrize("checksum_match", [True, False])
    @pytest.mark.parametrize("trusted_hosts", [[], ["example.org"]])
    @mock.patch("hermeto.core.package_managers.pip.main._download_url_package")
    @mock.patch("hermeto.core.package_managers.pip.main.must_match_any_checksum")
    @mock.patch.object(Path, "unlink")
    @mock.patch("hermeto.core.package_managers.pip.main.async_download_files")
    @mock.patch("hermeto.core.package_managers.pip.main.download_binary_file")
    def test_download_dependencies_url(
        self,
        mock_download_binary_file: mock.Mock,
        mock_async_download_files: mock.Mock,
        mock_unlink: mock.Mock,
        mock_must_match_any_checksum: mock.Mock,
        mock_download_url_package: mock.Mock,
        trusted_hosts: list[str],
        checksum_match: bool,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """
        Test dependency downloading.

        Mock the helper functions used for downloading here, test them properly
        elsewhere.

        Note that we're only testing the `cachito_hash` scenario. URL deps can
        also be hashed in 'requirements.txt' like any other pip dep. We really
        should expand this test, at some point, to include testing the `--hash`
        option in 'requirements.txt'.

        URL deps *must always* have a checksum, so we're only testing the case
        where the checksum *doesn't match* (we check for *missing*
        checksums elsewhere for URL deps).
        """
        # <setup>
        plain_url = "https://example.org/bar.tar.gz#cachito_hash=sha256:654321"
        url_req = mock_requirement(
            "bar",
            "url",
            download_line=f"bar @ {plain_url}",
            url=plain_url,
            qualifiers={"cachito_hash": "sha256:654321"},
        )

        options = []
        for host in trusted_hosts:
            options.append("--trusted-host")
            options.append(host)

        req_file = mock_requirements_file(
            requirements=[
                url_req,
            ],
            options=options,
        )

        pip_deps = rooted_tmp_path.join_within_root("deps", "pip")

        url_download = pip_deps.join_within_root(
            "external-bar", "bar-external-sha256-654321.tar.gz"
        ).path

        url_download_info = URLArtifact(
            package="bar",
            path=url_download,
            requirement_file=str(req_file.file_path.subpath_from_root),
            missing_req_file_checksum=False,
            build_dependency=False,
            original_url=plain_url,
            url_with_hash=plain_url,
        )

        mock_download_url_package.return_value = {
            "package": "bar",
            "path": url_download,
            "requirement_file": str(req_file.file_path.subpath_from_root),
            "missing_req_file_checksum": False,
            "package_type": None,
            "original_url": plain_url,
            "url_with_hash": plain_url,
        }

        mock_must_match_any_checksum.side_effect = [
            None if checksum_match else PackageRejected("", solution=None),
        ]
        # </setup>

        # <call>
        found_download = pip._download_dependencies(rooted_tmp_path, req_file, None)
        expected_download = [
            url_download_info,
        ]
        assert found_download == expected_download
        assert pip_deps.path.is_dir()
        # </call>

        # <check calls that must always be made>
        mock_download_url_package.assert_called_once_with(url_req, pip_deps, set(trusted_hosts))
        # </check calls that must always be made>

        # <check calls to checksum verification method>
        if checksum_match:
            # This looks confusing, but as mentioned above, we're currently only
            # testing the `cachito_hash` hash, which is a loophole allowing
            # hashed URLs and unhashed VCS deps to coexist in a
            # 'requirements.txt' file.
            msg = "No hash options used, will not require hashes unless HTTP(S) dependencies"
        else:
            msg = (
                "Download 'bar-external-sha256-654321.tar.gz' was removed from the output directory"
            )
        assert msg in caplog.text
        verify_checksum_call = [mock.call(url_download, [ChecksumInfo("sha256", "654321")])]
        mock_must_match_any_checksum.assert_has_calls(verify_checksum_call)
        assert mock_must_match_any_checksum.call_count == 1
        # </check calls to checksum verification method>

        # <check basic logging output>
        assert f"-- Processing requirement line '{url_req.download_line}'" in caplog.text
        assert (
            f"Successfully processed '{url_req.download_line}' in path 'deps/pip/external-bar/"
            f"bar-external-sha256-654321.tar.gz'"
        ) in caplog.text
        # </check basic logging output>

    @mock.patch("hermeto.core.package_managers.pip.main._download_vcs_package")
    @mock.patch.object(Path, "unlink")
    @mock.patch("hermeto.core.package_managers.pip.main.async_download_files")
    @mock.patch("hermeto.core.scm.clone_as_tarball")
    def test_download_dependencies_vcs(
        self,
        mock_clone_as_tarball: mock.Mock,
        mock_async_download_files: mock.Mock,
        mock_unlink: mock.Mock,
        mock_download_vcs_package: mock.Mock,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """
        Test dependency downloading.

        Mock the helper functions used for downloading here, test them properly elsewhere.

        VCS deps *cannot* be hashed, so we are not checking any checksum-related functions.
        """
        # <setup>
        # "egg" has a very specific meaning in Python packaging world. Let's avoid
        # confusion
        git_url = f"https://github.com/spam/bacon@{GIT_REF}"

        vcs_req = mock_requirement(
            "bacon", "vcs", download_line=f"bacon @ git+{git_url}", url=f"git+{git_url}"
        )

        req_file = mock_requirements_file(
            requirements=[vcs_req],
        )

        pip_deps = rooted_tmp_path.join_within_root("deps", "pip")

        vcs_download = pip_deps.join_within_root(
            "github.com",
            "spam",
            "bacon",
            f"bacon-gitcommit-{GIT_REF}.tar.gz",
        ).path

        vcs_download_info = VCSArtifact(
            package="bacon",
            path=vcs_download,
            requirement_file=str(req_file.file_path.subpath_from_root),
            missing_req_file_checksum=True,
            build_dependency=False,
            url=git_url,
            host="github.com",
            namespace="spam",
            repo="bacon",
            ref=GIT_REF,
        )

        mock_download_vcs_package.return_value = {
            "package": "bacon",
            "path": vcs_download,
            "url": git_url,
            "host": "github.com",
            "namespace": "spam",
            "repo": "bacon",
            "ref": GIT_REF,
        }
        # </setup>

        # <call>
        found_download = pip._download_dependencies(rooted_tmp_path, req_file, None)
        expected_download = [
            vcs_download_info,
        ]
        assert found_download == expected_download
        assert pip_deps.path.is_dir()
        # </call>

        # <check calls that must always be made>
        mock_download_vcs_package.assert_called_once_with(vcs_req, pip_deps)
        # </check calls that must always be made>

        # <check calls to checksum verification method>
        msg = (
            "No hash options used, will not require hashes unless HTTP(S) dependencies are present."
        )
        assert msg in caplog.text
        # </check calls to checksum verification method>

        # <check basic logging output>
        assert f"-- Processing requirement line '{vcs_req.download_line}'" in caplog.text
        assert (
            f"Successfully processed '{vcs_req.download_line}' in path 'deps/pip/github.com/spam/bacon/"
            f"bacon-gitcommit-{GIT_REF}.tar.gz'"
        ) in caplog.text
        # </check basic logging output>

    @mock.patch("hermeto.core.package_managers.pip.main.process_package_distributions")
    @mock.patch("hermeto.core.package_managers.pip.main.async_download_files")
    @mock.patch("hermeto.core.package_managers.pip.main._check_metadata_in_sdist")
    def test_download_from_requirement_files(
        self,
        _check_metadata_in_sdist: mock.Mock,
        async_download_files: mock.Mock,
        _process_package_distributions: mock.Mock,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Test downloading dependencies from a requirement file list."""
        req_file1 = rooted_tmp_path.join_within_root("requirements.txt")
        req_file1.path.write_text("foo==1.0.0")
        req_file2 = rooted_tmp_path.join_within_root("requirements-alt.txt")
        req_file2.path.write_text("bar==0.0.1")

        pip_deps = rooted_tmp_path.join_within_root("deps", "pip")

        pypi_download1 = pip_deps.join_within_root("foo", "foo-1.0.0.tar.gz").path
        pypi_download2 = pip_deps.join_within_root("bar", "bar-0.0.1.tar.gz").path

        pypi_package1 = mock_distribution_package_info("foo", "1.0.0", path=pypi_download1)
        pypi_package2 = mock_distribution_package_info("bar", "0.0.1", path=pypi_download2)

        _process_package_distributions.side_effect = [[pypi_package1], [pypi_package2]]

        downloads = pip._download_from_requirement_files(rooted_tmp_path, [req_file1, req_file2])
        assert downloads == [
            PyPIArtifact(
                package="foo",
                path=pypi_download1,
                requirement_file=str(req_file1.subpath_from_root),
                missing_req_file_checksum=True,
                build_dependency=False,
                index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
                package_type="sdist",
                _version="1.0.0",
            ),
            PyPIArtifact(
                package="bar",
                path=pypi_download2,
                requirement_file=str(req_file2.subpath_from_root),
                missing_req_file_checksum=True,
                build_dependency=False,
                index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
                package_type="sdist",
                _version="0.0.1",
            ),
        ]
        _check_metadata_in_sdist.assert_has_calls(
            [mock.call(pypi_package1.path), mock.call(pypi_package2.path)], any_order=True
        )


@pytest.mark.parametrize("exists", [True, False])
@pytest.mark.parametrize("devel", [True, False])
def test_default_requirement_file_list(
    rooted_tmp_path: RootedPath, exists: bool, devel: bool
) -> None:
    req_file = None
    requirements = pip.DEFAULT_REQUIREMENTS_FILE
    build_requirements = pip.DEFAULT_BUILD_REQUIREMENTS_FILE
    if exists:
        filename = build_requirements if devel else requirements
        req_file = rooted_tmp_path.join_within_root(filename)
        req_file.path.write_text("nothing to see here\n")

    req_files = pip._default_requirement_file_list(rooted_tmp_path, devel)
    expected = [req_file] if req_file else []
    assert req_files == expected


@mock.patch("hermeto.core.package_managers.pip.main._get_pip_metadata")
def test_resolve_pip_no_deps(mock_metadata: mock.Mock, rooted_tmp_path: RootedPath) -> None:
    mock_metadata.return_value = ("foo", "1.0")
    pkg_info = pip._resolve_pip(
        package_path=rooted_tmp_path,
        output_dir=rooted_tmp_path.join_within_root("output"),
    )
    expected = {
        "package": {"name": "foo", "version": "1.0", "type": "pip"},
        "dependencies": [],
        "packages_containing_rust_code": [],
        "requirements": [],
    }
    assert pkg_info == expected


@mock.patch("hermeto.core.package_managers.pip.main._get_pip_metadata")
def test_resolve_pip_invalid_req_file_path(
    mock_metadata: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    mock_metadata.return_value = ("foo", "1.0")
    invalid_path = Path("foo/bar.txt")
    requirement_files = [invalid_path]
    with pytest.raises(LockfileNotFound):
        pip._resolve_pip(
            package_path=rooted_tmp_path,
            output_dir=rooted_tmp_path.join_within_root("output"),
            requirement_files=requirement_files,
        )


@mock.patch("hermeto.core.package_managers.pip.main._get_pip_metadata")
def test_resolve_pip_invalid_bld_req_file_path(
    mock_metadata: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    mock_metadata.return_value = ("foo", "1.0")
    invalid_path = Path("foo/bar.txt")
    build_requirement_files = [invalid_path]
    with pytest.raises(LockfileNotFound):
        pip._resolve_pip(
            package_path=rooted_tmp_path,
            output_dir=rooted_tmp_path.join_within_root("output"),
            build_requirement_files=build_requirement_files,
        )


@pytest.mark.parametrize("custom_requirements", [True, False])
@mock.patch("hermeto.core.package_managers.pip.main._get_pip_metadata")
@mock.patch("hermeto.core.package_managers.pip.main._download_dependencies")
@mock.patch("hermeto.core.package_managers.pip.main.filter_packages_with_rust_code")
def test_resolve_pip(
    mock_filter_cargo_packages: mock.Mock,
    mock_download: mock.Mock,
    mock_metadata: mock.Mock,
    rooted_tmp_path: RootedPath,
    custom_requirements: bool,
) -> None:
    relative_req_file_path = Path("req.txt")
    relative_build_req_file_path = Path("breq.txt")
    req_file = rooted_tmp_path.join_within_root(pip.DEFAULT_REQUIREMENTS_FILE)
    build_req_file = rooted_tmp_path.join_within_root(pip.DEFAULT_BUILD_REQUIREMENTS_FILE)
    if custom_requirements:
        req_file = rooted_tmp_path.join_within_root(relative_req_file_path)
        build_req_file = rooted_tmp_path.join_within_root(relative_build_req_file_path)

    req_file.path.write_text("bar==2.1")
    build_req_file.path.write_text("baz==0.0.5")
    mock_filter_cargo_packages.return_value = []
    mock_metadata.return_value = ("foo", "1.0")
    mock_download.side_effect = [
        [
            PyPIArtifact(
                package="bar",
                path=Path("some/path"),
                requirement_file=str(req_file.subpath_from_root),
                missing_req_file_checksum=False,
                build_dependency=False,
                index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
                package_type="sdist",
                _version="2.1",
            )
        ],
        [
            PyPIArtifact(
                package="baz",
                path=Path("another/path"),
                requirement_file=str(build_req_file.subpath_from_root),
                missing_req_file_checksum=False,
                build_dependency=False,
                index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
                package_type="sdist",
                _version="0.0.5",
            )
        ],
    ]
    if custom_requirements:
        pkg_info = pip._resolve_pip(
            package_path=rooted_tmp_path,
            output_dir=rooted_tmp_path.join_within_root("output"),
            requirement_files=[relative_req_file_path],
            build_requirement_files=[relative_build_req_file_path],
        )
    else:
        pkg_info = pip._resolve_pip(
            package_path=rooted_tmp_path,
            output_dir=rooted_tmp_path.join_within_root("output"),
        )

    expected = {
        "package": {"name": "foo", "version": "1.0", "type": "pip"},
        "dependencies": [
            {
                "name": "bar",
                "version": "2.1",
                "type": "pip",
                "build_dependency": False,
                "kind": "pypi",
                "requirement_file": "req.txt" if custom_requirements else "requirements.txt",
                "missing_req_file_checksum": False,
                "package_type": "sdist",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            },
            {
                "name": "baz",
                "version": "0.0.5",
                "type": "pip",
                "build_dependency": True,
                "kind": "pypi",
                "requirement_file": "breq.txt" if custom_requirements else "requirements-build.txt",
                "missing_req_file_checksum": False,
                "package_type": "sdist",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            },
        ],
        "packages_containing_rust_code": [],
        "requirements": [req_file, build_req_file],
    }
    assert pkg_info == expected


@pytest.mark.parametrize(
    "component_kind, url",
    (
        ["vcs", f"git+https://github.com/cachito/mypkg.git@{'f' * 40}?egg=mypkg"],
        ["url", "https://files.cachito.rocks/mypkg.tar.gz"],
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
    "sdist_filename",
    [
        "myapp-0.1.tar",
        "myapp-0.1.tar.bz2",
        "myapp-0.1.tar.gz",
        "myapp-0.1.tar.xz",
        "myapp-0.1.zip",
    ],
)
def test_check_metadata_from_sdist(sdist_filename: str, data_dir: Path) -> None:
    sdist_path = data_dir / "archives" / sdist_filename
    pip._check_metadata_in_sdist(sdist_path)


def test_skip_check_on_tar_z(caplog: pytest.LogCaptureFixture) -> None:
    sdist_path = Path("app.tar.Z")
    pip._check_metadata_in_sdist(sdist_path)
    assert f"Skip checking metadata from compressed sdist {sdist_path.name}" in caplog.text


@pytest.mark.parametrize(
    "sdist_filename,expected_error",
    [
        ["myapp-0.1.tar.fake.zip", "a Zip file. Error:"],
        ["myapp-0.1.zip.fake.tar", "a Tar file. Error:"],
        ["myapp-without-pkg-info.tar.gz", "not include metadata"],
    ],
)
def test_metadata_check_fails_from_sdist(
    sdist_filename: Path, expected_error: str, data_dir: Path
) -> None:
    sdist_path = data_dir / "archives" / sdist_filename
    with pytest.raises(PackageRejected, match=expected_error):
        pip._check_metadata_in_sdist(sdist_path)


def test_metadata_check_invalid_argument() -> None:
    with pytest.raises(ValueError, match="Cannot check metadata"):
        pip._check_metadata_in_sdist(Path("myapp-0.2.tar.ZZZ"))


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
                foo==1.0.0
                bar @ https://github.com/org/bar/archive/refs/tags/bar-2.0.0.zip#cachito_hash=sha256:fedcba
                """
            ),
            dedent(
                """\
                foo==1.0.0
                bar @ file://${output_dir}/deps/pip/bar-fedcba.zip#cachito_hash=sha256:fedcba
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
    "packages, n_pip_packages",
    [
        pytest.param(
            [{"type": "pip", "requirements_files": ["requirements.txt"]}],
            1,
            id="single_python_package",
        ),
        pytest.param(
            [
                {"type": "pip", "requirements_files": ["requirements.txt"]},
                {"type": "pip", "path": "foo", "requirements_build_files": []},
            ],
            2,
            id="multiple_python_packages",
        ),
    ],
)
@mock.patch("hermeto.core.scm.GitRepo")
@mock.patch("hermeto.core.models.sbom.spdx_now")
@mock.patch("hermeto.core.package_managers.pip.main._replace_external_requirements")
@mock.patch("hermeto.core.package_managers.pip.main._resolve_pip")
@mock.patch("hermeto.core.package_managers.pip.main.filter_packages_with_rust_code")
def test_fetch_pip_source(
    mock_filter_cargo_packages: mock.Mock,
    mock_resolve_pip: mock.Mock,
    mock_replace_requirements: mock.Mock,
    mock_spdx_now: mock.Mock,
    mock_git_repo: mock.Mock,
    packages: list[PackageInput],
    n_pip_packages: int,
    rooted_tmp_path: RootedPath,
) -> None:
    source_dir = rooted_tmp_path.re_root("source")
    output_dir = rooted_tmp_path.re_root("output")
    source_dir.path.mkdir()
    source_dir.join_within_root("foo").path.mkdir()

    request = Request(source_dir=source_dir, output_dir=output_dir, packages=packages)

    mock_filter_cargo_packages.return_value = []
    resolved_a = {
        "package": {"name": "foo", "version": "1.0", "type": "pip"},
        "dependencies": [
            {
                "name": "bar",
                "version": "https://x.org/bar.zip#cachito_hash=sha256:aaaaaaaaaa",
                "type": "pip",
                "build_dependency": False,
                "kind": "url",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": False,
                "package_type": None,
            },
            {
                "name": "baz",
                "version": "0.0.5",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
                "type": "pip",
                "build_dependency": True,
                "kind": "pypi",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": False,
                "package_type": "wheel",
            },
        ],
        "packages_containing_rust_code": [],
        "requirements": ["/package_a/requirements.txt", "/package_a/requirements-build.txt"],
    }
    resolved_b = {
        "package": {"name": "spam", "version": "2.1", "type": "pip"},
        "dependencies": [
            {
                "name": "ham",
                "version": "3.2",
                "index_url": CUSTOM_PYPI_ENDPOINT,
                "type": "pip",
                "build_dependency": False,
                "kind": "pypi",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": True,
                "package_type": "sdist",
            },
            {
                "name": "eggs",
                "version": "https://x.org/eggs.zip#cachito_hash=sha256:aaaaaaaaaa",
                "type": "pip",
                "build_dependency": False,
                "kind": "url",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": True,
                "package_type": None,
            },
        ],
        "packages_containing_rust_code": [],
        "requirements": ["/package_b/requirements.txt"],
    }

    replaced_file_a = ProjectFile(
        abspath=Path("/package_a/requirements.txt"),
        template="bar @ file://${output_dir}/deps/pip/...",
    )
    replaced_file_b = ProjectFile(
        abspath=Path("/package_b/requirements.txt"),
        template="eggs @ file://${output_dir}/deps/pip/...",
    )
    annotation_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mock_spdx_now.return_value = annotation_timestamp

    mock_resolve_pip.side_effect = [resolved_a, resolved_b]
    mock_replace_requirements.side_effect = [replaced_file_a, None, replaced_file_b]

    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = "https://github.com/my-org/my-repo"
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    output = pip.fetch_pip_source(request)

    expect_components_package_a = [
        Component(
            name="foo",
            version="1.0",
            purl=f"pkg:pypi/foo@1.0?vcs_url=git%2Bhttps://github.com/my-org/my-repo%40{'f' * 40}",
        ),
        Component(
            name="bar",
            purl="pkg:pypi/bar?checksum=sha256:aaaaaaaaaa&download_url=https://x.org/bar.zip",
        ),
        Component(
            name="baz",
            version="0.0.5",
            purl="pkg:pypi/baz@0.0.5",
            properties=[
                Property(name=f"{APP_NAME}:pip:package:binary", value="true"),
                Property(name=f"{APP_NAME}:pip:package:build-dependency", value="true"),
            ],
        ),
    ]

    expect_components_package_b = [
        Component(
            name="spam",
            version="2.1",
            purl=f"pkg:pypi/spam@2.1?vcs_url=git%2Bhttps://github.com/my-org/my-repo%40{'f' * 40}#foo",
        ),
        Component(
            name="ham",
            version="3.2",
            purl=f"pkg:pypi/ham@3.2?repository_url={CUSTOM_PYPI_ENDPOINT}",
            properties=[
                Property(name=f"{APP_NAME}:missing_hash:in_file", value="requirements.txt")
            ],
        ),
        Component(
            name="eggs",
            purl="pkg:pypi/eggs?checksum=sha256:aaaaaaaaaa&download_url=https://x.org/eggs.zip",
            properties=[
                Property(name=f"{APP_NAME}:missing_hash:in_file", value="requirements.txt")
            ],
        ),
    ]

    if n_pip_packages == 1:
        expect_packages = expect_components_package_a
        expect_files = [replaced_file_a]
    elif n_pip_packages == 2:
        expect_packages = expect_components_package_a + expect_components_package_b
        expect_files = [replaced_file_a, replaced_file_b]
    else:
        assert False

    assert output.components == expect_packages
    assert output.build_config.project_files == expect_files
    assert len(output.build_config.environment_variables) == 2
    expected_annotations = []
    if n_pip_packages > 0:
        expected_annotations.append(
            Annotation(
                subjects={component.bom_ref for component in expect_packages},
                annotator={"organization": {"name": "red hat"}},
                timestamp=annotation_timestamp,
                text="hermeto:backend:pip",
            )
        )
    assert output.annotations == expected_annotations

    if n_pip_packages == 1:
        mock_resolve_pip.assert_any_call(
            source_dir, output_dir, [Path("requirements.txt")], None, None
        )
        mock_replace_requirements.assert_any_call("/package_a/requirements.txt")
        mock_replace_requirements.assert_any_call("/package_a/requirements-build.txt")
    if n_pip_packages == 2:
        mock_resolve_pip.assert_any_call(
            source_dir.join_within_root("foo"), output_dir, None, [], None
        )
        mock_replace_requirements.assert_any_call("/package_b/requirements.txt")


@pytest.mark.parametrize(
    "dependency, expected_purl",
    [
        (
            {
                "name": "pypi_package",
                "version": "1.0.0",
                "type": "pip",
                "dev": False,
                "kind": "pypi",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            },
            "pkg:pypi/pypi-package@1.0.0",
        ),
        (
            {
                "name": "mypypi_package",
                "version": "2.0.0",
                "type": "pip",
                "dev": False,
                "kind": "pypi",
                "index_url": CUSTOM_PYPI_ENDPOINT,
            },
            f"pkg:pypi/mypypi-package@2.0.0?repository_url={CUSTOM_PYPI_ENDPOINT}",
        ),
        (
            {
                "name": "git_dependency",
                "version": f"git+https://github.com/my-org/git_dependency@{'a' * 40}",
                "type": "pip",
                "dev": False,
                "kind": "vcs",
            },
            f"pkg:pypi/git-dependency?vcs_url=git%2Bhttps://github.com/my-org/git_dependency%40{'a' * 40}",
        ),
        (
            {
                "name": "Git_dependency",
                "version": f"git+file:///github.com/my-org/git_dependency@{'a' * 40}",
                "type": "pip",
                "dev": False,
                "kind": "vcs",
            },
            f"pkg:pypi/git-dependency?vcs_url=git%2Bfile:///github.com/my-org/git_dependency%40{'a' * 40}",
        ),
        (
            {
                "name": "git_dependency",
                "version": f"git+ssh://git@github.com/my-org/git_dependency@{'a' * 40}",
                "type": "pip",
                "dev": False,
                "kind": "vcs",
            },
            f"pkg:pypi/git-dependency?vcs_url=git%2Bssh://git%40github.com/my-org/git_dependency%40{'a' * 40}",
        ),
        (
            {
                "name": "git_dependency",
                "version": f"git+https://github.com/my-org/git_dependency@{'a' * 40}",
                "type": "pip",
                "dev": False,
                "kind": "vcs",
            },
            f"pkg:pypi/git-dependency?vcs_url=git%2Bhttps://github.com/my-org/git_dependency%40{'a' * 40}",
        ),
        (
            {
                "name": "https_dependency",
                "version": f"https://github.com/my-org/https_dependency/{'a' * 40}/file.tar.gz#egg=https_dependency&cachito_hash=sha256:de526c1",
                "type": "pip",
                "dev": False,
                "kind": "url",
            },
            f"pkg:pypi/https-dependency?checksum=sha256:de526c1&download_url=https://github.com/my-org/https_dependency/{'a' * 40}/file.tar.gz",
        ),
    ],
)
def test_generate_purl_dependencies(dependency: dict[str, Any], expected_purl: str) -> None:
    purl = pip._generate_purl_dependency(dependency)

    assert purl == expected_purl


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
    package = {"name": "foo", "version": "1.0.0", "type": "pip"}

    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = "ssh://git@github.com/my-org/my-repo"
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    purl = pip._generate_purl_main_package(package, rooted_tmp_path.join_within_root(subpath))

    assert purl == expected_purl


@mock.patch("hermeto.core.scm.GitRepo")
@mock.patch("hermeto.core.package_managers.pip.main._replace_external_requirements")
@mock.patch("hermeto.core.package_managers.pip.main._resolve_pip")
@mock.patch("hermeto.core.package_managers.cargo.main.run_cmd")
@mock.patch("hermeto.core.package_managers.cargo.main._verify_lockfile_is_present_or_fail")
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
    source_dir.join_within_root("foo").path.mkdir()

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

    resolved_a = {
        "package": {"name": "foo", "version": "1.0", "type": "pip"},
        "dependencies": [
            {
                "name": "bar",
                "version": "https://x.org/bar.zip#cachito_hash=sha256:aaaaaaaaaa",
                "type": "pip",
                "build_dependency": False,
                "kind": "url",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": False,
                "package_type": None,
            },
            {
                "name": "baz",
                "version": "0.0.5",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
                "type": "pip",
                "build_dependency": True,
                "kind": "pypi",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": False,
                "package_type": "wheel",
            },
        ],
        "packages_containing_rust_code": [CargoPackageInput(type="cargo", path=".")],
        "requirements": ["/package_a/requirements.txt", "/package_a/requirements-build.txt"],
    }
    resolved_b = {
        "package": {"name": "spam", "version": "2.1", "type": "pip"},
        "dependencies": [
            {
                "name": "ham",
                "version": "3.2",
                "index_url": CUSTOM_PYPI_ENDPOINT,
                "type": "pip",
                "build_dependency": False,
                "kind": "pypi",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": True,
                "package_type": "sdist",
            },
            {
                "name": "eggs",
                "version": "https://x.org/eggs.zip#cachito_hash=sha256:aaaaaaaaaa",
                "type": "pip",
                "build_dependency": False,
                "kind": "url",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": True,
                "package_type": None,
            },
        ],
        "packages_containing_rust_code": [CargoPackageInput(type="cargo", path=".")],
        "requirements": ["/package_b/requirements.txt"],
    }

    replaced_file_a = ProjectFile(
        abspath=Path("/package_a/requirements.txt"),
        template="bar @ file://${output_dir}/deps/pip/...",
    )
    replaced_file_b = ProjectFile(
        abspath=Path("/package_b/requirements.txt"),
        template="eggs @ file://${output_dir}/deps/pip/...",
    )

    mock_resolve_pip.side_effect = [resolved_a, resolved_b]
    mock_replace_requirements.side_effect = [replaced_file_a, None, replaced_file_b]

    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = "https://github.com/my-org/my-repo"
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    with pytest.raises(PackageWithCorruptLockfileRejected):
        pip.fetch_pip_source(request)
