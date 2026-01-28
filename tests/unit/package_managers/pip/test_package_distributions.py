from unittest import mock

import pypi_simple
import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import FetchError, PackageRejected
from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.package_managers.pip.package_distributions import (
    WheelsFilter,
    _parse_py_version,
    _process_prefer_binary_mode,
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
    digests: dict[str, str] | None = None,
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


class TestProcessPreferBinaryMode:
    """Tests for _process_prefer_binary_mode function."""

    def test_returns_wheels_plus_best_sdist(self) -> None:
        """Prefer-binary mode returns wheels and the best sdist together."""
        wheel1 = mock_distribution_package_info(
            name="pkg-1.0.0-cp312-cp312-manylinux_x86_64.whl",
            version="1.0.0",
            package_type="wheel",
        )
        wheel2 = mock_distribution_package_info(
            name="pkg-1.0.0-py3-none-any.whl",
            version="1.0.0",
            package_type="wheel",
        )
        sdist_tar = mock_distribution_package_info(
            name="pkg-1.0.0.tar.gz",
            version="1.0.0",
            package_type="sdist",
        )
        sdist_zip = mock_distribution_package_info(
            name="pkg-1.0.0.zip",
            version="1.0.0",
            package_type="sdist",
        )

        result = _process_prefer_binary_mode(
            sdists=[sdist_zip, sdist_tar],
            wheels=[wheel1, wheel2],
            name="pkg",
            version="1.0.0",
        )

        assert len(result) == 3
        assert result[0] == wheel1
        assert result[1] == wheel2
        assert result[2] == sdist_tar  # .tar.gz preferred over .zip

    def test_returns_only_wheels_when_no_sdist(
        self,
    ) -> None:
        """Wheel-only packages (no sdist on PyPI) return only wheels with debug log."""
        wheel = mock_distribution_package_info(
            name="pkg-1.0.0-py3-none-any.whl",
            version="1.0.0",
            package_type="wheel",
        )

        result = _process_prefer_binary_mode(
            sdists=[],
            wheels=[wheel],
            name="pkg",
            version="1.0.0",
        )

        assert result == [wheel]

    def test_falls_back_to_sdist_when_no_wheels(self) -> None:
        """No matching wheels falls back to sdist-only."""
        sdist = mock_distribution_package_info(
            name="pkg-1.0.0.tar.gz",
            version="1.0.0",
            package_type="sdist",
        )

        result = _process_prefer_binary_mode(
            sdists=[sdist],
            wheels=[],
            name="pkg",
            version="1.0.0",
        )

        assert result == [sdist]


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
    binary_filters: PipBinaryFilters | None,
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
    @pytest.mark.parametrize(
        "filter_kwargs, expected",
        [
            pytest.param(
                {},
                {
                    "packages": None,
                    "arch": {"x86_64"},
                    "os": {"linux"},
                    "py_version": None,
                    "py_impl": {"cp"},
                    "abi": None,
                    "platform_regex": None,
                },
                id="default_filters",
            ),
            pytest.param(
                {
                    "packages": "numpy,pandas",
                    "arch": "x86_64,aarch64",
                    "os": "linux,macos",
                    "py_version": 312,
                    "py_impl": "cp,pp",
                },
                {
                    "packages": {"numpy", "pandas"},
                    "arch": {"x86_64", "aarch64"},
                    "os": {"linux", "macos"},
                    "py_version": 312,
                    "py_impl": {"cp", "pp"},
                },
                id="custom_filters",
            ),
            pytest.param(
                {
                    "packages": ":all:",
                    "arch": ":all:",
                    "os": ":all:",
                    "py_impl": ":all:",
                    "abi": ":all:",
                },
                {
                    "packages": None,
                    "arch": None,
                    "os": None,
                    "py_impl": None,
                    "abi": None,
                },
                id="all_keyword",
            ),
        ],
    )
    def test_init_with_valid_values(self, filter_kwargs: dict, expected: dict) -> None:
        filters = PipBinaryFilters(**filter_kwargs)
        wheels_filter = WheelsFilter(filters)

        for attr, expected_value in expected.items():
            assert getattr(wheels_filter, attr) == expected_value

    @pytest.mark.parametrize(
        "filter_kwargs",
        [
            pytest.param({"py_version": 3.12}, id="invalid_py_version_type"),
            pytest.param(
                {"platform": "manylinux.*", "os": "linux", "arch": "aarch64"},
                id="invalid_platform_os_and_arch_combination",
            ),
            pytest.param({"platform": "*"}, id="invalid_platform_regex_syntax"),
        ],
    )
    def test_init_with_invalid_values_fails(self, filter_kwargs: dict) -> None:
        with pytest.raises(ValueError):
            PipBinaryFilters(**filter_kwargs)

    def test_parse_py_version_from_interpreter(self) -> None:
        assert _parse_py_version("cp312") == 312
        assert _parse_py_version("pp312") == 312
        assert _parse_py_version("py3") == 3
        assert _parse_py_version("py2.py3") == 3

    def test_filter_with_invalid_wheel_filename_is_skipped(self) -> None:
        filters = PipBinaryFilters()
        wheels_filter = WheelsFilter(filters)

        wheels = [mock_pypi_simple_distribution_package("foo.whl", "1.0.0")]

        result = wheels_filter.filter(wheels)
        assert result == []

    def test_filter_with_arch_and_os_fields(self) -> None:
        filters = PipBinaryFilters(arch="aarch64", os="macos")
        wheels_filter = WheelsFilter(filters)

        wrong_os = mock_pypi_simple_distribution_package(
            "package-1.0.0-cp312-cp312-manylinux_2_28_aarch64.whl", "1.0.0"
        )
        wrong_arch = mock_pypi_simple_distribution_package(
            "package-1.0.0-cp312-cp312-macos_10_9_x86_64.whl", "1.0.0"
        )
        correct = mock_pypi_simple_distribution_package(
            "package-1.0.0-cp312-cp312-macos_10_9_aarch64.whl", "1.0.0"
        )

        wheels = [wrong_arch, wrong_os, correct]
        result = wheels_filter.filter(wheels)
        assert result == [correct]

    def test_filter_with_platform_regex_field(self) -> None:
        filters = PipBinaryFilters(platform="manylinux.*")
        wheels_filter = WheelsFilter(filters)

        wrong_platform = mock_pypi_simple_distribution_package(
            "package-1.0.0-cp312-cp312-linux_x86_64.whl", "1.0.0"
        )
        correct_platform_1 = mock_pypi_simple_distribution_package(
            "package-1.0.0-cp312-cp312-manylinux_2_28_x86_64.whl", "1.0.0"
        )
        correct_platform_2 = mock_pypi_simple_distribution_package(
            "package-1.0.0-cp312-cp312-manylinux_2_28_aarch64.whl", "1.0.0"
        )

        wheels = [wrong_platform, correct_platform_1, correct_platform_2]
        result = wheels_filter.filter(wheels)
        assert result == [correct_platform_1, correct_platform_2]

    @pytest.mark.parametrize(
        "wheel_filename, matches",
        [
            pytest.param(
                "package-1.0.0-cp312-cp312-linux_x86_64.whl", True, id="identical_py_version"
            ),
            pytest.param(
                "package-1.0.0-cp311-abi3-linux_x86_64.whl", True, id="lower_py_version_with_abi3"
            ),
            pytest.param(
                "package-1.0.0-cp311-none-linux_x86_64.whl", True, id="lower_py_version_with_none"
            ),
            pytest.param(
                "package-1.0.0-cp311-cp311-linux_x86_64.whl", False, id="lower_py_version"
            ),
            pytest.param(
                "package-1.0.0-cp313-cp313-linux_x86_64.whl", False, id="higher_py_version"
            ),
        ],
    )
    def test_filter_with_specific_py_version(self, wheel_filename: str, matches: bool) -> None:
        filters = PipBinaryFilters(py_version=312)
        wheels_filter = WheelsFilter(filters)

        test_wheel = mock_pypi_simple_distribution_package(wheel_filename, "1.0.0")

        wheels = [test_wheel]
        result = wheels_filter.filter(wheels)
        assert result == wheels if matches else result == []
