# SPDX-License-Identifier: GPL-3.0-only
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar, Union

import pydantic
from typing_extensions import Self

from hermeto import APP_NAME
from hermeto.core.errors import InvalidInput
from hermeto.core.models.validators import check_sane_relpath, unique
from hermeto.core.rooted_path import PathOutsideRoot, RootedPath

BINARY_FILTER_ALL = ":all:"

if TYPE_CHECKING:
    from pydantic.error_wrappers import ErrorDict


log = logging.getLogger(__name__)

T = TypeVar("T")
ModelT = TypeVar("ModelT", bound=pydantic.BaseModel)


def _handle_legacy_allow_binary(
    instance: Union["PipPackageInput", "BundlerPackageInput"],
    binary_filter_class: type["PipBinaryFilters"] | type["BundlerBinaryFilters"],
) -> None:
    """Handle backward compatibility for allow_binary field.

    May modify instance attributes.
    """
    # If allow_binary is already False, nothing to process
    if not instance.allow_binary:
        return

    # Check if user provided both fields originally
    user_provided_both = instance.allow_binary and instance.binary is not None
    # Determine if allow_binary should be migrated to binary field
    should_migrate_allow_binary = instance.allow_binary and instance.binary is None

    if user_provided_both:
        log.warning(
            "Both 'allow_binary' and 'binary' fields specified. "
            "The 'binary' field will take precedence. "
            "Please remove 'allow_binary' as it is deprecated."
        )
    elif should_migrate_allow_binary:
        log.warning(
            "The 'allow_binary' field is deprecated and will be removed in the next major version. "
            "Please use 'binary': {} instead of 'allow_binary': true."
        )
        instance.binary = binary_filter_class.with_allow_binary_behavior()

    # Set allow_binary to False to prevent duplicate processing
    instance.allow_binary = False


def parse_user_input(to_model: Callable[[T], ModelT], input_obj: T) -> ModelT:
    """Parse user input into a model, re-raise validation errors as InvalidInput."""
    try:
        return to_model(input_obj)
    except pydantic.ValidationError as e:
        raise InvalidInput(_present_user_input_error(e)) from e


def _present_user_input_error(validation_error: pydantic.ValidationError) -> str:
    """Make a slightly nicer representation of a pydantic.ValidationError.

    Compared to pydantic's default message:
    - don't show the model name, just say "user input"
    - don't show the underlying error type (e.g. "type=value_error.const")
    """
    errors = validation_error.errors()
    n_errors = len(errors)

    def show_error(error: "ErrorDict") -> str:
        location = " -> ".join(map(str, error["loc"]))
        if error.get("type") != "union_tag_invalid":
            message = error["msg"]
        else:
            # Handle regular union tag errors (i.e. errors which stem from
            # a missing package manager implementation). Errors in experimental
            # package managers are handled elsewhere.
            ctx = error.get("ctx", {})
            raw = ctx.get("expected_tags", "")
            expected = [t.strip(" '") for t in raw.split(",")]
            quoted = ", ".join(f"'{t}'" for t in sorted(expected))
            message = f"Requested backend type '{ctx.get('tag', '<unknown>')}' doesn't match expected ones: {quoted}"

        if location != "__root__":
            message = f"{location}\n  {message}"

        return message

    header = f"{n_errors} validation error{'' if n_errors == 1 else 's'} for user input"
    details = "\n".join(map(show_error, errors))
    return f"{header}\n{details}"


PackageManagerType = Literal[
    "bundler",
    "cargo",
    "generic",
    "gomod",
    "npm",
    "pip",
    "rpm",
    "yarn",
    # Add experimental package managers here with x- prefix, e.g. "x-foo"
]


Flag = Literal[
    "cgo-disable", "dev-package-managers", "force-gomod-tidy", "gomod-vendor", "gomod-vendor-check"
]


class _PackageInputBase(pydantic.BaseModel, extra="forbid"):
    """Common input attributes accepted for all package types."""

    type: PackageManagerType
    path: Path = Path(".")

    @pydantic.field_validator("path")
    @classmethod
    def _path_is_relative(cls, path: Path) -> Path:
        return check_sane_relpath(path)


class SSLOptions(pydantic.BaseModel, extra="forbid"):
    """SSL options model.

    Defines extra options fields for client TLS authentication.
    """

    client_cert: str | None = None
    client_key: str | None = None
    ca_bundle: str | None = None
    ssl_verify: bool = True

    @pydantic.field_validator("client_key", "client_cert", "ca_bundle")
    @classmethod
    def _validate_auth_file_paths(cls, val: str, info: pydantic.ValidationInfo) -> str | None:
        if val is None:
            return val

        if not Path(val).is_file():
            raise ValueError(
                (
                    f"Specified ssl auth file '{info.field_name}':'{val}' is not a regular file.",
                    "Make sure the file exists and that it has correct permissions.",
                )
            )

        return val

    @pydantic.model_validator(mode="after")
    def _validate_ssl_options(self) -> Self:
        cert_and_key = (self.client_cert, self.client_key)
        if any(cert_and_key) and not all(cert_and_key):
            raise ValueError(
                "When using client certificates, client_key and client_cert must both be provided."
            )

        return self


def _validate_binary_filter_format(value: Any) -> str:
    """Validate binary filter format as either a single value or comma-separated string.

    May return a slightly modified string with non-empty, unique, trimmed values.
    """
    if not isinstance(value, str):
        raise ValueError(f"Binary filter must be a string, got {type(value).__name__}")

    # Maintain input order
    unique_stripped_filters = dict.fromkeys(
        stripped_filter for item in value.split(",") if (stripped_filter := item.strip())
    )
    if not unique_stripped_filters:
        raise ValueError("Binary filter cannot contain only empty values")

    return ",".join(unique_stripped_filters.keys())


BinaryFilterStr = Annotated[str, pydantic.BeforeValidator(_validate_binary_filter_format)]


class BinaryModeOptions(pydantic.BaseModel, extra="forbid"):
    """Base configuration for binary package handling."""

    packages: BinaryFilterStr = BINARY_FILTER_ALL


class PipBinaryFilters(BinaryModeOptions):
    """Binary filters specific to pip packages."""

    arch: BinaryFilterStr = "x86_64"
    os: BinaryFilterStr = "linux"
    py_version: int | None = None
    py_impl: BinaryFilterStr = "cp"
    abi: BinaryFilterStr = BINARY_FILTER_ALL
    platform: str | None = None

    @pydantic.model_validator(mode="after")
    def _validate_platform_exclusivity(self) -> Self:
        has_platform = self.platform is not None
        has_custom_os = self.os != "linux"
        has_custom_arch = self.arch != "x86_64"

        if has_platform and (has_custom_os or has_custom_arch):
            raise ValueError(
                "Use either 'platform' (regex pattern) or 'os' with 'arch', but not both."
            )

        return self

    @pydantic.field_validator("platform")
    @classmethod
    def _validate_platform(cls, value: str | None) -> str | None:
        if value is None:
            return value

        try:
            re.compile(value)
        except re.error as e:
            raise ValueError(f"Invalid platform regex: {value}") from e

        return value

    @classmethod
    def with_allow_binary_behavior(cls) -> Self:
        """Create filters that mimic the old allow_binary=True behavior."""
        return cls(
            arch=BINARY_FILTER_ALL,
            os=BINARY_FILTER_ALL,
            py_impl=BINARY_FILTER_ALL,
        )


class BundlerBinaryFilters(BinaryModeOptions):
    """Binary filters specific to bundler packages."""

    platform: BinaryFilterStr = BINARY_FILTER_ALL

    @classmethod
    def with_allow_binary_behavior(cls) -> Self:
        """Create filters that mimic the old allow_binary=True behavior."""
        return cls()


class RpmBinaryFilters(pydantic.BaseModel, extra="forbid"):
    """Binary filters specific to RPM packages."""

    arch: BinaryFilterStr = BINARY_FILTER_ALL


class BundlerPackageInput(_PackageInputBase):
    """Accepted input for a bundler package."""

    type: Literal["bundler"]
    allow_binary: bool = False
    binary: BundlerBinaryFilters | None = None

    @pydantic.model_validator(mode="after")
    def _handle_legacy_allow_binary_field(self) -> Self:
        """Handle backward compatibility for allow_binary field."""
        _handle_legacy_allow_binary(self, BundlerBinaryFilters)
        return self


class CargoPackageInput(_PackageInputBase):
    """Accepted input for a cargo package."""

    type: Literal["cargo"]


class GenericPackageInput(_PackageInputBase):
    """Accepted input for generic package."""

    type: Literal["generic"]
    lockfile: Path | None = None


class GomodPackageInput(_PackageInputBase):
    """Accepted input for a gomod package."""

    type: Literal["gomod"]


class NpmPackageInput(_PackageInputBase):
    """Accepted input for a npm package."""

    type: Literal["npm"]


class PipPackageInput(_PackageInputBase):
    """Accepted input for a pip package."""

    type: Literal["pip"]
    requirements_files: list[Path] | None = None
    requirements_build_files: list[Path] | None = None
    allow_binary: bool = False
    binary: PipBinaryFilters | None = None

    @pydantic.field_validator("requirements_files", "requirements_build_files")
    @classmethod
    def _no_explicit_none(cls, paths: list[Path] | None) -> list[Path]:
        """Fail if the user explicitly passes None."""
        if paths is None:
            # Note: same error message as pydantic's default
            raise ValueError("none is not an allowed value")
        return paths

    @pydantic.field_validator("requirements_files", "requirements_build_files")
    @classmethod
    def _requirements_file_path_is_relative(cls, paths: list[Path]) -> list[Path]:
        for p in paths:
            check_sane_relpath(p)
        return paths

    @pydantic.model_validator(mode="after")
    def _handle_legacy_allow_binary_field(self) -> Self:
        """Handle backward compatibility for allow_binary field."""
        _handle_legacy_allow_binary(self, PipBinaryFilters)
        return self


class ExtraOptions(pydantic.BaseModel, extra="forbid"):
    """Global package manager extra options model.

    This model takes care of carrying and parsing various kind of extra options that need to be
    passed through to CLI commands/services we interact with underneath to tweak their behaviour
    rather than our own. Each option set is namespaced by the corresponding tool/service it is
    related to.

    TODO: Enable this globally for all pkg managers not just the RpmPackageInput model.
    """

    dnf: dict[Literal["main"] | str, dict[str, Any]] | None = None
    ssl: SSLOptions | None = None

    @pydantic.model_validator(mode="before")
    @classmethod
    def _validate_dnf_options(cls, data: Any) -> Any:
        """DNF options model.

        DNF options can be provided via 2 'streams':
            1) global /etc/dnf/dnf.conf OR
            2) /etc/yum.repos.d/.repo files

        Config options are specified via INI format based on sections. There are 2 types of sections:
            1) global 'main' - either global repo options or DNF control-only options
                - NOTE: there must always ever be a single "main" section

            2) <repoid> sections - options tied specifically to a given defined repo

        [1] https://man7.org/linux/man-pages/man5/dnf.conf.5.html
        """

        def _raise_unexpected_type(repr_: str, *prefixes: str) -> None:
            loc = ".".join(prefixes + (repr_,))
            raise ValueError(f"Unexpected data type for '{loc}' in input JSON: expected 'dict'")

        if "dnf" not in data:
            return data

        prefixes: list[str] = ["options", "dnf"]
        dnf_opts = data["dnf"]

        if not isinstance(dnf_opts, dict):
            _raise_unexpected_type(str(dnf_opts), *prefixes)

        for repo, repo_options in dnf_opts.items():
            prefixes.append(repo)
            if not isinstance(repo_options, dict):
                _raise_unexpected_type(str(repo_options), *prefixes)

        return data


class RpmPackageInput(_PackageInputBase):
    """Accepted input for a rpm package."""

    type: Literal["rpm"]
    include_summary_in_sbom: bool = False
    options: ExtraOptions | None = None
    binary: RpmBinaryFilters | None = None


class YarnPackageInput(_PackageInputBase):
    """Accepted input for a yarn package."""

    type: Literal["yarn"]


PackageInput = Annotated[
    BundlerPackageInput
    | CargoPackageInput
    | GenericPackageInput
    | GomodPackageInput
    | NpmPackageInput
    | PipPackageInput
    | RpmPackageInput
    | YarnPackageInput,
    # https://pydantic-docs.helpmanual.io/usage/types/#discriminated-unions-aka-tagged-unions
    pydantic.Field(discriminator="type"),
]


class Request(pydantic.BaseModel):
    """Holds all data needed for the processing of a single request."""

    source_dir: RootedPath
    output_dir: RootedPath
    packages: list[PackageInput]
    flags: frozenset[Flag] = frozenset()

    @pydantic.field_validator("packages")
    @classmethod
    def _unique_packages(cls, packages: list[PackageInput]) -> list[PackageInput]:
        """De-duplicate the packages to be processed."""
        return unique(packages, by=lambda pkg: (pkg.type, pkg.path))

    @pydantic.field_validator("packages")
    @classmethod
    def _check_packages_paths(
        cls, packages: list[PackageInput], info: pydantic.ValidationInfo
    ) -> list[PackageInput]:
        """Check that package paths are existing subdirectories."""
        # Note that any of the other fields may have failed the validation (hence None), because
        # pydantic always validates all fields without failing early [1]
        # [1] https://github.com/pydantic/pydantic/discussions/9533#discussioncomment-9620872
        source_dir: RootedPath | None = info.data.get("source_dir", None)
        if source_dir is not None:
            for p in packages:
                try:
                    abspath = source_dir.join_within_root(p.path)
                except PathOutsideRoot:
                    raise ValueError(
                        f"package path (a symlink?) leads outside source directory: {p.path}"
                    )
                if not abspath.path.is_dir():
                    raise ValueError(
                        f"package path does not exist (or is not a directory): {p.path}"
                    )
        return packages

    @pydantic.field_validator("flags")
    @classmethod
    def _deprecation_warning(cls, flags: frozenset[Flag]) -> frozenset[Flag]:
        """Print a deprecation warning for flags, if needed."""
        if "gomod-vendor" in flags:
            log.warning(
                "The `gomod-vendor` flag is deprecated and will be removed in future versions. "
                "Note that it will no longer perform automatic vendoring, so in case vendoring "
                "is needed, it needs to be manually added to the source repository."
            )

        if "gomod-vendor-check" in flags:
            log.warning(
                "The `gomod-vendor-check` flag is deprecated and will be removed in future versions. "
                f"Its use is no longer necessary, {APP_NAME} will automatically check the contents of the "
                "vendor directory in case it is present."
            )

        return flags

    @pydantic.field_validator("packages")
    @classmethod
    def _packages_not_empty(cls, packages: list[PackageInput]) -> list[PackageInput]:
        """Check that the packages list is not empty."""
        if len(packages) == 0:
            raise ValueError("at least one package must be defined, got an empty list")
        return packages

    @property
    def bundler_packages(self) -> list[BundlerPackageInput]:
        """Get the bundler packages specified for this request."""
        return self._packages_by_type(BundlerPackageInput)

    @property
    def cargo_packages(self) -> list[CargoPackageInput]:
        """Get the cargo packages specified for this request."""
        return self._packages_by_type(CargoPackageInput)

    @property
    def generic_packages(self) -> list[GenericPackageInput]:
        """Get the generic packages specified for this request."""
        return self._packages_by_type(GenericPackageInput)

    @property
    def gomod_packages(self) -> list[GomodPackageInput]:
        """Get the gomod packages specified for this request."""
        return self._packages_by_type(GomodPackageInput)

    @property
    def npm_packages(self) -> list[NpmPackageInput]:
        """Get the npm packages specified for this request."""
        return self._packages_by_type(NpmPackageInput)

    @property
    def pip_packages(self) -> list[PipPackageInput]:
        """Get the pip packages specified for this request."""
        return self._packages_by_type(PipPackageInput)

    @property
    def rpm_packages(self) -> list[RpmPackageInput]:
        """Get the rpm packages specified for this request."""
        return self._packages_by_type(RpmPackageInput)

    @property
    def yarn_packages(self) -> list[YarnPackageInput]:
        """Get the yarn packages specified for this request."""
        return self._packages_by_type(YarnPackageInput)

    def _packages_by_type(self, pkgtype: type[T]) -> list[T]:
        return [package for package in self.packages if isinstance(package, pkgtype)]
