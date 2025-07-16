from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

import pydantic

from hermeto import APP_NAME

if TYPE_CHECKING:
    from typing_extensions import Self, assert_never


class PropertyEnum(str, Enum):
    """Property names literals. Workarounds Literals inflexibility."""

    PROP_BUNDLER_PACKAGE_BINARY = f"{APP_NAME}:bundler:package:binary"
    PROP_CDX_NPM_PACKAGE_BUNDLED = "cdx:npm:package:bundled"
    PROP_CDX_NPM_PACKAGE_DEVELOPMENT = "cdx:npm:package:development"
    PROP_FOUND_BY = f"{APP_NAME}:found_by"
    PROP_MISSING_HASH_IN_FILE = f"{APP_NAME}:missing_hash:in_file"
    PROP_PIP_PACKAGE_BINARY = f"{APP_NAME}:pip:package:binary"
    PROP_PIP_PACKAGE_BUILD_DEPENDENCY = f"{APP_NAME}:pip:package:build-dependency"
    PROP_RPM_SUMMARY = f"{APP_NAME}:rpm_summary"

    def __str__(self) -> str:
        return self.value


class Property(pydantic.BaseModel):
    """A property inside an SBOM component."""

    name: PropertyEnum
    value: str


@dataclass(frozen=True)
class PropertySet:
    """Represents the semantic meaning of the set of Properties of a single Component."""

    bundler_package_binary: bool = False
    found_by: Optional[str] = None
    missing_hash_in_file: frozenset[str] = field(default_factory=frozenset)
    npm_bundled: bool = False
    npm_development: bool = False
    pip_build_dependency: bool = False
    pip_package_binary: bool = False
    rpm_summary: str = ""

    @classmethod
    def from_properties(cls, props: Iterable[Property]) -> "Self":
        """Convert a list of SBOM component properties to a PropertySet."""
        bundler_package_binary = False
        found_by = None
        missing_hash_in_file = []
        npm_bundled = False
        npm_development = False
        pip_build_dependency = False
        pip_package_binary = False
        rpm_summary = ""

        for prop in props:
            if prop.name == PropertyEnum.PROP_BUNDLER_PACKAGE_BINARY:
                bundler_package_binary = True
            elif prop.name == PropertyEnum.PROP_CDX_NPM_PACKAGE_BUNDLED:
                npm_bundled = True
            elif prop.name == PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT:
                npm_development = True
            elif prop.name == PropertyEnum.PROP_FOUND_BY:
                found_by = prop.value
            elif prop.name == PropertyEnum.PROP_MISSING_HASH_IN_FILE:
                missing_hash_in_file.append(prop.value)
            elif prop.name == PropertyEnum.PROP_PIP_PACKAGE_BINARY:
                pip_package_binary = True
            elif prop.name == PropertyEnum.PROP_PIP_PACKAGE_BUILD_DEPENDENCY:
                pip_build_dependency = True
            elif prop.name == PropertyEnum.PROP_RPM_SUMMARY:
                rpm_summary = prop.value
            else:
                assert_never(prop.name)

        return cls(
            bundler_package_binary,
            found_by,
            frozenset(missing_hash_in_file),
            npm_bundled,
            npm_development,
            pip_build_dependency,
            pip_package_binary,
            rpm_summary,
        )

    def to_properties(self) -> list[Property]:
        """Convert a PropertySet to a list of SBOM component properties."""
        props = []
        if self.bundler_package_binary:
            props.append(Property(name=PropertyEnum.PROP_BUNDLER_PACKAGE_BINARY, value="true"))
        if self.found_by:
            props.append(Property(name=PropertyEnum.PROP_FOUND_BY, value=self.found_by))
        props.extend(
            Property(name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value=filepath)
            for filepath in self.missing_hash_in_file
        )
        if self.npm_bundled:
            props.append(Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_BUNDLED, value="true"))
        if self.npm_development:
            props.append(Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT, value="true"))
        if self.pip_build_dependency:
            props.append(
                Property(name=PropertyEnum.PROP_PIP_PACKAGE_BUILD_DEPENDENCY, value="true")
            )
        if self.pip_package_binary:
            props.append(Property(name=PropertyEnum.PROP_PIP_PACKAGE_BINARY, value="true"))
        if self.rpm_summary:
            props.append(Property(name=PropertyEnum.PROP_RPM_SUMMARY, value=self.rpm_summary))

        return sorted(props, key=lambda p: (p.name, p.value))

    def merge(self, other: "Self") -> "Self":
        """Combine two PropertySets."""
        cls = type(self)
        return cls(
            bundler_package_binary=self.bundler_package_binary or other.bundler_package_binary,
            found_by=self.found_by or other.found_by,
            missing_hash_in_file=self.missing_hash_in_file | other.missing_hash_in_file,
            npm_bundled=self.npm_bundled and other.npm_bundled,
            npm_development=self.npm_development and other.npm_development,
            pip_build_dependency=self.pip_build_dependency and other.pip_build_dependency,
            pip_package_binary=self.pip_package_binary or other.pip_package_binary,
            rpm_summary=self.rpm_summary or other.rpm_summary,
        )
