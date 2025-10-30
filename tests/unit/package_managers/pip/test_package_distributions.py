from typing import Optional
from unittest import mock

import pypi_simple
import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import FetchError, PackageRejected
from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.package_managers.pip.package_distributions import (
    WheelsFilter,
    _parse_py_version,
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
) -> None:
    package_name = "pkg"
    version = "0.1.0"
    req = mock_requirement(package_name, "pypi", version_specs=[("==", version)])

    file_1 = package_name + "-" + version + "-py3-none-any.whl"
    file_2 = package_name + "-" + version + "-cp311-cp311-any.whl"

    mock_get_project_page.return_value = pypi_simple.ProjectPage(
        package_name,
        [
            mock_pypi_simple_distribution_package(file_1, version, "wheel"),
            mock_pypi_simple_distribution_package(file_2, version, "wheel"),
        ],
        None,
        None,
    )
    artifacts = process_package_distributions(
        req, rooted_tmp_path, PipBinaryFilters.with_allow_binary_behavior()
    )

    assert artifacts[0].package_type == "wheel"
    assert artifacts[1].package_type == "wheel"
    assert len(artifacts) == 2


@pytest.mark.parametrize("binary_filters", (PipBinaryFilters.with_allow_binary_behavior(), None))
@mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
def test_process_existing_package_without_any_distributions(
    mock_get_project_page: mock.Mock,
    binary_filters: Optional[PipBinaryFilters],
    rooted_tmp_path: RootedPath,
) -> None:
    req = mock_requirement("pkg-0.1.0-py3-none-any.whl", "pypi", version_specs=[("==", "0.1.0")])
    with pytest.raises(PackageRejected):
        process_package_distributions(req, rooted_tmp_path, binary_filters=binary_filters)


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
    assert f"Package {package_name}=={version} is yanked, use a different version" in caplog.text


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
    package_name = "pkg-0.1.0-py3-none-any.whl"
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
    artifacts = process_package_distributions(
        req, rooted_tmp_path, PipBinaryFilters.with_allow_binary_behavior()
    )

    if use_user_hashes and use_pypi_digests:
        assert (
            f"{package_name}: using intersection of requirements-file and PyPI-reported checksums"
            in caplog.text
        )
        assert artifacts[0].checksums_to_match == {
            ChecksumInfo("sha128", "abcdef"),
            ChecksumInfo("sha256", "abcdef"),
        }

    elif use_user_hashes and not use_pypi_digests:
        assert f"{package_name}: using requirements-file checksums" in caplog.text
        assert artifacts[0].checksums_to_match == {
            ChecksumInfo("sha128", "abcdef"),
            ChecksumInfo("sha256", "abcdef"),
            ChecksumInfo("sha512", "xxxxxx"),
        }

    elif use_pypi_digests and not use_user_hashes:
        assert f"{package_name}: using PyPI-reported checksums" in caplog.text
        assert artifacts[0].checksums_to_match == {
            ChecksumInfo("sha128", "abcdef"),
            ChecksumInfo("sha256", "abcdef"),
            ChecksumInfo("sha512", "yyyyyy"),
        }

    elif not use_user_hashes and not use_pypi_digests:
        assert (
            f"{package_name}: no checksums reported by PyPI or specified in requirements file"
            in caplog.text
        )
        assert artifacts[0].checksums_to_match == set()


@mock.patch("pypi_simple.PyPISimple.get_project_page")
def test_process_package_distributions_with_different_checksums(
    mock_get_project_page: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    package_name = "pkg-0.1.0-py3-none-any.whl"
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

    artifacts = process_package_distributions(
        req, rooted_tmp_path, PipBinaryFilters.with_allow_binary_behavior()
    )

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


class TestWheelsFilter:
    def test_init_with_default_filters(self) -> None:
        filters = PipBinaryFilters()
        wheels_filter = WheelsFilter(filters)

        assert wheels_filter.packages is None
        assert wheels_filter.arch == {"x86_64"}
        assert wheels_filter.os == {"linux"}
        assert wheels_filter.py_version is None
        assert wheels_filter.py_impl == {"cp"}
        assert wheels_filter.abi is None
        assert wheels_filter.platform_regex is None

    def test_init_with_custom_filters(self) -> None:
        filters = PipBinaryFilters(
            packages="numpy,pandas",
            arch="x86_64,aarch64",
            os="linux,macos",
            py_version=38,
            py_impl="cp,pp",
        )
        wheels_filter = WheelsFilter(filters)

        assert wheels_filter.packages == {"numpy", "pandas"}
        assert wheels_filter.arch == {"x86_64", "aarch64"}
        assert wheels_filter.os == {"linux", "macos"}
        assert wheels_filter.py_version == 38
        assert wheels_filter.py_impl == {"cp", "pp"}

    def test_init_with_all_keyword(self) -> None:
        filters = PipBinaryFilters(
            packages=":all:",
            arch=":all:",
            os=":all:",
            py_impl=":all:",
            abi=":all:",
        )
        wheels_filter = WheelsFilter(filters)

        assert wheels_filter.packages is None
        assert wheels_filter.arch is None
        assert wheels_filter.os is None
        assert wheels_filter.py_impl is None
        assert wheels_filter.abi is None

    def test_init_with_invalid_py_version_fails(self) -> None:
        with pytest.raises(ValueError):
            PipBinaryFilters(py_version=3.11)

    def test_init_with_platform_and_os_or_arch_fails(self) -> None:
        with pytest.raises(ValueError):
            PipBinaryFilters(platform="manylinux.*", os="linux", arch="aarch64")

    def test_init_with_invalid_platform_regex_fails(self) -> None:
        with pytest.raises(ValueError):
            PipBinaryFilters(platform="*")

    def test_filter_with_whitespace_in_constraints(self) -> None:
        filters = PipBinaryFilters(
            arch=" x86_64 , aarch64 ",
            os=" linux , macos, win ",
            py_version=310,
            py_impl=" cp , pp ",
            abi=" abi3 , none ",
        )
        wheels_filter = WheelsFilter(filters)

        assert wheels_filter.arch == {"x86_64", "aarch64"}
        assert wheels_filter.os == {"linux", "macos", "win"}
        assert wheels_filter.py_version == 310
        assert wheels_filter.py_impl == {"cp", "pp"}
        assert wheels_filter.abi == {"abi3", "none"}

    def test_filter_with_invalid_wheel_filename(self) -> None:
        filters = PipBinaryFilters()
        wheels_filter = WheelsFilter(filters)

        wheels = [
            mock_pypi_simple_distribution_package(
                "foo.whl",
                "1.0.0",
            )
        ]

        result = wheels_filter.filter(wheels)
        assert result == []

    def test_filter_with_no_matching_wheels(self) -> None:
        filters = PipBinaryFilters(arch="aarch64", os="macos")
        wheels_filter = WheelsFilter(filters)

        wheels = [
            # wrong arch
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp39-cp39-macos_10_9_x86_64.whl", "1.0.0"
            ),
            # wrong os
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp39-cp39-linux_aarch64.whl", "1.0.0"
            ),
        ]

        result = wheels_filter.filter(wheels)
        assert result == []

    def test_filter_with_arch_and_os_filters(self) -> None:
        filters = PipBinaryFilters(arch="aarch64", os="macos")
        wheels_filter = WheelsFilter(filters)

        wheels = [
            # wrong arch
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp39-cp39-macos_10_9_x86_64.whl",
                "1.0.0",
            ),
            # wrong os
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp39-cp39-linux_aarch64.whl",
                "1.0.0",
            ),
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp39-cp39-macos_10_9_aarch64.whl",
                "1.0.0",
            ),
        ]

        result = wheels_filter.filter(wheels)
        assert len(result) == 1

        filenames = [wheel.filename for wheel in result]
        assert "package-1.0.0-cp39-cp39-macos_10_9_aarch64.whl" in filenames

    def test_filter_with_platform_regex(self) -> None:
        filters = PipBinaryFilters(platform="manylinux.*")
        wheels_filter = WheelsFilter(filters)

        wheels = [
            # wrong platform
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp39-cp39-linux_x86_64.whl", "1.0.0"
            ),
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp39-cp39-manylinux_2_28_x86_64.whl", "1.0.0"
            ),
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp39-cp39-manylinux_2_28_aarch64.whl", "1.0.0"
            ),
        ]

        result = wheels_filter.filter(wheels)
        assert len(result) == 2

        filenames = [wheel.filename for wheel in result]
        assert "package-1.0.0-cp39-cp39-manylinux_2_28_x86_64.whl" in filenames
        assert "package-1.0.0-cp39-cp39-manylinux_2_28_aarch64.whl" in filenames

    def test_filter_lower_py_version_than_requested(self) -> None:
        filters = PipBinaryFilters(py_version=311)
        wheels_filter = WheelsFilter(filters)

        wheels = [
            # incompatible ABI
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp310-cp310-linux_x86_64.whl", "1.0.0"
            ),
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp310-abi3-linux_x86_64.whl", "1.0.0"
            ),
            mock_pypi_simple_distribution_package(
                "package-1.0.0-cp310-none-linux_x86_64.whl", "1.0.0"
            ),
        ]

        result = wheels_filter.filter(wheels)
        assert len(result) == 2

        filenames = [wheel.filename for wheel in result]
        assert "package-1.0.0-cp310-abi3-linux_x86_64.whl" in filenames
        assert "package-1.0.0-cp310-none-linux_x86_64.whl" in filenames

    def test_parse_py_version(self) -> None:
        assert _parse_py_version("cp312") == 312
        assert _parse_py_version("pp312") == 312
        assert _parse_py_version("py3") == 3
        assert _parse_py_version("py2.py3") == 3
