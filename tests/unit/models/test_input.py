import re
from pathlib import Path
from typing import Any, Optional, cast
from unittest import mock

import pydantic
import pytest as pytest

from hermeto.core.errors import InvalidInput
from hermeto.core.models.input import (
    BINARY_FILTER_ALL,
    BinaryFilter,
    BinaryFilterField,
    BundlerBinaryFilters,
    BundlerPackageInput,
    GomodPackageInput,
    Mode,
    NpmPackageInput,
    PackageInput,
    PipBinaryFilters,
    PipPackageInput,
    Request,
    RpmBinaryFilters,
    RpmPackageInput,
    SSLOptions,
    _parse_binary_filter,
    parse_user_input,
)
from hermeto.core.rooted_path import RootedPath


def test_parse_user_input() -> None:
    expect_error = re.compile(r"1 validation error for user input\ntype\n  Input should be 'gomod'")
    with pytest.raises(InvalidInput, match=expect_error):
        parse_user_input(GomodPackageInput.model_validate, {"type": "go-package"})


class TestPackageInput:
    @pytest.mark.parametrize(
        "input_data, expect_data",
        [
            (
                {"type": "gomod"},
                {"type": "gomod", "path": Path(".")},
            ),
            (
                {"type": "gomod", "path": "./some/path"},
                {"type": "gomod", "path": Path("some/path")},
            ),
            (
                {"type": "pip"},
                {
                    "type": "pip",
                    "path": Path("."),
                    "requirements_files": None,
                    "requirements_build_files": None,
                    "allow_binary": False,
                    "binary": None,
                },
            ),
            (
                {
                    "type": "pip",
                    "requirements_files": ["reqs.txt"],
                    "requirements_build_files": [],
                    "allow_binary": True,
                },
                {
                    "type": "pip",
                    "path": Path("."),
                    "requirements_files": [Path("reqs.txt")],
                    "requirements_build_files": [],
                    "allow_binary": False,
                    "binary": {
                        "arch": {"filters": set()},
                        "os": {"filters": set()},
                        "py_impl": {"filters": set()},
                        "py_version": {"filters": set()},
                        "packages": {"filters": set()},
                    },
                },
            ),
            (
                {"type": "rpm"},
                {
                    "type": "rpm",
                    "path": Path("."),
                    "options": None,
                    "include_summary_in_sbom": False,
                    "binary": None,
                },
            ),
            (
                {
                    "type": "rpm",
                    "options": {
                        "dnf": {
                            "main": {"best": True, "debuglevel": 2},
                            "foorepo": {"arch": "x86_64", "enabled": True},
                        }
                    },
                    "include_summary_in_sbom": False,
                },
                {
                    "type": "rpm",
                    "path": Path("."),
                    "options": {
                        "dnf": {
                            "main": {"best": True, "debuglevel": 2},
                            "foorepo": {"arch": "x86_64", "enabled": True},
                        },
                        "ssl": None,
                    },
                    "include_summary_in_sbom": False,
                    "binary": None,
                },
            ),
            (
                {
                    "type": "rpm",
                    "options": {"ssl": {"ssl_verify": 0}},
                },
                {
                    "type": "rpm",
                    "path": Path("."),
                    "options": {
                        "dnf": None,
                        "ssl": {
                            "ca_bundle": None,
                            "client_cert": None,
                            "client_key": None,
                            "ssl_verify": False,
                        },
                    },
                    "include_summary_in_sbom": False,
                    "binary": None,
                },
            ),
            (
                {
                    "type": "rpm",
                    "options": {
                        "dnf": {
                            "main": {"best": True, "debuglevel": 2},
                            "foorepo": {"arch": "x86_64", "enabled": True},
                        },
                        "ssl": {"ssl_verify": 0},
                    },
                },
                {
                    "type": "rpm",
                    "path": Path("."),
                    "options": {
                        "dnf": {
                            "main": {"best": True, "debuglevel": 2},
                            "foorepo": {"arch": "x86_64", "enabled": True},
                        },
                        "ssl": {
                            "ca_bundle": None,
                            "client_cert": None,
                            "client_key": None,
                            "ssl_verify": False,
                        },
                    },
                    "include_summary_in_sbom": False,
                    "binary": None,
                },
            ),
            (
                {
                    "type": "pip",
                    "binary": {
                        "arch": "aarch64,armv7l",
                        "os": "darwin,windows",
                        "py_version": "3.9,3.10",
                        "py_impl": "pp,jy",
                        "packages": "numpy,pandas",
                    },
                },
                {
                    "type": "pip",
                    "path": Path("."),
                    "requirements_files": None,
                    "requirements_build_files": None,
                    "allow_binary": False,
                    "binary": {
                        "arch": {"filters": {"aarch64", "armv7l"}},
                        "os": {"filters": {"darwin", "windows"}},
                        "py_impl": {"filters": {"pp", "jy"}},
                        "py_version": {"filters": {"3.9", "3.10"}},
                        "packages": {"filters": {"numpy", "pandas"}},
                    },
                },
            ),
            (
                {
                    "type": "bundler",
                    "binary": {
                        "platform": "x86_64-linux,universal-darwin",
                        "packages": "nokogiri,ffi",
                    },
                },
                {
                    "type": "bundler",
                    "path": Path("."),
                    "allow_binary": False,
                    "binary": {
                        "platform": {"filters": {"x86_64-linux", "universal-darwin"}},
                        "packages": {"filters": {"nokogiri", "ffi"}},
                    },
                },
            ),
            (
                {
                    "type": "rpm",
                    "binary": {"arch": "aarch64,ppc64le"},
                },
                {
                    "type": "rpm",
                    "path": Path("."),
                    "options": None,
                    "include_summary_in_sbom": False,
                    "binary": {
                        "arch": {"filters": {"aarch64", "ppc64le"}},
                    },
                },
            ),
        ],
    )
    def test_valid_packages(self, input_data: dict[str, Any], expect_data: dict[str, Any]) -> None:
        adapter: pydantic.TypeAdapter[PackageInput] = pydantic.TypeAdapter(PackageInput)
        package = cast(PackageInput, adapter.validate_python(input_data))
        assert package.model_dump() == expect_data

    @pytest.mark.parametrize(
        "input_data, expect_error",
        [
            pytest.param(
                {}, r"Unable to extract tag using discriminator 'type'", id="no_type_discrinator"
            ),
            pytest.param(
                {"type": "go-package"},
                r"Input tag 'go-package' found using 'type' does not match any of the expected tags: 'bundler', 'cargo', 'generic', 'gomod', 'npm', 'pip', 'rpm', 'yarn'",
                id="incorrect_type_tag",
            ),
            pytest.param(
                {"type": "gomod", "path": "/absolute"},
                r"Value error, path must be relative: /absolute",
                id="path_not_relative",
            ),
            pytest.param(
                {"type": "gomod", "path": ".."},
                r"Value error, path contains ..: ..",
                id="gomod_path_references_parent_directory",
            ),
            pytest.param(
                {"type": "gomod", "path": "weird/../subpath"},
                r"Value error, path contains ..: weird/../subpath",
                id="gomod_path_references_parent_directory_2",
            ),
            pytest.param(
                {"type": "pip", "requirements_files": ["weird/../subpath"]},
                r"pip.requirements_files\n  Value error, path contains ..: weird/../subpath",
                id="pip_path_references_parent_directory",
            ),
            pytest.param(
                {"type": "pip", "requirements_build_files": ["weird/../subpath"]},
                r"pip.requirements_build_files\n  Value error, path contains ..: weird/../subpath",
                id="pip_path_references_parent_directory",
            ),
            pytest.param(
                {"type": "pip", "requirements_files": None},
                r"none is not an allowed value",
                id="pip_no_requirements_files",
            ),
            pytest.param(
                {"type": "pip", "requirements_build_files": None},
                r"none is not an allowed value",
                id="pip_no_requirements_build_files",
            ),
            pytest.param(
                {"type": "rpm", "options": {"extra": "foo"}},
                r".*Extra inputs are not permitted \[type=extra_forbidden, input_value='foo'.*",
                id="rpm_extra_unknown_options",
            ),
            pytest.param(
                {"type": "rpm", "options": {"dnf": "bad_type"}},
                r"Unexpected data type for 'options.dnf.bad_type' in input JSON",
                id="rpm_bad_type_for_dnf_namespace",
            ),
            pytest.param(
                {"type": "rpm", "options": {"dnf": {"repo": "bad_type"}}},
                r"Unexpected data type for 'options.dnf.repo.bad_type' in input JSON",
                id="rpm_bad_type_for_dnf_options",
            ),
            pytest.param(
                {"type": "pip", "binary": "invalid_string"},
                r"Input should be a valid dictionary",
                id="pip_binary_invalid_string",
            ),
            pytest.param(
                {"type": "pip", "binary": {"unknown_field": "value"}},
                r"Extra inputs are not permitted",
                id="pip_binary_unknown_field",
            ),
            pytest.param(
                {"type": "pip", "binary": {"arch": 123}},
                r"Value error, Binary filter must be a string",
                id="pip_binary_arch_not_string",
            ),
            pytest.param(
                {"type": "bundler", "binary": {"platform": []}},
                r"Value error, Binary filter must be a string",
                id="bundler_binary_platform_not_string",
            ),
            pytest.param(
                {"type": "rpm", "binary": {"arch": ""}},
                r"Value error, No valid filters found",
                id="rpm_binary_empty_arch",
            ),
        ],
    )
    def test_invalid_packages(self, input_data: dict[str, Any], expect_error: str) -> None:
        with pytest.raises(pydantic.ValidationError, match=expect_error):
            adapter: pydantic.TypeAdapter[PackageInput] = pydantic.TypeAdapter(PackageInput)
            adapter.validate_python(input_data)


class TestSSLOptions:
    @staticmethod
    def patched_isfile(path: Path) -> bool:
        return str(path) == "pass"

    def test_defaults(self) -> None:
        ssl = SSLOptions()
        assert (
            ssl.client_cert is None
            and ssl.client_key is None
            and ssl.ca_bundle is None
            and ssl.ssl_verify is True
        )

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param(
                {"client_cert": "fail", "client_key": "pass"}, id="client_cert_file_not_found"
            ),
            pytest.param(
                {"client_cert": "pass", "client_key": "fail"}, id="client_key_file_not_found"
            ),
            pytest.param(
                {"client_cert": "pass", "client_key": "pass", "ca_bundle": "fail"},
                id="ca_bundle_file_not_found",
            ),
        ],
    )
    def test_auth_file_not_found(self, data: dict[str, str]) -> None:
        fail_opt = [i for i, v in data.items() if v == "fail"].pop()
        err = rf"Specified ssl auth file '{fail_opt}':'fail' is not a regular file."

        with mock.patch.object(Path, "is_file", new=self.patched_isfile):
            with pytest.raises(pydantic.ValidationError, match=err):
                SSLOptions(**data)

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param({"client_cert": "pass"}, id="client_key_missing"),
            pytest.param({"client_key": "pass"}, id="client_cert_missing"),
            pytest.param(
                {"client_key": "pass", "ca_bundle": "pass"},
                id="client_cert_missing_ca_bundle_no_effect",
            ),
        ],
    )
    def test_client_cert_and_key_both_provided(self, data: dict[str, str]) -> None:
        err = "When using client certificates, client_key and client_cert must both be provided."
        with mock.patch.object(Path, "is_file", new=self.patched_isfile):
            with pytest.raises(pydantic.ValidationError, match=err):
                SSLOptions(**data)


class TestRequest:
    def test_valid_request(self, tmp_path: Path) -> None:
        tmp_path.joinpath("subpath").mkdir(exist_ok=True)

        request = Request(
            source_dir=str(tmp_path),
            output_dir=str(tmp_path),
            packages=[
                GomodPackageInput(type="gomod"),
                GomodPackageInput(type="gomod", path="subpath"),
                NpmPackageInput(type="npm"),
                NpmPackageInput(type="npm", path="subpath"),
                PipPackageInput(type="pip", requirements_build_files=[]),
                # check de-duplication
                GomodPackageInput(type="gomod"),
                GomodPackageInput(type="gomod", path="subpath"),
                NpmPackageInput(type="npm"),
                NpmPackageInput(type="npm", path="subpath"),
                PipPackageInput(type="pip", requirements_build_files=[]),
            ],
        )

        assert request.model_dump() == {
            "source_dir": RootedPath(tmp_path),
            "output_dir": RootedPath(tmp_path),
            "packages": [
                {"type": "gomod", "path": Path(".")},
                {"type": "gomod", "path": Path("subpath")},
                {"type": "npm", "path": Path(".")},
                {"type": "npm", "path": Path("subpath")},
                {
                    "type": "pip",
                    "path": Path("."),
                    "requirements_files": None,
                    "requirements_build_files": [],
                    "allow_binary": False,
                    "binary": None,
                },
            ],
            "flags": frozenset(),
            "mode": Mode.STRICT,
        }
        assert isinstance(request.source_dir, RootedPath)
        assert isinstance(request.output_dir, RootedPath)

    def test_packages_properties(self, tmp_path: Path) -> None:
        packages = [{"type": "gomod"}, {"type": "npm"}, {"type": "pip"}, {"type": "rpm"}]
        request = Request(source_dir=tmp_path, output_dir=tmp_path, packages=packages)
        assert request.gomod_packages == [GomodPackageInput(type="gomod")]
        assert request.npm_packages == [NpmPackageInput(type="npm")]
        assert request.pip_packages == [PipPackageInput(type="pip")]
        assert request.rpm_packages == [RpmPackageInput(type="rpm")]

    @pytest.mark.parametrize("which_path", ["source_dir", "output_dir"])
    def test_path_not_absolute(self, which_path: str) -> None:
        input_data = {
            "source_dir": "/source",
            "output_dir": "/output",
            which_path: "relative/path",
            "packages": [],
        }
        expect_error = "Value error, path must be absolute: relative/path"
        with pytest.raises(pydantic.ValidationError, match=expect_error):
            Request.model_validate(input_data)

    def test_conflicting_packages(self, tmp_path: Path) -> None:
        expect_error = f"Value error, conflict by {('pip', Path('.'))}"
        with pytest.raises(pydantic.ValidationError, match=re.escape(expect_error)):
            Request(
                source_dir=tmp_path,
                output_dir=tmp_path,
                packages=[
                    PipPackageInput(type="pip"),
                    PipPackageInput(type="pip", requirements_files=["foo.txt"]),
                ],
            )

    @pytest.mark.parametrize(
        "path, expect_error",
        [
            ("no-such-dir", "package path does not exist (or is not a directory): no-such-dir"),
            ("not-a-dir", "package path does not exist (or is not a directory): not-a-dir"),
            (
                "suspicious-symlink",
                "package path (a symlink?) leads outside source directory: suspicious-symlink",
            ),
        ],
    )
    def test_invalid_package_paths(self, path: str, expect_error: str, tmp_path: Path) -> None:
        tmp_path.joinpath("suspicious-symlink").symlink_to("..")
        tmp_path.joinpath("not-a-dir").touch()

        with pytest.raises(pydantic.ValidationError, match=re.escape(expect_error)):
            Request(
                source_dir=tmp_path,
                output_dir=tmp_path,
                packages=[GomodPackageInput(type="gomod", path=path)],
            )

    def test_invalid_flags(self) -> None:
        expect_error = r"Input should be 'cgo-disable', 'dev-package-managers', 'force-gomod-tidy', 'gomod-vendor' or 'gomod-vendor-check'"
        with pytest.raises(pydantic.ValidationError, match=expect_error):
            Request(
                source_dir="/source",
                output_dir="/output",
                packages=[],
                flags=["no-such-flag"],
            )

    def test_empty_packages(self) -> None:
        expect_error = r"Value error, at least one package must be defined, got an empty list"
        with pytest.raises(pydantic.ValidationError, match=expect_error):
            Request(
                source_dir="/source",
                output_dir="/output",
                packages=[],
            )


class TestBinaryFilter:
    @pytest.mark.parametrize(
        "input_filters,expected_filters,expected_is_all",
        [
            pytest.param(None, set(), True, id="empty_filter_creation"),
            pytest.param({"x86_64"}, {"x86_64"}, False, id="filter_with_single_value"),
            pytest.param(
                {"x86_64", "aarch64"},
                {"x86_64", "aarch64"},
                False,
                id="filter_with_multiple_values",
            ),
            pytest.param(
                {"x86_64", "x86_64", "aarch64"},
                {"x86_64", "aarch64"},
                False,
                id="filter_deduplicates_values",
            ),
            pytest.param(set(), set(), True, id="is_all_property_empty_set"),
        ],
    )
    def test_filter_creation_and_properties(
        self, input_filters: Optional[set[str]], expected_filters: set[str], expected_is_all: bool
    ) -> None:
        if input_filters is None:
            filter = BinaryFilter()
        else:
            filter = BinaryFilter(filters=input_filters)
        assert filter.filters == expected_filters
        assert filter.is_all is expected_is_all


class TestParseBinaryFilter:
    def test_parse_none_returns_empty_filter(self) -> None:
        result = _parse_binary_filter(None)
        assert isinstance(result, BinaryFilter)
        assert result.filters == set()
        assert result.is_all is True

    def test_parse_existing_filter_returns_unchanged(self) -> None:
        original = BinaryFilter(filters={"x86_64"})
        result = _parse_binary_filter(original)
        assert result.filters == {"x86_64"}
        assert result is original

    @pytest.mark.parametrize(
        "input_value,expected_filters,expected_is_all",
        [
            pytest.param(BINARY_FILTER_ALL, set(), True, id="all_keyword_lowercase"),
            pytest.param(f"  {BINARY_FILTER_ALL}  ", set(), True, id="all_keyword_with_whitespace"),
            pytest.param("x86_64", {"x86_64"}, False, id="single_value"),
            pytest.param(
                "x86_64,aarch64", {"x86_64", "aarch64"}, False, id="multiple_comma_separated"
            ),
            pytest.param(
                " x86_64 , aarch64 ", {"x86_64", "aarch64"}, False, id="whitespace_handling"
            ),
            pytest.param(
                "x86_64,,aarch64", {"x86_64", "aarch64"}, False, id="empty_components_ignored"
            ),
            pytest.param("x86_64,aarch64,", {"x86_64", "aarch64"}, False, id="trailing_comma"),
            pytest.param(",x86_64,aarch64", {"x86_64", "aarch64"}, False, id="leading_comma"),
            pytest.param(
                f"x86_64,{BINARY_FILTER_ALL},aarch64", set(), True, id="all_with_other_values"
            ),
            pytest.param(
                f"darwin,{BINARY_FILTER_ALL},linux,windows",
                set(),
                True,
                id="all_overrides_everything",
            ),
            pytest.param(":ALL:", {":ALL:"}, False, id="all_keyword_case_sensitive"),
        ],
    )
    def test_parse_string_valid_cases(
        self, input_value: str, expected_filters: set[str], expected_is_all: bool
    ) -> None:
        result = _parse_binary_filter(input_value)
        assert result.filters == expected_filters
        assert result.is_all is expected_is_all

    @pytest.mark.parametrize(
        "input_value,error_match",
        [
            pytest.param("", "No valid filters found", id="empty_string"),
            pytest.param("   ", "No valid filters found", id="only_whitespace"),
            pytest.param(",,,", "No valid filters found", id="only_commas"),
            pytest.param(" , , ", "No valid filters found", id="comma_whitespace_only"),
        ],
    )
    def test_parse_string_invalid_cases(self, input_value: str, error_match: str) -> None:
        with pytest.raises(ValueError, match=error_match):
            _parse_binary_filter(input_value)

    @pytest.mark.parametrize(
        "invalid_type",
        [
            pytest.param(123, id="integer"),
            pytest.param(["x86_64"], id="list"),
            pytest.param({"arch": "x86_64"}, id="dict"),
            pytest.param(True, id="boolean"),
        ],
    )
    def test_parse_invalid_types(self, invalid_type: Any) -> None:
        with pytest.raises(ValueError) as exc_info:
            _parse_binary_filter(invalid_type)
        error_msg = str(exc_info.value)
        assert f"Got type: {type(invalid_type).__name__}" in error_msg
        assert "must be a string" in error_msg
        assert "e.g., 'x86_64,aarch64' or ':all:'" in error_msg

    @pytest.mark.parametrize(
        "input_value,expected_filters",
        [
            pytest.param("x86_64,café,日本", {"x86_64", "café", "日本"}, id="unicode_values"),
            pytest.param("x86-64,arm-linux", {"x86-64", "arm-linux"}, id="hyphenated_values"),
            pytest.param(
                "linux_gnu,darwin_x64", {"linux_gnu", "darwin_x64"}, id="underscore_values"
            ),
            pytest.param("3.9,3.10,3.11", {"3.9", "3.10", "3.11"}, id="dotted_values"),
            pytest.param("x86_64-linux-gnu", {"x86_64-linux-gnu"}, id="mixed_special_chars"),
            pytest.param("123,456,789", {"123", "456", "789"}, id="numeric_string_values"),
            pytest.param("x86_64,123,abc", {"x86_64", "123", "abc"}, id="mixed_numeric_alpha"),
        ],
    )
    def test_special_character_handling(self, input_value: str, expected_filters: set[str]) -> None:
        result = _parse_binary_filter(input_value)
        assert result.filters == expected_filters
        assert result.is_all is False


class TestBinaryFilterField:

    class TestModel(pydantic.BaseModel):
        """Used to test BinaryFilterField."""

        arch_filter: BinaryFilterField

    def test_field_accepts_filter_string_all(self) -> None:
        model = self.TestModel(arch_filter=":all:")
        assert model.arch_filter.filters == set()

    def test_field_accepts_filter_string(self) -> None:
        model = self.TestModel(arch_filter="x86_64,aarch64")
        assert model.arch_filter.filters == {"x86_64", "aarch64"}

    def test_field_accepts_binary_filter(self) -> None:
        binary_filter = BinaryFilter(filters={"x86_64"})
        model = self.TestModel(arch_filter=binary_filter)
        assert model.arch_filter is binary_filter

    def test_field_accepts_none(self) -> None:
        model = self.TestModel(arch_filter=None)
        assert model.arch_filter.filters == set()
        assert model.arch_filter.is_all is True


class TestPipBinaryFilters:
    def test_default_values(self) -> None:
        filters = PipBinaryFilters()
        assert filters.arch.filters == {"x86_64"}
        assert filters.os.filters == {"linux"}
        assert filters.py_impl.filters == {"cp"}
        assert filters.py_version.filters == set()
        assert filters.packages.filters == set()

    def test_with_allow_binary_behavior(self) -> None:
        filters = PipBinaryFilters.with_allow_binary_behavior()
        assert filters.arch.is_all is True
        assert filters.os.is_all is True
        assert filters.py_impl.is_all is True
        assert filters.py_version.is_all is True
        assert filters.packages.is_all is True

    def test_binary_filters_with_values(self) -> None:
        filters = PipBinaryFilters(
            arch="x86_64,aarch64",
            os="linux,darwin",
            py_version="3.9,3.10",
            py_impl="cp,pp",
            packages="numpy,pandas",
        )
        assert filters.arch.filters == {"x86_64", "aarch64"}
        assert filters.os.filters == {"linux", "darwin"}
        assert filters.py_version.filters == {"3.9", "3.10"}
        assert filters.py_impl.filters == {"cp", "pp"}
        assert filters.packages.filters == {"numpy", "pandas"}


class TestBundlerBinaryFilters:
    def test_default_values(self) -> None:
        filters = BundlerBinaryFilters()
        assert filters.platform.filters == set()
        assert filters.packages.filters == set()

    def test_with_allow_binary_behavior(self) -> None:
        filters = BundlerBinaryFilters.with_allow_binary_behavior()
        assert filters.platform.is_all is True
        assert filters.packages.is_all is True

    def test_binary_filters_with_values(self) -> None:
        filters = BundlerBinaryFilters(
            platform="x86_64-linux,universal-darwin",
            packages="nokogiri,ffi",
        )
        assert filters.platform.filters == {"x86_64-linux", "universal-darwin"}
        assert filters.packages.filters == {"nokogiri", "ffi"}


class TestRpmBinaryFilters:
    def test_default_values(self) -> None:
        filters = RpmBinaryFilters()
        assert filters.arch.filters == set()

    def test_platform_filters_with_values(self) -> None:
        filters = RpmBinaryFilters(arch="x86_64,aarch64")
        assert filters.arch.filters == {"x86_64", "aarch64"}


class TestLegacyAllowBinary:
    def test_migrate_pip_allow_binary_true(self) -> None:
        package = PipPackageInput(type="pip", allow_binary=True)
        assert package.allow_binary is False
        assert package.binary is not None
        assert isinstance(package.binary, PipBinaryFilters)
        assert package.binary.arch.is_all is True
        assert package.binary.os.is_all is True
        assert package.binary.py_impl.is_all is True
        assert package.binary.py_version.is_all is True
        assert package.binary.packages.is_all is True

    def test_pip_allow_binary_false_no_binary_filters(self) -> None:
        package = PipPackageInput(type="pip", allow_binary=False)
        assert package.allow_binary is False
        assert package.binary is None

    def test_pip_both_fields_binary_takes_precedence(self) -> None:
        binary_options = PipBinaryFilters(arch="aarch64", os="darwin")
        package = PipPackageInput(type="pip", allow_binary=True, binary=binary_options)
        assert package.allow_binary is False
        assert package.binary is binary_options
        assert package.binary.arch.filters == {"aarch64"}
        assert package.binary.os.filters == {"darwin"}

    def test_migrate_bundler_allow_binary_true(self) -> None:
        package = BundlerPackageInput(type="bundler", allow_binary=True)
        assert package.allow_binary is False
        assert package.binary is not None
        assert isinstance(package.binary, BundlerBinaryFilters)
        assert package.binary.platform.is_all is True
        assert package.binary.packages.is_all is True

    def test_bundler_allow_binary_false_no_filters(self) -> None:
        package = BundlerPackageInput(type="bundler", allow_binary=False)
        assert package.allow_binary is False
        assert package.binary is None

    def test_bundler_both_fields_binary_takes_precedence(self) -> None:
        binary_options = BundlerBinaryFilters(platform="x86_64-linux")
        package = BundlerPackageInput(type="bundler", allow_binary=True, binary=binary_options)
        assert package.allow_binary is False
        assert package.binary is binary_options
        assert package.binary.platform.filters == {"x86_64-linux"}
