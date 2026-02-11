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
from hermeto.core.models.input import ExtraOptions, RpmBinaryFilters, RpmPackageInput, SSLOptions
from hermeto.core.models.sbom import Annotation, Component, Property
from hermeto.core.package_managers.rpm import fetch_rpm_source, inject_files_post
from hermeto.core.package_managers.rpm.main import (
    DEFAULT_PACKAGE_DIR,
    _createrepo,
    _download,
    _generate_repofiles,
    _generate_repos,
    _generate_sbom_components,
    _get_ssl_context,
    _Repofile,
    _resolve_rpm_project,
    _verify_downloaded,
)
from hermeto.core.package_managers.rpm.redhat import RedhatRpmsLock
from hermeto.core.rooted_path import RootedPath

RPM_LOCK_FILE_DATA = """
lockfileVersion: 1
lockfileVendor: redhat
arches:
  - arch: x86_64
    packages:
      - url: https://example.com/x86_64/Packages/v/vim-enhanced-9.1.158-1.fc38.x86_64.rpm
        checksum: sha256:21bb2a09852e75a693d277435c162e1a910835c53c3cee7636dd552d450ed0f1
        size: 1976132
        repoid: updates
    source:
      - url: https://example.com/source/tree/Packages/v/vim-9.1.158-1.fc38.src.rpm
        checksum: sha256:94803b5e1ff601bf4009f223cb53037cdfa2fe559d90251bbe85a3a5bc6d2aab
        size: 14735448
        repoid: updates-source
    module_metadata:
      - url: https://example.com/x86_64/repodata/683718e724821ff45bf625a1b63f0431919bfff012af57589da57fd88dc6b445-modules.yaml.gz
        checksum: sha256:683718e724821ff45bf625a1b63f0431919bfff012af57589da57fd88dc6b445
        size: 76926
        repoid: updates
"""


@pytest.mark.parametrize(
    "model_input,result_options",
    [
        pytest.param(mock.Mock(options=None), None, id="fetch_with_no_options"),
        pytest.param(
            RpmPackageInput.model_construct(
                type="rpm",
                options=ExtraOptions.model_construct(dnf={"foorepo": {"foo": 1, "bar": False}}),
            ),
            {
                "rpm": {
                    "dnf": {"foorepo": {"foo": 1, "bar": False}},
                    "ssl": None,
                },
            },
            id="fetch_with_dnf_options",
        ),
        pytest.param(
            [
                RpmPackageInput.model_construct(
                    type="rpm",
                    options=ExtraOptions.model_construct(dnf={"foorepo": {"foo": 1, "bar": False}}),
                ),
                RpmPackageInput.model_construct(
                    type="rpm",
                    options=ExtraOptions.model_construct(dnf={"bazrepo": {"baz": 0}}),
                ),
            ],
            {
                "rpm": {
                    "dnf": {"bazrepo": {"baz": 0}},
                    "ssl": None,
                }
            },
            id="fetch_multiple_dnf_option_sets",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.rpm.main.create_backend_annotation")
@mock.patch("hermeto.core.package_managers.rpm.main.RequestOutput.from_obj_list")
@mock.patch("hermeto.core.package_managers.rpm.main._resolve_rpm_project")
def test_fetch_rpm_source(
    mock_resolve_rpm_project: mock.Mock,
    mock_from_obj_list: mock.Mock,
    mock_create_annotation: mock.Mock,
    model_input: mock.Mock | RpmPackageInput | list[RpmPackageInput],
    result_options: dict[str, dict[str, Any]] | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _has_multiple_options(rpm_models: list[RpmPackageInput]) -> bool:
        options = 0
        for model in rpm_models:
            if model.options:
                options += 1
        return options > 1

    mock_components = [mock.Mock()]
    mock_resolve_rpm_project.return_value = mock_components
    mock_annotation = Annotation(
        subjects=set(),
        annotator={"organization": {"name": "red hat"}},
        timestamp="2026-01-01T00:00:00Z",
        text="hermeto:backend:rpm",
    )
    mock_create_annotation.return_value = mock_annotation
    mock_request = mock.Mock()
    mock_request.rpm_packages = model_input if isinstance(model_input, list) else [model_input]
    fetch_rpm_source(mock_request)

    if isinstance(model_input, list):
        mock_components *= len(model_input)

        if _has_multiple_options(model_input):
            assert "Multiple sets of DNF options detected on the input:" in caplog.text

    mock_resolve_rpm_project.assert_called()
    mock_create_annotation.assert_called_with(mock_components, "rpm")
    mock_from_obj_list.assert_called_with(
        components=mock_components,
        environment_variables=[],
        project_files=[],
        options=result_options,
        annotations=[mock_annotation],
    )


def test_resolve_rpm_project_no_lockfile(rooted_tmp_path: RootedPath) -> None:
    with pytest.raises(LockfileNotFound):
        # MagicMock is to pass Path/str to LockfileNotFound and avoid TypeError
        mock_source_dir = mock.MagicMock()
        mock_source_dir.join_within_root.return_value.path.exists.return_value = False
        _resolve_rpm_project(mock_source_dir, mock.Mock())


def test_resolve_rpm_project_invalid_yaml_format(rooted_tmp_path: RootedPath) -> None:
    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        # colon is missing at the end
        f.write("lockfileVendor: redhat\nlockfileVersion: 1\narches\n")
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)

    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        # end of line is missing between items
        f.write("lockfileVendor: redhat lockfileVersion: 1\narches:\n")
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)


def test_resolve_rpm_project_invalid_lockfile_format(rooted_tmp_path: RootedPath) -> None:
    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "lockfileVendor": "unknown",
                "lockfileVersion": 1,
                "arches": [],
            },
            f,
        )
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)

    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": 2,
                "arches": [],
            },
            f,
        )
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)

    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": "zz",
                "arches": [],
            },
            f,
        )
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)

    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "vendor": "redhat",
                "lockfileVersion": 1,
                "arches": [],
            },
            f,
        )
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)

    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": 1,
                "arches": "everything",
            },
            f,
        )
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)

    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": "zz",
                "arches": [
                    {
                        "arch": "x86_64",
                        "packages": [
                            {
                                "address": "SOME_ADDRESS",
                                "size": 1111,
                            },
                        ],
                    },
                ],
            },
            f,
        )
    with pytest.raises(InvalidLockfileFormat):
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)


def test_resolve_rpm_project_arch_empty(rooted_tmp_path: RootedPath) -> None:
    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": 1,
                "arches": [
                    {
                        "arch": "x86_64",
                    },
                ],
            },
            f,
        )
    with pytest.raises(InvalidLockfileFormat) as exc_info:
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)

    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": 1,
                "arches": [
                    {
                        "arch": "aarch64",
                        "packages": [],
                    },
                ],
            },
            f,
        )
    with pytest.raises(InvalidLockfileFormat) as exc_info:
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)

    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": 1,
                "arches": [
                    {
                        "arch": "i686",
                        "packages": [],
                        "source": [],
                    },
                    {
                        "arch": "x86_64",
                        "packages": [
                            {
                                "url": "SOME_URL",
                            },
                        ],
                    },
                ],
            },
            f,
        )
    with pytest.raises(InvalidLockfileFormat) as exc_info:
        _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)
    assert "At least one field ('packages', 'source') must be set in every arch." in str(
        exc_info.value
    )


@mock.patch("hermeto.core.package_managers.rpm.main._download")
def test_resolve_rpm_project_correct_format(
    mock_download: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    with open(rooted_tmp_path.join_within_root("rpms.lock.yaml"), "w") as f:
        yaml.safe_dump(
            {
                "lockfileVendor": "redhat",
                "lockfileVersion": 1,
                "arches": [
                    {
                        "arch": "x86_64",
                        "packages": [
                            {
                                "repoid": "foo",
                                "url": "SOME_URL",
                            },
                        ],
                        "source": [
                            {
                                "url": "SOME_URL",
                            },
                        ],
                    },
                ],
            },
            f,
        )
    _resolve_rpm_project(rooted_tmp_path, rooted_tmp_path)


@mock.patch(
    "hermeto.core.package_managers.rpm.main.open",
    new_callable=mock.mock_open,
)
@mock.patch("hermeto.core.package_managers.rpm.main._download")
@mock.patch("hermeto.core.package_managers.rpm.main._verify_downloaded")
@mock.patch("hermeto.core.package_managers.rpm.main.RedhatRpmsLock.model_validate")
@mock.patch("hermeto.core.package_managers.rpm.main._generate_sbom_components")
def test_resolve_rpm_project(
    mock_generate_sbom_components: mock.Mock,
    mock_model_validate: mock.Mock,
    mock_verify_downloaded: mock.Mock,
    mock_download: mock.Mock,
    mock_open: mock.Mock,
) -> None:
    output_dir = mock.Mock()
    mock_package_dir_path = mock.Mock()
    output_dir.join_within_root.return_value.path = mock_package_dir_path
    mock_download.return_value = {}

    source_dir = mock.Mock()
    source_dir.subpath_from_root = Path()

    _resolve_rpm_project(source_dir, output_dir, None)
    mock_download.assert_called_once_with(
        mock_model_validate.return_value, mock_package_dir_path, None, None
    )
    mock_verify_downloaded.assert_called_once_with({})
    mock_generate_sbom_components.assert_called_once_with({}, Path("rpms.lock.yaml"), False)


@mock.patch("hermeto.core.package_managers.rpm.main.run_cmd")
def test_createrepo(mock_run_cmd: mock.Mock, rooted_tmp_path: RootedPath) -> None:
    repodir = rooted_tmp_path
    repoid = "repo1"
    _createrepo(repoid, repodir.path)
    mock_run_cmd.assert_called_once_with(
        ["createrepo_c", "--general-compress-type", "gz", str(repodir)], params={}
    )


@mock.patch("hermeto.core.package_managers.rpm.main._createrepo")
def test_generate_repos(mock_createrepo: mock.Mock, rooted_tmp_path: RootedPath) -> None:
    package_dir = rooted_tmp_path.join_within_root(DEFAULT_PACKAGE_DIR)
    arch_dir = package_dir.path.joinpath("x86_64")
    arch_dir.joinpath("repo1").mkdir(parents=True)
    arch_dir.joinpath("repos.d").mkdir(parents=True)
    _generate_repos(rooted_tmp_path.path)
    mock_createrepo.assert_called_once_with("repo1", arch_dir.joinpath("repo1"))


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


@mock.patch("hermeto.core.package_managers.rpm.main.Path")
@mock.patch("hermeto.core.package_managers.rpm.main._generate_repofiles")
@mock.patch("hermeto.core.package_managers.rpm.main._generate_repos")
def test_inject_files_post(
    mock_generate_repos: mock.Mock,
    mock_generate_repofiles: mock.Mock,
    mock_path: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    inject_files_post(
        from_output_dir=rooted_tmp_path.path, for_output_dir=rooted_tmp_path.path, options={}
    )
    mock_generate_repos.assert_called_once_with(rooted_tmp_path.path)
    mock_generate_repofiles.assert_called_with(rooted_tmp_path.path, rooted_tmp_path.path, {})


@mock.patch("ssl.create_default_context")
@mock.patch("hermeto.core.package_managers.rpm.main.asyncio.run")
@mock.patch("hermeto.core.package_managers.rpm.main.async_download_files")
def test_download(
    mock_async_download_files: mock.Mock, mock_asyncio: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    lock = RedhatRpmsLock.model_validate(yaml.safe_load(RPM_LOCK_FILE_DATA))
    _download(lock, rooted_tmp_path.path)
    mock_async_download_files.assert_called_once_with(
        {
            "https://example.com/x86_64/Packages/v/vim-enhanced-9.1.158-1.fc38.x86_64.rpm": str(
                rooted_tmp_path.path.joinpath(
                    "x86_64/updates/vim-enhanced-9.1.158-1.fc38.x86_64.rpm"
                )
            ),
            "https://example.com/source/tree/Packages/v/vim-9.1.158-1.fc38.src.rpm": str(
                rooted_tmp_path.path.joinpath("x86_64/updates-source/vim-9.1.158-1.fc38.src.rpm")
            ),
            "https://example.com/x86_64/repodata/683718e724821ff45bf625a1b63f0431919bfff012af57589da57fd88dc6b445-modules.yaml.gz": str(
                rooted_tmp_path.path.joinpath(
                    "x86_64/updates/683718e724821ff45bf625a1b63f0431919bfff012af57589da57fd88dc6b445-modules.yaml.gz"
                )
            ),
        },
        5,
        ssl_context=None,
    )
    mock_asyncio.assert_called_once()


@mock.patch("hermeto.core.package_managers.rpm.main.asyncio.run")
def test_download_filters_architectures(
    mock_asyncio: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    """Test that _download only processes architectures matching the filter."""
    lock = RedhatRpmsLock.model_validate(
        {
            "lockfileVersion": 1,
            "lockfileVendor": "redhat",
            "arches": [
                {"arch": "x86_64", "packages": [{"url": "http://x86.rpm"}]},
                {"arch": "aarch64", "packages": [{"url": "http://arm.rpm"}]},
            ],
        }
    )

    metadata = _download(lock, rooted_tmp_path.path, None, RpmBinaryFilters(arch="x86_64"))

    # The paths should only be for x86_64 packages
    paths = [str(p) for p in metadata.keys()]
    assert all("x86_64" in p for p in paths)
    assert not any("aarch64" in p for p in paths)


@mock.patch("pathlib.Path.stat")
def test_verify_downloaded_unexpected_size(stat_mock: mock.Mock) -> None:
    stat_mock.return_value = mock.Mock()
    stat_mock.st_size = 0
    metadata = {Path("foo"): {"size": 12345}}

    with pytest.raises(ChecksumVerificationFailed):
        _verify_downloaded(metadata)


def test_verify_downloaded_unsupported_hash_alg() -> None:
    metadata = {Path("foo"): {"checksum": "noalg:unmatchedchecksum", "size": None}}
    with pytest.raises(ChecksumVerificationFailed):
        _verify_downloaded(metadata)


@mock.patch(
    "hermeto.core.package_managers.rpm.main.open",
    new_callable=mock.mock_open,
    read_data=b"test",
)
def test_verify_downloaded_unmatched_checksum(mock_open: mock.Mock) -> None:
    metadata = {Path("foo"): {"checksum": "sha256:unmatchedchecksum", "size": None}}
    with pytest.raises(ChecksumVerificationFailed):
        _verify_downloaded(metadata)


class TestRedhatRpmsLock:
    @pytest.fixture
    def raw_content(self) -> dict:
        return {"lockfileVendor": "redhat", "lockfileVersion": 1, "arches": []}

    @mock.patch("hermeto.core.package_managers.rpm.redhat.uuid")
    def test_internal_repoid(self, mock_uuid: mock.Mock, raw_content: dict) -> None:
        mock_uuid.uuid4.return_value.hex = "abcdefghijklmn"
        lock = RedhatRpmsLock.model_validate(raw_content)
        assert lock.generated_repoid == f"{APP_NAME}-abcdef"

    @mock.patch("hermeto.core.package_managers.rpm.redhat.uuid")
    def test_internal_source_repoid(self, mock_uuid: mock.Mock, raw_content: dict) -> None:
        mock_uuid.uuid4.return_value.hex = "abcdefghijklmn"
        lock = RedhatRpmsLock.model_validate(raw_content)
        assert lock.generated_source_repoid == f"{APP_NAME}-abcdef-source"


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

    @mock.patch("hermeto.core.package_managers.rpm.main._Repofile._apply_defaults")
    @mock.patch("hermeto.core.package_managers.rpm.main.ConfigParser.write")
    def test_write(
        self, mock_superclass_write: mock.Mock, mock_apply_defaults: mock.Mock, tmp_path: Path
    ) -> None:
        mock_superclass_write.return_value = None

        with open(tmp_path / "test.repo", "w") as f:
            _Repofile({"foo": "bar"}).write(f)

        mock_apply_defaults.assert_called_once()


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
