# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from hermeto import APP_NAME
from hermeto.core.errors import (
    ChecksumVerificationFailed,
    InvalidLockfileFormat,
    LockfileNotFound,
    PackageManagerError,
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
    AuthConfig,
    BearerAuth,
    LockfileArtifactUrl,
    resolve_env_vars,
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

LOCKFILE_V2_WITH_AUTH = """
metadata:
    version: '2.0'
artifacts:
    - download_url: https://gitlab.example.com/api/v4/projects/123/repository/archive.tar.gz
      filename: archive.tar.gz
      checksum: sha256:abc123def456
      auth:
        bearer:
          header: PRIVATE-TOKEN
          value: "$GITLAB_TOKEN"
    - download_url: https://api.github.com/repos/owner/repo/tarball/v1.0.0
      filename: repo.tar.gz
      checksum: sha256:def456abc789
      auth:
        bearer:
          value: "Bearer $GITHUB_TOKEN"
    - download_url: https://example.com/public-file.zip
      checksum: sha256:aaa111bbb222
"""

LOCKFILE_V2_NO_AUTH = """
metadata:
    version: '2.0'
artifacts:
    - download_url: https://example.com/artifact
      filename: archive.zip
      checksum: md5:3a18656e1cea70504b905836dee14db0
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
          value: "$MY_TOKEN"
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
            id="auth_in_v1_rejected",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.generic.main.asyncio.run")
@mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
def test_resolve_generic_lockfile_invalid(
    mock_download: mock.Mock,
    mock_asyncio_run: mock.Mock,
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
                "auth": None,
            },
            {
                "checksum": "md5:32112bed1914cfe3799600f962750b1d",
                "download_url": "https://example.com/more/complex/path/file.tar.gz?foo=bar#fragment",
                "filename": str(rooted_tmp_path.join_within_root("file.tar.gz")),
                "auth": None,
            },
        ],
    }

    # setup lockfile
    lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
    with open(lockfile_path, "w") as f:
        f.write(LOCKFILE_VALID)

    assert _load_lockfile(lockfile_path.path, rooted_tmp_path).model_dump() == expected_lockfile


# =============================================
# Tests for bearer token authentication support
# =============================================


class TestResolveEnvVars:
    """Tests for resolve_env_vars utility function."""

    def test_single_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOKEN", "secret123")
        assert resolve_env_vars("$MY_TOKEN") == "secret123"

    def test_var_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        assert resolve_env_vars("Bearer $GITHUB_TOKEN") == "Bearer ghp_xxx"

    def test_var_with_nonstandard_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITEA_TOKEN", "tok_yyy")
        assert resolve_env_vars("token $GITEA_TOKEN") == "token tok_yyy"

    def test_multiple_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAR1", "aaa")
        monkeypatch.setenv("VAR2", "bbb")
        assert resolve_env_vars("$VAR1:$VAR2") == "aaa:bbb"

    def test_no_vars(self) -> None:
        assert resolve_env_vars("plain-value") == "plain-value"

    def test_missing_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        with pytest.raises(PackageManagerError, match="NONEXISTENT_VAR"):
            resolve_env_vars("$NONEXISTENT_VAR")

    def test_multiple_missing_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISSING1", raising=False)
        monkeypatch.delenv("MISSING2", raising=False)
        with pytest.raises(PackageManagerError, match="MISSING1.*MISSING2"):
            resolve_env_vars("$MISSING1 $MISSING2")

    def test_empty_string(self) -> None:
        assert resolve_env_vars("") == ""

    def test_curly_brace_single_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOKEN", "secret456")
        assert resolve_env_vars("${MY_TOKEN}") == "secret456"

    def test_curly_brace_with_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USER", "admin")
        assert resolve_env_vars("${USER}_token") == "admin_token"

    def test_mixed_syntax(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAR1", "aaa")
        monkeypatch.setenv("VAR2", "bbb")
        assert resolve_env_vars("$VAR1:${VAR2}") == "aaa:bbb"

    def test_curly_brace_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(PackageManagerError, match="MISSING_VAR"):
            resolve_env_vars("${MISSING_VAR}")


class TestBearerAuthModel:
    """Tests for BearerAuth Pydantic model."""

    def test_defaults(self) -> None:
        auth = BearerAuth(value="$TOKEN")
        assert auth.header == "Authorization"
        assert auth.value == "$TOKEN"

    def test_custom_header(self) -> None:
        auth = BearerAuth(header="PRIVATE-TOKEN", value="$GITLAB_TOKEN")
        assert auth.header == "PRIVATE-TOKEN"

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(Exception):
            BearerAuth(value="$TOKEN", unknown_field="bad")  # type: ignore[call-arg]


class TestAuthConfig:
    """Tests for AuthConfig Pydantic model."""

    def test_valid(self) -> None:
        config = AuthConfig(bearer=BearerAuth(value="$TOKEN"))
        assert config.bearer.value == "$TOKEN"

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(Exception):
            AuthConfig(
                bearer=BearerAuth(value="$TOKEN"),
                unknown="bad",  # type: ignore[call-arg]
            )


class TestResolveAuthHeader:
    """Tests for LockfileArtifactUrl.resolve_auth_header method."""

    def test_no_auth(self, rooted_tmp_path: RootedPath) -> None:
        artifact = LockfileArtifactUrl.model_validate(
            {
                "download_url": "https://example.com/file.zip",
                "checksum": "sha256:abc123",
            },
            context={"output_dir": rooted_tmp_path},
        )
        assert artifact.resolve_auth_header() == {}

    def test_bearer_default_header(
        self, rooted_tmp_path: RootedPath, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        artifact = LockfileArtifactUrl.model_validate(
            {
                "download_url": "https://api.github.com/repos/owner/repo/tarball/v1.0",
                "checksum": "sha256:abc123",
                "auth": {"bearer": {"value": "Bearer $GITHUB_TOKEN"}},
            },
            context={"output_dir": rooted_tmp_path},
        )
        assert artifact.resolve_auth_header() == {"Authorization": "Bearer ghp_test123"}

    def test_bearer_custom_header(
        self, rooted_tmp_path: RootedPath, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "glpat_xxx")
        artifact = LockfileArtifactUrl.model_validate(
            {
                "download_url": "https://gitlab.example.com/api/v4/projects/123/archive.tar.gz",
                "checksum": "sha256:abc123",
                "auth": {"bearer": {"header": "PRIVATE-TOKEN", "value": "$GITLAB_TOKEN"}},
            },
            context={"output_dir": rooted_tmp_path},
        )
        assert artifact.resolve_auth_header() == {"PRIVATE-TOKEN": "glpat_xxx"}

    def test_missing_env_var_raises(self, rooted_tmp_path: RootedPath) -> None:
        artifact = LockfileArtifactUrl.model_validate(
            {
                "download_url": "https://example.com/file.zip",
                "checksum": "sha256:abc123",
                "auth": {"bearer": {"value": "$UNSET_TOKEN"}},
            },
            context={"output_dir": rooted_tmp_path},
        )
        with pytest.raises(PackageManagerError, match="Authentication failed for"):
            artifact.resolve_auth_header()

    def test_missing_env_var_includes_url(self, rooted_tmp_path: RootedPath) -> None:
        artifact = LockfileArtifactUrl.model_validate(
            {
                "download_url": "https://private.example.com/secret.tar.gz",
                "checksum": "sha256:abc123",
                "auth": {"bearer": {"value": "$MISSING_SECRET"}},
            },
            context={"output_dir": rooted_tmp_path},
        )
        with pytest.raises(PackageManagerError, match="private.example.com/secret.tar.gz"):
            artifact.resolve_auth_header()


class TestAuthInLockfileV1Rejected:
    """Test that auth is rejected in v1.0 lockfiles."""

    def test_auth_in_v1_raises(self, rooted_tmp_path: RootedPath) -> None:
        lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
        with open(lockfile_path, "w") as f:
            f.write(LOCKFILE_V1_WITH_AUTH)

        with pytest.raises(InvalidLockfileFormat):
            _load_lockfile(lockfile_path.path, rooted_tmp_path)


class TestLockfileV2WithAuth:
    """Tests for lockfile v2.0 with auth configuration."""

    def test_load_v2_no_auth(self, rooted_tmp_path: RootedPath) -> None:
        lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
        with open(lockfile_path, "w") as f:
            f.write(LOCKFILE_V2_NO_AUTH)

        lockfile = _load_lockfile(lockfile_path.path, rooted_tmp_path)
        assert lockfile.metadata.version == "2.0"
        assert len(lockfile.artifacts) == 1
        artifact = lockfile.artifacts[0]
        assert isinstance(artifact, LockfileArtifactUrl)
        assert artifact.auth is None

    def test_load_v2_with_auth(self, rooted_tmp_path: RootedPath) -> None:
        lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
        with open(lockfile_path, "w") as f:
            f.write(LOCKFILE_V2_WITH_AUTH)

        lockfile = _load_lockfile(lockfile_path.path, rooted_tmp_path)
        assert lockfile.metadata.version == "2.0"
        assert len(lockfile.artifacts) == 3

        # GitLab artifact with custom header
        gitlab_artifact = lockfile.artifacts[0]
        assert isinstance(gitlab_artifact, LockfileArtifactUrl)
        assert gitlab_artifact.auth is not None
        assert gitlab_artifact.auth.bearer.header == "PRIVATE-TOKEN"
        assert gitlab_artifact.auth.bearer.value == "$GITLAB_TOKEN"

        # GitHub artifact with default Authorization header
        github_artifact = lockfile.artifacts[1]
        assert isinstance(github_artifact, LockfileArtifactUrl)
        assert github_artifact.auth is not None
        assert github_artifact.auth.bearer.header == "Authorization"
        assert github_artifact.auth.bearer.value == "Bearer $GITHUB_TOKEN"

        # Public artifact without auth
        public_artifact = lockfile.artifacts[2]
        assert isinstance(public_artifact, LockfileArtifactUrl)
        assert public_artifact.auth is None

    @mock.patch("hermeto.core.package_managers.generic.main.asyncio.run")
    @mock.patch("hermeto.core.package_managers.generic.main.async_download_files")
    @mock.patch("hermeto.core.package_managers.generic.main.must_match_any_checksum")
    def test_resolve_lockfile_passes_auth_headers(
        self,
        mock_checksums: mock.Mock,
        mock_download: mock.Mock,
        mock_asyncio_run: mock.Mock,
        rooted_tmp_path: RootedPath,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "glpat_test")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")

        lockfile_path = rooted_tmp_path.join_within_root(DEFAULT_LOCKFILE_NAME)
        with open(lockfile_path, "w") as f:
            f.write(LOCKFILE_V2_WITH_AUTH)

        _resolve_generic_lockfile(lockfile_path.path, rooted_tmp_path)

        # Verify async_download_files was called with correct headers_by_url
        mock_asyncio_run.assert_called_once()
        # The coroutine was created by async_download_files; verify the mock was called
        mock_download.assert_called_once()
        _, kwargs = mock_download.call_args
        assert "headers_by_url" in kwargs
        headers = kwargs["headers_by_url"]

        # GitLab artifact should have PRIVATE-TOKEN header
        gitlab_url = "https://gitlab.example.com/api/v4/projects/123/repository/archive.tar.gz"
        assert headers[gitlab_url] == {"PRIVATE-TOKEN": "glpat_test"}

        # GitHub artifact should have Authorization header
        github_url = "https://api.github.com/repos/owner/repo/tarball/v1.0.0"
        assert headers[github_url] == {"Authorization": "Bearer ghp_test"}

        # Public artifact should NOT be in headers_by_url
        public_url = "https://example.com/public-file.zip"
        assert public_url not in headers
