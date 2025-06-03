from typing import Optional
from unittest import mock

import pypi_simple
import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import FetchError, PackageRejected
from hermeto.core.package_managers.pip.package_distributions import (
    _sdist_preference,
    process_package_distributions,
)
from hermeto.core.rooted_path import RootedPath
from tests.unit.package_managers.pip.test_main import (
    mock_distribution_package_info,
    mock_requirement,
)


def mock_pypi_simple_distribution_package(
    filename: str,
    version: str,
    package_type: str = "sdist",
    digests: Optional[dict[str, str]] = None,
    is_yanked: bool = False,
) -> pypi_simple.DistributionPackage:
    return pypi_simple.DistributionPackage(
        filename=filename,
        url="",
        project=None,
        version=version,
        package_type=package_type,
        digests=digests or dict(),
        requires_python=None,
        has_sig=None,
        is_yanked=is_yanked,
    )


def test_sdist_sorting() -> None:
    """Test that sdist preference key can be used for sorting in the expected order."""
    unyanked_tar_gz = mock_distribution_package_info(name="unyanked.tar.gz", is_yanked=False)
    unyanked_zip = mock_distribution_package_info(name="unyanked.zip", is_yanked=False)
    unyanked_tar_bz2 = mock_distribution_package_info(name="unyanked.tar.bz2", is_yanked=False)
    yanked_tar_gz = mock_distribution_package_info(name="yanked.tar.gz", is_yanked=True)
    yanked_zip = mock_distribution_package_info(name="yanked.zip", is_yanked=True)
    yanked_tar_bz2 = mock_distribution_package_info(name="yanked.tar.bz2", is_yanked=True)

    # Original order is descending by preference
    sdists = [
        unyanked_tar_gz,
        unyanked_zip,
        unyanked_tar_bz2,
        yanked_tar_gz,
        yanked_zip,
        yanked_tar_bz2,
    ]
    # Expected order is ascending by preference
    expect_order = [
        yanked_tar_bz2,
        yanked_zip,
        yanked_tar_gz,
        unyanked_tar_bz2,
        unyanked_zip,
        unyanked_tar_gz,
    ]

    sdists.sort(key=_sdist_preference)
    assert sdists == expect_order


@mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
def test_process_non_existing_package_distributions(
    mock_get_project_page: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    package_name = "does-not-exists"
    req = mock_requirement(package_name, "pypi", version_specs=[("==", "1.0.0")])

    mock_get_project_page.side_effect = pypi_simple.NoSuchProjectError(package_name, "URL")
    with pytest.raises(FetchError) as exc_info:
        process_package_distributions(req, rooted_tmp_path)

    assert (
        str(exc_info.value)
        == f"PyPI query failed: No details about project '{package_name}' available at URL"
    )


@mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
def test_process_existing_wheel_only_package(
    mock_get_project_page: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    package_name = "aiowsgi"
    version = "0.1.0"
    req = mock_requirement(package_name, "pypi", version_specs=[("==", version)])

    file_1 = package_name + "-" + version + "-py3-none-any.whl"
    file_2 = package_name + "-" + version + "-manylinux1_x86_64.whl"

    mock_get_project_page.return_value = pypi_simple.ProjectPage(
        package_name,
        [
            mock_pypi_simple_distribution_package(file_1, version, "wheel"),
            mock_pypi_simple_distribution_package(file_2, version, "wheel"),
        ],
        None,
        None,
    )
    artifacts = process_package_distributions(req, rooted_tmp_path, allow_binary=True)
    assert artifacts[0].package_type != "sdist"
    assert len(artifacts) == 2
    assert f"No sdist found for package {package_name}=={version}" in caplog.text


@pytest.mark.parametrize("allow_binary", (True, False))
@mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
def test_process_existing_package_without_any_distributions(
    mock_get_project_page: mock.Mock,
    allow_binary: bool,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    package_name = "aiowsgi"
    version = "0.1.0"
    req = mock_requirement(package_name, "pypi", version_specs=[("==", version)])

    with pytest.raises(PackageRejected) as exc_info:
        process_package_distributions(req, rooted_tmp_path, allow_binary=allow_binary)

    assert f"No sdist found for package {package_name}=={version}" in caplog.text
    assert str(exc_info.value) == f"No distributions found for package {package_name}=={version}"

    if allow_binary:
        assert str(exc_info.value.solution) == (
            "Please check that the package exists on PyPI or that the name"
            " and version are correct.\n"
        )
    else:
        assert str(exc_info.value.solution) == (
            "It seems that this version does not exist or isn't published as an"
            " sdist.\n"
            "Try to specify the dependency directly via a URL instead, for example,"
            " the tarball for a GitHub release.\n"
            "Alternatively, allow the use of wheels."
        )


@mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
def test_process_yanked_package_distributions(
    mock_get_project_page: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    package_name = "aiowsgi"
    version = "0.1.0"
    req = mock_requirement(package_name, "pypi", version_specs=[("==", version)])

    mock_get_project_page.return_value = pypi_simple.ProjectPage(
        package_name,
        [
            mock_pypi_simple_distribution_package(
                filename=package_name, version=version, is_yanked=True
            )
        ],
        None,
        None,
    )

    process_package_distributions(req, rooted_tmp_path)
    assert (
        f"The version {version} of package {package_name} is yanked, use a different version"
        in caplog.text
    )


@pytest.mark.parametrize("use_user_hashes", (True, False))
@pytest.mark.parametrize("use_pypi_digests", (True, False))
@mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
def test_process_package_distributions_with_checksums(
    mock_get_project_page: mock.Mock,
    use_user_hashes: bool,
    use_pypi_digests: bool,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    package_name = "aiowsgi"
    version = "0.1.0"
    req = mock_requirement(
        package_name,
        "pypi",
        version_specs=[("==", version)],
        hashes=["sha128:abcdef", "sha256:abcdef", "sha512:xxxxxx"] if use_user_hashes else [],
    )

    mock_get_project_page.return_value = pypi_simple.ProjectPage(
        package_name,
        [
            mock_pypi_simple_distribution_package(package_name, version, "sdist"),
            mock_pypi_simple_distribution_package(
                package_name,
                version,
                "wheel",
                digests=(
                    {"sha128": "abcdef", "sha256": "abcdef", "sha512": "yyyyyy"}
                    if use_pypi_digests
                    else {}
                ),
            ),
        ],
        None,
        None,
    )
    artifacts = process_package_distributions(req, rooted_tmp_path, allow_binary=True)

    if use_user_hashes and use_pypi_digests:
        assert (
            f"{package_name}: using intersection of requirements-file and PyPI-reported checksums"
            in caplog.text
        )
        assert artifacts[1].checksums_to_match == {
            ChecksumInfo("sha128", "abcdef"),
            ChecksumInfo("sha256", "abcdef"),
        }

    elif use_user_hashes and not use_pypi_digests:
        assert f"{package_name}: using requirements-file checksums" in caplog.text
        assert artifacts[1].checksums_to_match == {
            ChecksumInfo("sha128", "abcdef"),
            ChecksumInfo("sha256", "abcdef"),
            ChecksumInfo("sha512", "xxxxxx"),
        }

    elif use_pypi_digests and not use_user_hashes:
        assert f"{package_name}: using PyPI-reported checksums" in caplog.text
        assert artifacts[1].checksums_to_match == {
            ChecksumInfo("sha128", "abcdef"),
            ChecksumInfo("sha256", "abcdef"),
            ChecksumInfo("sha512", "yyyyyy"),
        }

    elif not use_user_hashes and not use_pypi_digests:
        assert (
            f"{package_name}: no checksums reported by PyPI or specified in requirements file"
            in caplog.text
        )
        assert artifacts[1].checksums_to_match == set()


@mock.patch("pypi_simple.PyPISimple.get_project_page")
def test_process_package_distributions_with_different_checksums(
    mock_get_project_page: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    package_name = "aiowsgi"
    version = "0.1.0"
    req = mock_requirement(
        package_name, "pypi", version_specs=[("==", version)], hashes=["sha128:abcdef"]
    )

    mock_get_project_page.return_value = pypi_simple.ProjectPage(
        package_name,
        [
            mock_pypi_simple_distribution_package(package_name, version),
            mock_pypi_simple_distribution_package(
                package_name, version, "wheel", digests={"sha256": "abcdef"}
            ),
        ],
        None,
        None,
    )

    artifacts = process_package_distributions(req, rooted_tmp_path, allow_binary=True)

    assert len(artifacts) == 1
    assert f"Filtering out {package_name} due to checksum mismatch" in caplog.text


@pytest.mark.parametrize(
    "noncanonical_version, canonical_version",
    [
        ("1.0", "1"),
        ("1.0.0", "1"),
        ("1.0.alpha1", "1a1"),
        ("1.1.0", "1.1"),
        ("1.1.alpha1", "1.1a1"),
        ("1.0-1", "1.post1"),
        ("1.1.0-1", "1.1.post1"),
    ],
)
@pytest.mark.parametrize("requested_version_is_canonical", [True, False])
@pytest.mark.parametrize("actual_version_is_canonical", [True, False])
@mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
def test_process_package_distributions_noncanonical_version(
    mock_get_project_page: mock.Mock,
    rooted_tmp_path: RootedPath,
    canonical_version: str,
    noncanonical_version: str,
    requested_version_is_canonical: bool,
    actual_version_is_canonical: bool,
) -> None:
    """Test that canonical names match non-canonical names."""
    if requested_version_is_canonical:
        requested_version = canonical_version
    else:
        requested_version = noncanonical_version

    if actual_version_is_canonical:
        actual_version = canonical_version
    else:
        actual_version = noncanonical_version

    req = mock_requirement("foo", "pypi", version_specs=[("==", requested_version)])
    mock_get_project_page.return_value = pypi_simple.ProjectPage(
        "foo",
        [
            mock_pypi_simple_distribution_package(filename="foo.tar.gz", version=actual_version),
            mock_pypi_simple_distribution_package(
                filename="foo-manylinux.whl", version=actual_version
            ),
        ],
        None,
        None,
    )

    artifacts = process_package_distributions(req, rooted_tmp_path)
    assert artifacts[0].package_type == "sdist"
    assert artifacts[0].version == requested_version
    assert all(w.version == requested_version for w in artifacts[1:])
