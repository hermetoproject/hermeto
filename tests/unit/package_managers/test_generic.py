from pathlib import Path
from typing import Any, Type
from unittest import mock

import pytest

from hermeto.core.errors import BaseError, PackageRejected
from hermeto.core.models.input import GenericPackageInput
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.generic.main import (
    DEFAULT_DEPS_DIR,
    DEFAULT_LOCKFILE_NAME,
    _load_lockfile,
    _resolve_generic_lockfile,
    fetch_generic_source,
)
from hermeto.core.rooted_path import PathOutsideRoot, RootedPath

LOCKFILE_WRONG_VERSION = """
metadata:
    version: '0.42'
artifacts:
    - download_url: https://example.com/artifact
      checksum: md5:3a18656e1cea70504b905836dee14db0
"""

LOCKFILE_CHECKSUM_MISSING = """
metadata:
    version: '1.0'
artifacts:
    - download_url: https://example.com/artifact
"""

LOCKFILE_WRONG_CHECKSUM_FORMAT = """
metadata:
    version: '1.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: 32112bed1914cfe3799600f962750b1d
"""

LOCKFILE_VALID = """
metadata:
    version: '1.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
    - download_url: https://example.com/more/complex/path/file.tar.gz?foo=bar#fragment
      checksum: md5:32112bed1914cfe3799600f962750b1d
"""

LOCKFILE_VALID_MAVEN = """
metadata:
    version: '1.0'
artifacts:
    - type: "maven"
      attributes:
        repository_url: "https://repo.spring.io/release"
        group_id: "org.springframework.boot"
        artifact_id: "spring-boot-starter"
        version: "3.1.5"
        type: "jar"
        classifier: ""
      checksum: "sha256:c3c5e397008ba2d3d0d6e10f7f343b68d2e16c5a3fbe6a6daa7dd4d6a30197a5"
    - type: "maven"
      attributes:
        repository_url: "https://repo1.maven.org/maven2"
        group_id: "io.netty"
        artifact_id: "netty-transport-native-epoll"
        version: "4.1.100.Final"
        type: "jar"
        classifier: "sources"
      checksum: "sha256:c3c5e397008ba2d3d0d6e10f7f343b68d2e16c5a3fbe6a6daa7dd4d6a30197a5"
"""

LOCKFILE_INVALID_FILENAME = """
metadata:
    version: '1.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: ./../../../archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
"""

LOCKFILE_FILENAME_OVERLAP = """
metadata:
    version: '1.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
    - download_url: https://example.com/artifact2
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
"""

LOCKFILE_URL_OVERLAP = """
metadata:
    version: '1.0'
artifacts:
    - download_url: https://example.com/artifact
      checksum: md5:3a18656e1cea70504b905836dee14db0
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
"""

LOCKFILE_WRONG_CHECKSUM = """
metadata:
    version: '1.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:32112bed1914cfe3799600f962750b1d
"""


@pytest.mark.parametrize(
    ["model_input", "components"],
    [
        pytest.param(GenericPackageInput.model_construct(type="generic"), [], id="single_input"),
    ],
)
@mock.patch("hermeto.core.package_managers.generic.main.RequestOutput.from_obj_list")
@mock.patch("hermeto.core.package_managers.generic.main._resolve_generic_lockfile")
def test_fetch_generic_source(
    mock_resolve_generic_lockfile: mock.Mock,
    mock_from_obj_list: mock.Mock,
    model_input: GenericPackageInput,
    components: list[Component],
) -> None:

    mock_resolve_generic_lockfile.return_value = components

    mock_request = mock.Mock()
    mock_request.generic_packages = [model_input]

    fetch_generic_source(mock_request)

    mock_resolve_generic_lockfile.assert_called()


def test_fetch_generic_source_relative_lockfile_path() -> None:

    model_input = GenericPackageInput.model_construct(
        type="generic", lockfile=Path("relative.yaml")
    )

    mock_request = mock.Mock()
    mock_request.generic_packages = [model_input]

    with pytest.raises(PackageRejected) as exc_info:
        fetch_generic_source(mock_request)

    assert (
        "Supplied generic lockfile path 'relative.yaml' is not absolute, refusing to continue."
        in str(exc_info.value)
    )


@mock.patch("hermeto.core.package_managers.generic.main._load_lockfile")
def test_resolve_generic_no_lockfile(mock_load: mock.Mock, rooted_tmp_path: RootedPath) -> None:
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with pytest.raises(PackageRejected) as exc_info:
        _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)
    assert (
        f"Cachi2 generic lockfile '{lockfile_path}' does not exist, refusing to continue."
        in str(exc_info.value)
    )
    mock_load.assert_not_called()


@pytest.mark.parametrize(
    ["lockfile", "expected_exception", "expected_err"],
    [
        pytest.param("{", PackageRejected, "yaml format is not correct", id="invalid_yaml"),
        pytest.param(
            LOCKFILE_WRONG_VERSION, PackageRejected, "Input should be '1.0'", id="wrong_version"
        ),
        pytest.param(
            LOCKFILE_CHECKSUM_MISSING, PackageRejected, "Field required", id="checksum_missing"
        ),
        pytest.param(
            LOCKFILE_INVALID_FILENAME,
            PathOutsideRoot,
            "",
            id="invalid_filename",
        ),
        pytest.param(
            LOCKFILE_FILENAME_OVERLAP,
            PackageRejected,
            "Duplicate filenames",
            id="conflicting_filenames",
        ),
        pytest.param(
            LOCKFILE_URL_OVERLAP,
            PackageRejected,
            "Duplicate download_urls",
            id="conflicting_urls",
        ),
        pytest.param(
            LOCKFILE_WRONG_CHECKSUM,
            PackageRejected,
            "Failed to verify archive.zip against any of the provided checksums.",
            id="wrong_checksum",
        ),
        pytest.param(
            LOCKFILE_WRONG_CHECKSUM_FORMAT,
            PackageRejected,
            "Checksum must be in the format 'algorithm:hash'",
            id="wrong_checksum",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.generic.main.asyncio.run")
@mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
def test_resolve_generic_lockfile_invalid(
    mock_download: mock.Mock,
    mock_asyncio_run: mock.Mock,
    lockfile: str,
    expected_exception: Type[BaseError],
    expected_err: str,
    rooted_tmp_path: RootedPath,
) -> None:
    # setup lockfile
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(lockfile)

    # setup testing downloaded dependency
    deps_path = rooted_tmp_path.join_within_root(DEFAULT_DEPS_DIR)
    Path.mkdir(deps_path.path, parents=True, exist_ok=True)
    with open(deps_path.join_within_root("archive.zip"), "w") as f:
        f.write("Testfile")

    with pytest.raises(expected_exception) as exc_info:
        _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)

    assert expected_err in str(exc_info.value)


@pytest.mark.parametrize(
    ["lockfile_content", "expected_components"],
    [
        pytest.param(
            LOCKFILE_VALID,
            [
                {
                    "external_references": [
                        {"type": "distribution", "url": "https://example.com/artifact"}
                    ],
                    "name": "archive.zip",
                    "properties": [{"name": "cachi2:found_by", "value": "cachi2"}],
                    "purl": "pkg:generic/archive.zip?checksum=md5:3a18656e1cea70504b905836dee14db0&download_url=https://example.com/artifact",
                    "type": "file",
                },
                {
                    "external_references": [
                        {
                            "type": "distribution",
                            "url": "https://example.com/more/complex/path/file.tar.gz?foo=bar#fragment",
                        }
                    ],
                    "name": "file.tar.gz",
                    "properties": [{"name": "cachi2:found_by", "value": "cachi2"}],
                    "purl": "pkg:generic/file.tar.gz?checksum=md5:32112bed1914cfe3799600f962750b1d&download_url=https://example.com/more/complex/path/file.tar.gz%3Ffoo%3Dbar%23fragment",
                    "type": "file",
                },
            ],
            id="valid_lockfile",
        ),
        pytest.param(
            LOCKFILE_VALID_MAVEN,
            [
                {
                    "external_references": [
                        {
                            "type": "distribution",
                            "url": "https://repo.spring.io/release/org/springframework/boot/spring-boot-starter/3.1.5/spring-boot-starter-3.1.5.jar",
                        }
                    ],
                    "name": "spring-boot-starter",
                    "properties": [{"name": "cachi2:found_by", "value": "cachi2"}],
                    "purl": "pkg:maven/org.springframework.boot/spring-boot-starter@3.1.5?checksum=sha256:c3c5e397008ba2d3d0d6e10f7f343b68d2e16c5a3fbe6a6daa7dd4d6a30197a5&repository_url=https://repo.spring.io/release&type=jar",
                    "type": "library",
                    "version": "3.1.5",
                },
                {
                    "external_references": [
                        {
                            "type": "distribution",
                            "url": "https://repo1.maven.org/maven2/io/netty/netty-transport-native-epoll/4.1.100.Final/netty-transport-native-epoll-4.1.100.Final-sources.jar",
                        }
                    ],
                    "name": "netty-transport-native-epoll",
                    "properties": [{"name": "cachi2:found_by", "value": "cachi2"}],
                    "purl": "pkg:maven/io.netty/netty-transport-native-epoll@4.1.100.Final?checksum=sha256:c3c5e397008ba2d3d0d6e10f7f343b68d2e16c5a3fbe6a6daa7dd4d6a30197a5&classifier=sources&repository_url=https://repo1.maven.org/maven2&type=jar",
                    "type": "library",
                    "version": "4.1.100.Final",
                },
            ],
            id="valid_lockfile_maven",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.generic.main.asyncio.run")
@mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
@mock.patch("hermeto.core.package_managers.generic.main.must_match_any_checksum")
def test_resolve_generic_lockfile_valid(
    mock_checksums: mock.Mock,
    mock_download: mock.Mock,
    mock_asyncio_run: mock.Mock,
    lockfile_content: str,
    expected_components: list[dict[str, Any]],
    rooted_tmp_path: RootedPath,
) -> None:
    # setup lockfile
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(lockfile_content)

    assert [
        c.model_dump(exclude_none=True)
        for c in _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)
    ] == expected_components
    mock_checksums.assert_called()


def test_load_generic_lockfile_valid(rooted_tmp_path: RootedPath) -> None:
    expected_lockfile = {
        "metadata": {"version": "1.0"},
        "artifacts": [
            {
                "download_url": "https://example.com/artifact",
                "filename": str(rooted_tmp_path.join_within_root("archive.zip")),
                "checksum": "md5:3a18656e1cea70504b905836dee14db0",
            },
            {
                "checksum": "md5:32112bed1914cfe3799600f962750b1d",
                "download_url": "https://example.com/more/complex/path/file.tar.gz?foo=bar#fragment",
                "filename": str(rooted_tmp_path.join_within_root("file.tar.gz")),
            },
        ],
    }

    # setup lockfile
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(LOCKFILE_VALID)

    assert _load_lockfile(lockfile_path.path, rooted_tmp_path).model_dump() == expected_lockfile
