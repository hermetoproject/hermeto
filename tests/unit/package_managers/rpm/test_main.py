# SPDX-License-Identifier: GPL-3.0-only
import ssl
from configparser import ConfigParser
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml

from hermeto import APP_NAME
from hermeto.core.errors import (
    ChecksumVerificationFailed,
    InvalidLockfileFormat,
    LockfileNotFound,
)
from hermeto.core.models.input import SSLOptions
from hermeto.core.models.sbom import Component, Property
from hermeto.core.package_managers.rpm.main import (
    DEFAULT_PACKAGE_DIR,
    _generate_repofiles,
    _generate_sbom_components,
    _get_ssl_context,
    _Repofile,
    _resolve_rpm_project,
    _verify_downloaded,
)
from hermeto.core.package_managers.rpm.redhat import RedhatRpmsLock
from hermeto.core.rooted_path import RootedPath


def test_resolve_rpm_project_no_lockfile(rooted_tmp_path: RootedPath) -> None:
    with pytest.raises(LockfileNotFound):
        # MagicMock is to pass Path/str to LockfileNotFound and avoid TypeError
        mock_source_dir = mock.MagicMock()
        mock_source_dir.join_within_root.return_value.path.exists.return_value = False
        _resolve_rpm_project(mock_source_dir, mock.Mock())


@pytest.mark.parametrize(
    "yaml_content",
    [
        pytest.param(
            "lockfileVendor: redhat\nlockfileVersion: 1\narches\n",
            id="missing_colon",
        ),
        pytest.param(
            "lockfileVendor: redhat lockfileVersion: 1\narches:\n",
            id="missing_newline",
        ),
    ],
)
def test_resolve_rpm_project_rejects_invalid_yaml_format(
    rooted_tmp_path: RootedPath, yaml_content: str
) -> None:
    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        f.write(yaml_content)
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)


@pytest.mark.parametrize(
    "lockfile_data",
    [
        pytest.param(
            {"lockfileVendor": "unknown", "lockfileVersion": 1, "arches": []},
            id="unknown_vendor",
        ),
        pytest.param(
            {"lockfileVendor": "redhat", "lockfileVersion": 2, "arches": []},
            id="unsupported_version",
        ),
        pytest.param(
            {"lockfileVendor": "redhat", "lockfileVersion": "zz", "arches": []},
            id="non_int_version",
        ),
        pytest.param(
            {"vendor": "redhat", "lockfileVersion": 1, "arches": []},
            id="missing_lockfileVendor_key",
        ),
        pytest.param(
            {"lockfileVendor": "redhat", "lockfileVersion": 1, "arches": "everything"},
            id="arches_not_list",
        ),
        pytest.param(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": "zz",
                "arches": [
                    {"arch": "x86_64", "packages": [{"address": "SOME_ADDRESS", "size": 1111}]},
                ],
            },
            id="invalid_package_schema",
        ),
    ],
)
def test_resolve_rpm_project_invalid_lockfile_format(
    rooted_tmp_path: RootedPath, lockfile_data: dict
) -> None:
    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(lockfile_data, f)
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)


@pytest.mark.parametrize(
    "lockfile_data, expected_components",
    [
        pytest.param(
            {"lockfileVendor": "redhat", "lockfileVersion": 1, "arches": [{"arch": "x86_64"}]},
            0,
            id="no_packages_or_source",
        ),
        pytest.param(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": 1,
                "arches": [{"arch": "aarch64", "packages": []}],
            },
            0,
            id="empty_packages",
        ),
        pytest.param(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": 1,
                "arches": [
                    {"arch": "i686", "packages": [], "source": []},
                ],
            },
            0,
            id="all_empty",
        ),
        pytest.param(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": 1,
                "arches": [
                    {"arch": "i686", "packages": [], "source": []},
                    {
                        "arch": "x86_64",
                        "packages": [{"url": "https://example.com/foo-1.0-1.fc39.x86_64.rpm"}],
                    },
                ],
            },
            1,
            id="mixed_empty_and_valid",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.rpm.main.Package.from_filepath")
@mock.patch("hermeto.core.package_managers.rpm.main.async_download_files")
def test_resolve_rpm_project_accepts_empty_arch(
    mock_async_download_files: mock.Mock,
    mock_from_filepath: mock.Mock,
    rooted_tmp_path: RootedPath,
    lockfile_data: dict,
    expected_components: int,
) -> None:
    mock_from_filepath.return_value = mock.Mock(
        to_component=mock.Mock(
            return_value=Component(name="foo", version="1.0", purl="pkg:rpm/foo@1.0"),
        ),
    )
    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(lockfile_data, f)
    components = _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)
    assert len(components) == expected_components
    assert mock_async_download_files.call_count == len(lockfile_data["arches"])


@pytest.mark.parametrize(
    "options, expected_repofile",
    [
        pytest.param(
            None,
            """
            [repo1]
            baseurl=file://{output_dir}/repo1
            gpgcheck=1

            [hermeto-repo]
            baseurl=file://{output_dir}/hermeto-repo
            gpgcheck=1
            name=Packages unaffiliated with an official repository
            """,
            id="no_repo_options",
        ),
        pytest.param(
            {
                "rpm": {
                    "dnf": {
                        "repo1": {"gpgcheck": 0},
                        "hermeto-repo": {"sslverify": False, "timeout": 4},
                    }
                }
            },
            """
             [repo1]
             baseurl=file://{output_dir}/repo1
             gpgcheck=0

             [hermeto-repo]
             name=Packages unaffiliated with an official repository
             baseurl=file://{output_dir}/hermeto-repo
             gpgcheck=1
             sslverify=False
             timeout=4
             """,
            id="dnf_repo_options",
        ),
    ],
)
def test_generate_repofiles(
    rooted_tmp_path: RootedPath, expected_repofile: str, options: dict[str, Any] | None
) -> None:
    package_dir = rooted_tmp_path.join_within_root(DEFAULT_PACKAGE_DIR)
    arch_dir = Path(package_dir.path, "x86_64")
    for dir_ in ["repo1", "hermeto-repo", "repos.d"]:
        Path(arch_dir, dir_).mkdir(parents=True)

    _generate_repofiles(rooted_tmp_path.path, rooted_tmp_path.path, options)
    repopath = arch_dir.joinpath("repos.d", f"{APP_NAME}.repo")
    with open(repopath) as f:
        actual = ConfigParser()
        expected = ConfigParser()
        actual.read_file(f)
        expected.read_string(expected_repofile.format(output_dir=arch_dir.as_posix()))
        assert expected == actual


RPM_FILE = "foo-1.0-2.fc39.x86_64.rpm"
DOWNLOAD_URL = f"https://example.com/{RPM_FILE}"


@pytest.mark.parametrize(
    "opt_rpm_tags,metadata,purl_format_str,sbom_properties",
    [
        pytest.param(
            {},
            {"repoid": "foorepo", "url": DOWNLOAD_URL, "checksum": "sha256:21bb2a09"},
            "pkg:rpm/{name}@{version}-{release}?arch={arch}&checksum={checksum}&repository_id={repoid}",
            [],
            id="with_repoid_and_url",
        ),
        pytest.param(
            {"vendor": "Fedora Project"},
            {"repoid": "foorepo", "url": DOWNLOAD_URL, "checksum": "sha256:21bb2a09"},
            "pkg:rpm/fedora/{name}@{version}-{release}?arch={arch}&checksum={checksum}&repository_id={repoid}",
            [],
            id="with_namespace",
        ),
        pytest.param(
            {"vendor": "RPM Fusion"},
            {"repoid": "foorepo", "url": DOWNLOAD_URL, "checksum": "sha256:21bb2a09"},
            "pkg:rpm/rpm_fusion/{name}@{version}-{release}?arch={arch}&checksum={checksum}&repository_id={repoid}",
            [],
            id="with_normalized_namespace",
        ),
        pytest.param(
            {"epoch": "2"},
            {"repoid": "foorepo", "url": DOWNLOAD_URL, "checksum": "sha256:21bb2a09"},
            "pkg:rpm/{name}@{version}-{release}?arch={arch}&checksum={checksum}&epoch={epoch}&repository_id={repoid}",
            [],
            id="with_epoch",
        ),
        pytest.param(
            {"arch": "noarch"},
            {"repoid": "foorepo", "url": DOWNLOAD_URL, "checksum": "sha256:21bb2a09"},
            "pkg:rpm/{name}@{version}-{release}?arch={arch}&checksum={checksum}&repository_id={repoid}",
            [],
            id="with_noarch",
        ),
        pytest.param(
            {},
            {"repoid": "foorepo", "url": DOWNLOAD_URL, "checksum": "sha256:21bb2a09"},
            "pkg:rpm/{name}@{version}-{release}?arch=src&checksum={checksum}&repository_id={repoid}",
            [],
            id="with_src_rpm",
        ),
        pytest.param(
            {},
            {"url": DOWNLOAD_URL, "checksum": "sha256:21bb2a09"},
            "pkg:rpm/{name}@{version}-{release}?arch={arch}&checksum={checksum}&download_url={url}",
            [],
            id="no_repoid",
        ),
        pytest.param(
            {},
            {"repoid": "foorepo", "url": DOWNLOAD_URL},
            "pkg:rpm/{name}@{version}-{release}?arch={arch}&repository_id={repoid}",
            [Property(name=f"{APP_NAME}:missing_hash:in_file", value="rpms.lock.yaml")],
            id="no_checksum",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.rpm.main.run_cmd")
def test_generate_sbom_components(
    mock_run_cmd: mock.Mock,
    opt_rpm_tags: dict[str, str],
    metadata: dict[str, str],
    purl_format_str: str,
    sbom_properties: list[Property],
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    rpm_tags = {
        "name": "foo",
        "version": "1.0",
        "release": "2.fc39",
        "arch": "x86_64",
    }
    rpm_tags.update(opt_rpm_tags)

    if request.node.callspec.id == "with_src_rpm":
        rpm_file_path = tmp_path / Path(RPM_FILE).with_suffix(".src.rpm")
    else:
        rpm_file_path = tmp_path / RPM_FILE

    files_metadata = {rpm_file_path: metadata}

    mock_run_cmd.return_value = "\n".join([f"{k}={v}" for k, v in rpm_tags.items()])
    components = _generate_sbom_components(files_metadata, Path("rpms.lock.yaml"))

    assert components == [
        Component(
            name=rpm_tags["name"],
            version=rpm_tags["version"],
            purl=purl_format_str.format(**{**rpm_tags, **metadata}),
            properties=sbom_properties,
        )
    ]


def test_verify_downloaded_unsupported_hash_alg() -> None:
    metadata = {Path("foo"): {"checksum": "noalg:unmatchedchecksum", "size": None}}
    with pytest.raises(ChecksumVerificationFailed):
        _verify_downloaded(metadata)


class TestRedhatRpmsLock:
    @pytest.fixture
    def raw_content(self) -> dict:
        return {"lockfileVendor": "redhat", "lockfileVersion": 1, "arches": []}

    @pytest.mark.parametrize(
        "attr, expected",
        [
            pytest.param("generated_repoid", f"{APP_NAME}-abcdef", id="repoid"),
            pytest.param(
                "generated_source_repoid", f"{APP_NAME}-abcdef-source", id="source_repoid"
            ),
        ],
    )
    @mock.patch("hermeto.core.package_managers.rpm.redhat.uuid")
    def test_internal_repoid(
        self, mock_uuid: mock.Mock, raw_content: dict, attr: str, expected: str
    ) -> None:
        mock_uuid.uuid4.return_value.hex = "abcdefghijklmn"
        lock = RedhatRpmsLock.model_validate(raw_content)
        assert getattr(lock, attr) == expected


class TestRepofile:
    @pytest.mark.parametrize(
        "defaults, data, expected",
        [
            pytest.param(None, {}, True, id="no_defaults_no_sections"),
            pytest.param({"foo": "bar"}, {}, True, id="just_defaults_no_sections"),
            pytest.param({"fake": {"foo": "bar"}}, {}, True, id="complex_defaults_no_sections"),
            pytest.param(None, {"section": {"foo": "bar"}}, False, id="with_data"),
        ],
    )
    def test_empty(
        self, data: dict[str, Any], defaults: dict[str, Any] | None, expected: bool
    ) -> None:
        actual = _Repofile(defaults)
        actual.read_dict(data)
        assert actual.empty == expected

    @pytest.mark.parametrize(
        "defaults, data, expected",
        [
            pytest.param(
                None, {"section": {"foo": "bar"}}, {"section": {"foo": "bar"}}, id="no_defaults"
            ),
            pytest.param(
                {"default": "baz"},
                {"section": {"foo": "bar"}},
                {"section": {"foo": "bar", "default": "baz"}},
                id="defaults_no_value_conflict",
            ),
            pytest.param(
                {"foo": "baz"},
                {"section1": {"foo": "bar"}, "section2": {"foo2": "bar2"}},
                {"section1": {"foo": "bar"}, "section2": {"foo2": "bar2", "foo": "baz"}},
                id="defaults_value_conflict",
            ),
        ],
    )
    def test_apply_defaults(
        self, data: dict[str, Any], defaults: dict[str, Any] | None, expected: dict[str, Any]
    ) -> None:
        expected_r = _Repofile()
        expected_r.read_dict(expected)
        actual = _Repofile(defaults)
        actual.read_dict(data)
        actual._apply_defaults()
        assert actual == expected_r


@pytest.mark.parametrize(
    "ssl_verify, expected_mode",
    [
        pytest.param(True, ssl.CERT_REQUIRED, id="host_verification_required_when_verify_true"),
        pytest.param(False, ssl.CERT_NONE, id="host_verification_disabled_when_verify_false"),
    ],
)
def test_get_ssl_context_verify_mode(ssl_verify: bool, expected_mode: int) -> None:
    ssl_context = _get_ssl_context(SSLOptions(ssl_verify=ssl_verify))
    assert ssl_context.verify_mode is expected_mode
