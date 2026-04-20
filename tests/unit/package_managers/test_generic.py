# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from pydantic import ValidationError

from hermeto import APP_NAME
from hermeto.core.errors import (
    ChecksumVerificationFailed,
    InvalidLockfileFormat,
    LockfileNotFound,
    PackageRejected,
    PathOutsideRoot,
)
from hermeto.core.models.input import GenericPackageInput
from hermeto.core.models.sbom import Annotation, Component
from hermeto.core.package_managers.generic.main import (
    DEFAULT_DEPS_DIR,
    DEFAULT_LOCKFILE_NAME,
    _load_lockfile,
    _resolve_generic_lockfile,
    _resolve_lockfile_path,
    fetch_generic_source,
)
from hermeto.core.package_managers.generic.models import (
    BasicAuth,
    BearerAuth,
    LockfileArtifactAuth,
)
from hermeto.core.rooted_path import RootedPath

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

LOCKFILE_WITH_BASIC_AUTH = """
metadata:
    version: '2.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
      auth:
        basic:
          username: user
          password: passwd
"""

LOCKFILE_WITH_BEARER_AUTH = """
metadata:
    version: '2.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
      auth:
        bearer:
          value: "Bearer secret123"
"""

LOCKFILE_WITH_CUSTOM_HEADER_BEARER_AUTH = """
metadata:
    version: '2.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
      auth:
        bearer:
          header: X-Custom-Auth
          value: secret123
"""

LOCKFILE_WITH_MIXED_AUTH = """
metadata:
    version: '2.0'
artifacts:
    - download_url: https://example.com/authed
      filename: authed.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
      auth:
        basic:
          username: user
          password: passwd
    - download_url: https://example.com/public
      filename: public.zip
      checksum: md5:32112bed1914cfe3799600f962750b1d
"""

LOCKFILE_V2_VALID = """
metadata:
    version: '2.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
    - download_url: https://example.com/more/complex/path/file.tar.gz?foo=bar#fragment
      checksum: md5:32112bed1914cfe3799600f962750b1d
"""

LOCKFILE_V1_WITH_AUTH = """
metadata:
    version: '1.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
      auth:
        bearer:
          value: my-token
"""


@pytest.mark.parametrize(
    ["model_input", "components"],
    [
        pytest.param(
            GenericPackageInput.model_construct(type="generic"),
            [Component(name="foo", version="1.0.0", purl="pkg:generic/foo@1.0.0")],
            id="single_input_with_components",
        ),
        pytest.param(
            GenericPackageInput.model_construct(type="generic"),
            [],
            id="single_input_without_components",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.generic.main.create_backend_annotation")
@mock.patch("hermeto.core.package_managers.generic.main.RequestOutput.from_obj_list")
@mock.patch("hermeto.core.package_managers.generic.main._resolve_generic_lockfile")
def test_fetch_generic_source(
    mock_resolve_generic_lockfile: mock.Mock,
    mock_from_obj_list: mock.Mock,
    mock_create_annotation: mock.Mock,
    model_input: GenericPackageInput,
    components: list[Component],
) -> None:
    mock_resolve_generic_lockfile.return_value = components
    mock_annotation = Annotation(
        subjects=set(),
        annotator={"organization": {"name": "red hat"}},
        timestamp="2026-01-01T00:00:00Z",
        text="hermeto:backend:generic",
    )
    mock_create_annotation.side_effect = lambda resolved_components, _: (
        mock_annotation if resolved_components else None
    )

    mock_request = mock.Mock()
    mock_request.generic_packages = [model_input]

    fetch_generic_source(mock_request)

    mock_resolve_generic_lockfile.assert_called()
    mock_create_annotation.assert_called_once_with(components, "generic")
    mock_from_obj_list.assert_called_once_with(
        components=components,
        annotations=[mock_annotation] if components else [],
    )


@pytest.mark.parametrize(
    ("pkg_path", "lockfile_value", "expected_result"),
    [
        pytest.param(Path("."), None, "artifacts.lock.yaml", id="default-lockfile"),
        pytest.param(
            Path("pkg"), Path("relative.yaml"), "pkg/relative.yaml", id="relative-lockfile"
        ),
        pytest.param(
            Path("pkg"),
            Path("/absolute/path/to/lockfile.yaml"),
            "/absolute/path/to/lockfile.yaml",
            id="absolute-lockfile",
        ),
    ],
)
def test_resolve_lockfile_path(
    rooted_tmp_path: RootedPath,
    pkg_path: Path,
    lockfile_value: Path | None,
    expected_result: str,
) -> None:
    if Path(expected_result).is_absolute():
        expected_path = Path(expected_result)
    else:
        expected_path = rooted_tmp_path.join_within_root(expected_result).path

    resolved = _resolve_lockfile_path(rooted_tmp_path, pkg_path, lockfile_value)
    assert resolved == Path(expected_path)


def test_resolve_lockfile_path_fail(rooted_tmp_path: RootedPath) -> None:
    with pytest.raises(PackageRejected) as exc_info:
        _resolve_lockfile_path(rooted_tmp_path, Path("pkg"), Path("../outside.yaml"))

    assert "must be inside the package path" in str(exc_info.value)


@mock.patch("hermeto.core.package_managers.generic.main._load_lockfile")
def test_resolve_generic_no_lockfile(mock_load: mock.Mock, rooted_tmp_path: RootedPath) -> None:
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with pytest.raises(LockfileNotFound):
        _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)
    mock_load.assert_not_called()


@pytest.mark.parametrize(
    ["lockfile", "expected_exception"],
    [
        pytest.param("{", InvalidLockfileFormat, id="invalid_yaml"),
        pytest.param(LOCKFILE_WRONG_VERSION, InvalidLockfileFormat, id="wrong_version"),
        pytest.param(LOCKFILE_CHECKSUM_MISSING, InvalidLockfileFormat, id="checksum_missing"),
        pytest.param(
            LOCKFILE_INVALID_FILENAME,
            PathOutsideRoot,
            id="invalid_filename",
        ),
        pytest.param(
            LOCKFILE_FILENAME_OVERLAP,
            InvalidLockfileFormat,
            id="conflicting_filenames",
        ),
        pytest.param(
            LOCKFILE_URL_OVERLAP,
            InvalidLockfileFormat,
            id="conflicting_urls",
        ),
        pytest.param(
            LOCKFILE_WRONG_CHECKSUM,
            ChecksumVerificationFailed,
            id="wrong_checksum",
        ),
        pytest.param(
            LOCKFILE_WRONG_CHECKSUM_FORMAT,
            InvalidLockfileFormat,
            id="wrong_checksum_format",
        ),
        pytest.param(
            LOCKFILE_V1_WITH_AUTH,
            InvalidLockfileFormat,
            id="v1_rejects_auth",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
def test_resolve_generic_lockfile_invalid(
    mock_async_download_files: mock.Mock,
    lockfile: str,
    expected_exception: type[PackageRejected],
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

    with pytest.raises(expected_exception):
        _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)


@pytest.mark.parametrize(
    ["lockfile_content", "expected_components"],
    [
        pytest.param(
            LOCKFILE_VALID,
            [
                {
                    "bom-ref": "pkg:generic/archive.zip?checksum=md5:3a18656e1cea70504b905836dee14db0&download_url=https://example.com/artifact",
                    "externalReferences": [
                        {"type": "distribution", "url": "https://example.com/artifact"}
                    ],
                    "name": "archive.zip",
                    "properties": [{"name": f"{APP_NAME}:found_by", "value": f"{APP_NAME}"}],
                    "purl": "pkg:generic/archive.zip?checksum=md5:3a18656e1cea70504b905836dee14db0&download_url=https://example.com/artifact",
                    "type": "file",
                },
                {
                    "bom-ref": "pkg:generic/file.tar.gz?checksum=md5:32112bed1914cfe3799600f962750b1d&download_url=https://example.com/more/complex/path/file.tar.gz%3Ffoo%3Dbar%23fragment",
                    "externalReferences": [
                        {
                            "type": "distribution",
                            "url": "https://example.com/more/complex/path/file.tar.gz?foo=bar#fragment",
                        }
                    ],
                    "name": "file.tar.gz",
                    "properties": [{"name": f"{APP_NAME}:found_by", "value": f"{APP_NAME}"}],
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
                    "bom-ref": "pkg:maven/org.springframework.boot/spring-boot-starter@3.1.5?checksum=sha256:c3c5e397008ba2d3d0d6e10f7f343b68d2e16c5a3fbe6a6daa7dd4d6a30197a5&repository_url=https://repo.spring.io/release&type=jar",
                    "externalReferences": [
                        {
                            "type": "distribution",
                            "url": "https://repo.spring.io/release/org/springframework/boot/spring-boot-starter/3.1.5/spring-boot-starter-3.1.5.jar",
                        }
                    ],
                    "name": "spring-boot-starter",
                    "properties": [{"name": f"{APP_NAME}:found_by", "value": f"{APP_NAME}"}],
                    "purl": "pkg:maven/org.springframework.boot/spring-boot-starter@3.1.5?checksum=sha256:c3c5e397008ba2d3d0d6e10f7f343b68d2e16c5a3fbe6a6daa7dd4d6a30197a5&repository_url=https://repo.spring.io/release&type=jar",
                    "type": "library",
                    "version": "3.1.5",
                },
                {
                    "bom-ref": "pkg:maven/io.netty/netty-transport-native-epoll@4.1.100.Final?checksum=sha256:c3c5e397008ba2d3d0d6e10f7f343b68d2e16c5a3fbe6a6daa7dd4d6a30197a5&classifier=sources&repository_url=https://repo1.maven.org/maven2&type=jar",
                    "externalReferences": [
                        {
                            "type": "distribution",
                            "url": "https://repo1.maven.org/maven2/io/netty/netty-transport-native-epoll/4.1.100.Final/netty-transport-native-epoll-4.1.100.Final-sources.jar",
                        }
                    ],
                    "name": "netty-transport-native-epoll",
                    "properties": [{"name": f"{APP_NAME}:found_by", "value": f"{APP_NAME}"}],
                    "purl": "pkg:maven/io.netty/netty-transport-native-epoll@4.1.100.Final?checksum=sha256:c3c5e397008ba2d3d0d6e10f7f343b68d2e16c5a3fbe6a6daa7dd4d6a30197a5&classifier=sources&repository_url=https://repo1.maven.org/maven2&type=jar",
                    "type": "library",
                    "version": "4.1.100.Final",
                },
            ],
            id="valid_lockfile_maven",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
@mock.patch("hermeto.core.package_managers.generic.main.must_match_any_checksum")
def test_resolve_generic_lockfile_valid(
    mock_checksums: mock.Mock,
    mock_async_download_files: mock.Mock,
    lockfile_content: str,
    expected_components: list[dict[str, Any]],
    rooted_tmp_path: RootedPath,
) -> None:
    # setup lockfile
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(lockfile_content)

    assert [
        c.model_dump(by_alias=True, exclude_none=True)
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


def test_load_generic_lockfile_v2_valid(rooted_tmp_path: RootedPath) -> None:
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(LOCKFILE_WITH_BEARER_AUTH)

    lockfile = _load_lockfile(lockfile_path.path, rooted_tmp_path)
    assert lockfile.metadata.version == "2.0"
    assert lockfile.artifacts[0].auth is not None
    assert lockfile.artifacts[0].auth.bearer is not None
    assert lockfile.artifacts[0].auth.bearer.value == "Bearer secret123"


class TestEnvVarExpansion:
    """Tests for environment variable expansion in auth fields."""

    @pytest.mark.parametrize(
        ["env_vars", "value", "expected"],
        [
            pytest.param(
                {"MY_TOKEN": "secret123"},
                "Bearer $MY_TOKEN",
                "Bearer secret123",
                id="dollar_var_syntax",
            ),
            pytest.param(
                {"MY_TOKEN": "secret123"},
                "Bearer ${MY_TOKEN}",
                "Bearer secret123",
                id="braced_var_syntax",
            ),
            pytest.param(
                {"TOKEN": "should_not_appear"},
                "Bearer \\$TOKEN",
                "Bearer $TOKEN",
                id="escaped_dollar_sign",
            ),
            pytest.param(
                {},
                "no-expansion",
                "no-expansion",
                id="literal_no_expansion",
            ),
            pytest.param(
                {"A": "first", "B": "second"},
                "$A and ${B}",
                "first and second",
                id="multiple_vars_mixed_syntax",
            ),
            pytest.param(
                {"TOKEN": "secret123"},
                "Token:$TOKEN",
                "Token:secret123",
                id="no_space_before_var",
            ),
            pytest.param(
                {"EMPTY_VAR": ""},
                "Bearer $EMPTY_VAR",
                "Bearer ",
                id="empty_env_var",
            ),
        ],
    )
    def test_expand_env_vars(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_vars: dict[str, str],
        value: str,
        expected: str,
    ) -> None:
        for name, val in env_vars.items():
            monkeypatch.setenv(name, val)
        auth = BearerAuth(value=value)
        assert auth.value == expected

    @pytest.mark.parametrize(
        ["value", "missing_var"],
        [
            pytest.param("Bearer $NONEXISTENT_VAR", "NONEXISTENT_VAR", id="dollar_syntax"),
            pytest.param("Bearer ${NONEXISTENT_VAR}", "NONEXISTENT_VAR", id="braced_syntax"),
        ],
    )
    def test_unset_var_raises(self, value: str, missing_var: str) -> None:
        with pytest.raises(ValidationError, match=f"{missing_var}.*is not set"):
            BearerAuth(value=value)

    def test_expansion_in_all_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USER", "admin")
        monkeypatch.setenv("PASS", "s3cret")
        auth = BasicAuth(username="$USER", password="$PASS")  # noqa: S106
        assert auth.username == "admin"
        assert auth.password == "s3cret"  # noqa: S105


class TestLockfileArtifactAuth:
    """Tests for LockfileArtifactAuth model."""

    @pytest.mark.parametrize(
        ["kwargs", "has_basic", "has_bearer"],
        [
            pytest.param(
                {"basic": BasicAuth(username="user", password="passwd")},  # noqa: S106
                True,
                False,
                id="basic_only",
            ),
            pytest.param(
                {"bearer": BearerAuth(value="secret123")},
                False,
                True,
                id="bearer_only",
            ),
        ],
    )
    def test_valid_combinations(
        self, kwargs: dict[str, Any], has_basic: bool, has_bearer: bool
    ) -> None:
        auth = LockfileArtifactAuth(**kwargs)
        assert (auth.basic is not None) == has_basic
        assert (auth.bearer is not None) == has_bearer

    def test_both_raises(self) -> None:
        with pytest.raises(ValidationError, match="Exactly one of the auth types must be set"):
            LockfileArtifactAuth(
                basic=BasicAuth(username="user", password="passwd"),  # noqa: S106
                bearer=BearerAuth(value="secret123"),
            )

    @pytest.mark.parametrize(
        ["kwargs", "expected"],
        [
            pytest.param(
                {"basic": BasicAuth(username="user", password="passwd")},  # noqa: S106
                {"Authorization": "Basic dXNlcjpwYXNzd2Q="},
                id="basic",
            ),
            pytest.param(
                {"basic": BasicAuth(username="user@domain", password="p@ss:word!")},  # noqa: S106
                {"Authorization": "Basic dXNlckBkb21haW46cEBzczp3b3JkIQ=="},
                id="basic_special_chars",
            ),
            pytest.param(
                {"bearer": BearerAuth(value="Bearer secret123")},
                {"Authorization": "Bearer secret123"},
                id="bearer_default_header",
            ),
            pytest.param(
                {"bearer": BearerAuth(header="X-Token", value="secret123")},
                {"X-Token": "secret123"},
                id="bearer_custom_header",
            ),
        ],
    )
    def test_get_headers(self, kwargs: dict[str, Any], expected: dict[str, str]) -> None:
        auth = LockfileArtifactAuth(**kwargs)
        assert auth.get_headers() == expected

    def test_raise_error_when_no_auth_type_set(self) -> None:
        with pytest.raises(ValidationError, match="Exactly one of the auth types must be set"):
            LockfileArtifactAuth()


@pytest.mark.parametrize(
    ["lockfile_content", "expected_url", "expected_headers"],
    [
        pytest.param(
            LOCKFILE_WITH_BASIC_AUTH,
            "https://example.com/artifact",
            {"Authorization": "Basic dXNlcjpwYXNzd2Q="},
            id="basic_auth",
        ),
        pytest.param(
            LOCKFILE_WITH_BEARER_AUTH,
            "https://example.com/artifact",
            {"Authorization": "Bearer secret123"},
            id="bearer_auth_default_header",
        ),
        pytest.param(
            LOCKFILE_WITH_CUSTOM_HEADER_BEARER_AUTH,
            "https://example.com/artifact",
            {"X-Custom-Auth": "secret123"},
            id="bearer_auth_custom_header",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.generic.main.asyncio.run")
@mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
@mock.patch("hermeto.core.package_managers.generic.main.must_match_any_checksum")
def test_resolve_generic_lockfile_with_auth(
    mock_checksums: mock.Mock,
    mock_download: mock.Mock,
    mock_asyncio_run: mock.Mock,
    lockfile_content: str,
    expected_url: str,
    expected_headers: dict[str, str],
    rooted_tmp_path: RootedPath,
) -> None:
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(lockfile_content)

    _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)

    assert mock_download.call_args.kwargs["headers"] == {expected_url: expected_headers}


@mock.patch("hermeto.core.package_managers.generic.main.asyncio.run")
@mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
@mock.patch("hermeto.core.package_managers.generic.main.must_match_any_checksum")
def test_resolve_generic_lockfile_mixed_auth(
    mock_checksums: mock.Mock,
    mock_download: mock.Mock,
    mock_asyncio_run: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(LOCKFILE_WITH_MIXED_AUTH)

    _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)

    headers = mock_download.call_args.kwargs["headers"]
    # Only the authed artifact should have headers
    assert "https://example.com/authed" in headers
    assert "https://example.com/public" not in headers


@mock.patch("hermeto.core.package_managers.generic.main.asyncio.run")
@mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
@mock.patch("hermeto.core.package_managers.generic.main.must_match_any_checksum")
def test_resolve_generic_lockfile_no_auth(
    mock_checksums: mock.Mock,
    mock_download: mock.Mock,
    mock_asyncio_run: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(LOCKFILE_VALID)

    _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)

    assert mock_download.call_args.kwargs["headers"] is None


@mock.patch("hermeto.core.package_managers.generic.main.asyncio.run")
@mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
@mock.patch("hermeto.core.package_managers.generic.main.must_match_any_checksum")
def test_resolve_generic_lockfile_v2_no_auth(
    mock_checksums: mock.Mock,
    mock_download: mock.Mock,
    mock_asyncio_run: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(LOCKFILE_V2_VALID)

    _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)

    assert mock_download.call_args.kwargs["headers"] is None
