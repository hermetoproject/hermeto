# SPDX-License-Identifier: GPL-3.0-only
import re
from pathlib import Path
from typing import Any, Literal, cast
from unittest import mock

import pydantic
import pytest as pytest

from hermeto.core.errors import InvalidInput
from hermeto.core.models.input import (
    BINARY_FILTER_ALL,
    BundlerBinaryFilters,
    BundlerPackageInput,
    GomodPackageInput,
    NpmPackageInput,
    PackageInput,
    PipBinaryFilters,
    PipPackageInput,
    Request,
    RpmPackageInput,
    SSLOptions,
    _validate_binary_filter_format,
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
                        "arch": BINARY_FILTER_ALL,
                        "os": BINARY_FILTER_ALL,
                        "py_impl": BINARY_FILTER_ALL,
                        "py_version": None,
                        "abi": BINARY_FILTER_ALL,
                        "platform": None,
                        "packages": BINARY_FILTER_ALL,
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
            pytest.param(
                {
                    "type": "pip",
                    "binary": {
                        "arch": "aarch64,armv7l",
                        "os": "darwin,windows",
                        "py_version": 39,
                        "py_impl": "pp,jy",
                        "abi": "cp,pp",
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
                        "arch": "aarch64,armv7l",
                        "os": "darwin,windows",
                        "py_impl": "pp,jy",
                        "py_version": 39,
                        "abi": "cp,pp",
                        "platform": None,
                        "packages": "numpy,pandas",
                    },
                },
                id="pip_with_binary_filters",
            ),
            pytest.param(
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
                        "platform": "x86_64-linux,universal-darwin",
                        "packages": "nokogiri,ffi",
                    },
                },
                id="bundler_with_binary_filters",
            ),
            pytest.param(
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
                        "arch": "aarch64,ppc64le",
                    },
                },
                id="rpm_with_binary_filters",
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


class TestBinaryFilterValidation:
    """Test core binary filter validation functionality."""

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            pytest.param(BINARY_FILTER_ALL, BINARY_FILTER_ALL, id="all_keyword"),
            pytest.param(
                f"  {BINARY_FILTER_ALL}  ", BINARY_FILTER_ALL, id="all_keyword_with_whitespace"
            ),
            pytest.param("x86_64", "x86_64", id="single_value"),
            pytest.param("x86_64,aarch64", "x86_64,aarch64", id="comma_separated"),
            pytest.param("x86_64 ,aarch64", "x86_64,aarch64", id="comma_separated_with_whitespace"),
            pytest.param("x86_64,,aarch64", "x86_64,aarch64", id="empty_components_ignored"),
        ],
    )
    def test_accepts_valid_formats(self, input_value: str, expected: str) -> None:
        """Test that valid formats are accepted."""
        assert _validate_binary_filter_format(input_value) == expected

    @pytest.mark.parametrize(
        "input_value,error_match",
        [
            pytest.param("", "Binary filter cannot contain only empty values", id="empty_string"),
            pytest.param(
                " ,,", "Binary filter cannot contain only empty values", id="empty_separated_string"
            ),
            pytest.param(123, "must be a string", id="non_string_type"),
        ],
    )
    def test_rejects_invalid_formats(self, input_value: Any, error_match: str) -> None:
        """Test that invalid formats are rejected."""
        with pytest.raises(ValueError, match=error_match):
            _validate_binary_filter_format(input_value)


class TestLegacyAllowBinary:
    """Test legacy allow_binary field migration functionality."""

    @pytest.mark.parametrize(
        "package_class,package_type",
        [
            pytest.param(PipPackageInput, "pip", id="pip"),
            pytest.param(BundlerPackageInput, "bundler", id="bundler"),
        ],
    )
    def test_no_migration_when_allow_binary_false(
        self,
        package_class: type[PipPackageInput | BundlerPackageInput],
        package_type: Literal["pip", "bundler"],
    ) -> None:
        """Test early return when allow_binary=False."""
        package = package_class(type=package_type, allow_binary=False)
        assert package.allow_binary is False
        assert package.binary is None

    @pytest.mark.parametrize(
        "package_class,package_type,binary_filter_class",
        [
            pytest.param(PipPackageInput, "pip", PipBinaryFilters, id="pip"),
            pytest.param(BundlerPackageInput, "bundler", BundlerBinaryFilters, id="bundler"),
        ],
    )
    def test_migration_when_allow_binary_true(
        self,
        package_class: type[PipPackageInput | BundlerPackageInput],
        package_type: Literal["pip", "bundler"],
        binary_filter_class: type[PipBinaryFilters | BundlerBinaryFilters],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test allow_binary=True migrates to binary filters."""
        package = package_class(type=package_type, allow_binary=True)

        assert package.binary == binary_filter_class.with_allow_binary_behavior()
        assert package.allow_binary is False
        assert "deprecated" in caplog.text

    @pytest.mark.parametrize(
        "package_class,package_type",
        [
            pytest.param(PipPackageInput, "pip", id="pip"),
            pytest.param(BundlerPackageInput, "bundler", id="bundler"),
        ],
    )
    def test_both_fields_binary_unchanged(
        self,
        package_class: type[PipPackageInput | BundlerPackageInput],
        package_type: Literal["pip", "bundler"],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test binary field unchanged when both fields specified."""
        package = package_class(
            type=package_type, allow_binary=True, binary={"packages": "numpy,pandas"}
        )

        assert package.binary is not None
        assert package.binary.packages == "numpy,pandas"  # Our value, not :all:
        assert package.allow_binary is False
        assert "Both" in caplog.text and "precedence" in caplog.text
