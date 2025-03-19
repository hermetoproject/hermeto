# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import asyncio
import configparser
import functools
import io
import logging
import os.path
import re
import tarfile
import urllib
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    Iterable,
    Iterator,
    Literal,
    Optional,
    Pattern,
    Union,
    cast,
)

import tomlkit
from packageurl import PackageURL

from hermeto import APP_NAME
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import clone_as_tarball, get_repo_id

if TYPE_CHECKING:
    from typing_extensions import TypeGuard

import pypi_simple
import requests
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name, canonicalize_version

from hermeto.core.checksum import ChecksumInfo, must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import FetchError, PackageRejected, UnexpectedFormat, UnsupportedFeature
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, ProjectFile, RequestOutput
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.general import (
    async_download_files,
    download_binary_file,
    extract_git_info,
)

log = logging.getLogger(__name__)

DEFAULT_BUILD_REQUIREMENTS_FILE = "requirements-build.txt"
DEFAULT_REQUIREMENTS_FILE = "requirements.txt"

# Check that the path component of a URL ends with a full-length git ref
GIT_REF_IN_PATH = re.compile(r"@[a-fA-F0-9]{40}$")

# All supported sdist formats, see https://docs.python.org/3/distutils/sourcedist.html
ZIP_FILE_EXT = ".zip"
COMPRESSED_TAR_EXT = ".tar.Z"
SDIST_FILE_EXTENSIONS = [ZIP_FILE_EXT, ".tar.gz", ".tar.bz2", ".tar.xz", COMPRESSED_TAR_EXT, ".tar"]
SDIST_EXT_PATTERN = r"|".join(map(re.escape, SDIST_FILE_EXTENSIONS))

PYPI_URL = "https://pypi.org"

PIP_METADATA_DOC = (
    "https://github.com/hermetoproject/hermeto/blob/main/docs/pip.md#project-metadata"
)
PIP_REQUIREMENTS_TXT_DOC = (
    "https://github.com/hermetoproject/hermeto/blob/main/docs/pip.md#requirementstxt"
)
PIP_EXTERNAL_DEPS_DOC = (
    "https://github.com/hermetoproject/hermeto/blob/main/docs/pip.md#external-dependencies"
)
PIP_NO_SDIST_DOC = "https://github.com/hermetoproject/hermeto/blob/main/docs/pip.md#dependency-does-not-distribute-sources"


@dataclass
class DistributionPackageInfo:
    """A class representing relevant information about a distribution package."""

    name: str
    version: str
    package_type: Literal["sdist", "wheel"]
    path: Path
    url: str
    index_url: str
    is_yanked: bool

    # PyPi only returns a single checksum for a given download artifact
    pypi_checksums: set[ChecksumInfo] = field(default_factory=set)
    # "User" checksums *must* come from a 'requirements*.txt' file or equivalent
    req_file_checksums: set[ChecksumInfo] = field(default_factory=set)

    checksums_to_match: set[ChecksumInfo] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self.checksums_to_match = self._determine_checksums_to_match()

    def _determine_checksums_to_match(self) -> set[ChecksumInfo]:
        """Determine the set of checksums to match for a given distribution package."""
        checksums: set[ChecksumInfo] = set()

        if self.pypi_checksums and self.req_file_checksums:
            checksums = self.pypi_checksums.intersection(self.req_file_checksums)
            msg = "using intersection of requirements-file and PyPI-reported checksums"
        elif self.pypi_checksums:
            checksums = self.pypi_checksums
            msg = "using PyPI-reported checksums"
        elif self.req_file_checksums:
            checksums = self.req_file_checksums
            msg = "using requirements-file checksums"
        else:
            msg = "no checksums reported by PyPI or specified in requirements file"

        log.debug("%s: %s", self.path.name, msg)
        return checksums

    def should_download(self) -> bool:
        """Determine if this artifact should be downloaded.

        If there are checksums in the requirements file, but they do not match
        with those reported by PyPI, we do not want to download the artifact.

        Otherwise, we do.
        """
        return (
            len(self.checksums_to_match) > 0
            or len(self.pypi_checksums) == 0
            or len(self.req_file_checksums) == 0
        )

    @property
    def has_checksums_to_match(self) -> bool:
        """Determine if we have checksums to match against.

        This decides whether or not we
        call `hermeto.core.checksum.must_match_any_checksum()`
        """
        return len(self.checksums_to_match) > 0

    @property
    def download_info(self) -> dict[str, Any]:
        """Only necessary attributes to process download information."""
        return {
            "package": self.name,
            "version": self.version,
            "path": self.path,
        }


def fetch_pip_source(request: Request) -> RequestOutput:
    """Resolve and fetch pip dependencies for the given request."""
    components: list[Component] = []
    project_files: list[ProjectFile] = []
    environment_variables: list[EnvironmentVariable] = []

    if request.pip_packages:
        environment_variables = [
            EnvironmentVariable(name="PIP_FIND_LINKS", value="${output_dir}/deps/pip"),
            EnvironmentVariable(name="PIP_NO_INDEX", value="true"),
        ]

    for package in request.pip_packages:
        path_within_root = request.source_dir.join_within_root(package.path)
        info = _resolve_pip(
            path_within_root,
            request.output_dir,
            package.requirements_files,
            package.requirements_build_files,
            package.allow_binary,
        )
        purl = _generate_purl_main_package(info["package"], path_within_root)
        components.append(
            Component(name=info["package"]["name"], version=info["package"]["version"], purl=purl)
        )

        for dependency in info["dependencies"]:
            purl = _generate_purl_dependency(dependency)
            version = dependency["version"] if dependency["kind"] == "pypi" else None

            missing_hash_in_file: frozenset = frozenset()
            if dependency["missing_req_file_checksum"]:
                missing_hash_in_file = frozenset({dependency["requirement_file"]})

            pip_package_binary = False
            if dependency["package_type"] == "wheel":
                pip_package_binary = True

            pip_build_dependency = False
            if dependency["build_dependency"] is True:
                pip_build_dependency = True

            components.append(
                Component(
                    name=dependency["name"],
                    version=version,
                    purl=purl,
                    properties=PropertySet(
                        missing_hash_in_file=missing_hash_in_file,
                        pip_package_binary=pip_package_binary,
                        pip_build_dependency=pip_build_dependency,
                    ).to_properties(),
                )
            )

        replaced_requirements_files = map(_replace_external_requirements, info["requirements"])
        project_files.extend(filter(None, replaced_requirements_files))

    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=environment_variables,
        project_files=project_files,
    )


def _generate_purl_main_package(package: dict[str, Any], package_path: RootedPath) -> str:
    """Get the purl for this package."""
    type = "pypi"
    name = package["name"]
    version = package["version"]
    url = get_repo_id(package_path.root).as_vcs_url_qualifier()
    qualifiers = {"vcs_url": url}
    if package_path.subpath_from_root != Path("."):
        subpath = package_path.subpath_from_root.as_posix()
    else:
        subpath = None

    purl = PackageURL(
        type=type,
        name=name,
        version=version,
        qualifiers=qualifiers,
        subpath=subpath,
    )

    return purl.to_string()


def _generate_purl_dependency(package: dict[str, Any]) -> str:
    """Get the purl for this dependency."""
    type = "pypi"
    name = package["name"]
    dependency_kind = package.get("kind", None)
    version = None
    qualifiers: Optional[dict[str, str]] = None

    if dependency_kind == "pypi":
        version = package["version"]
        index_url = package["index_url"]
        if index_url.rstrip("/") != pypi_simple.PYPI_SIMPLE_ENDPOINT.rstrip("/"):
            qualifiers = {"repository_url": index_url}
    elif dependency_kind == "vcs":
        qualifiers = {"vcs_url": package["version"]}
    elif dependency_kind == "url":
        parsed_url = urllib.parse.urldefrag(package["version"])
        fragments: dict[str, list[str]] = urllib.parse.parse_qs(parsed_url.fragment)
        checksum: str = fragments["cachito_hash"][0]
        qualifiers = {"download_url": parsed_url.url, "checksum": checksum}
    else:
        # Should not happen
        raise RuntimeError(f"Unexpected requirement kind: {dependency_kind}")

    purl = PackageURL(
        type=type,
        name=name,
        version=version,
        qualifiers=qualifiers,
    )

    return purl.to_string()


def _infer_package_name_from_origin_url(package_dir: RootedPath) -> str:
    try:
        repo_id = get_repo_id(package_dir.root)
    except UnsupportedFeature:
        raise PackageRejected(
            reason="Unable to infer package name from origin URL",
            solution=(
                "Provide valid metadata in the package files or ensure"
                "the git repository has an 'origin' remote with a valid URL."
            ),
            docs=PIP_METADATA_DOC,
        )

    repo_name = Path(repo_id.parsed_origin_url.path).stem
    resolved_name = Path(repo_name).joinpath(package_dir.subpath_from_root)
    return canonicalize_name(str(resolved_name).replace("/", "-")).strip("-.")


def _extract_metadata_from_config_files(
    package_dir: RootedPath,
) -> tuple[Optional[str], Optional[str]]:
    """
    Extract package name and version in the following order.

    1. pyproject.toml
    2. setup.py
    3. setup.cfg

    Note: version is optional in the SBOM, but name is required
    """
    pyproject_toml = PyProjectTOML(package_dir)
    if pyproject_toml.exists():
        log.debug("Checking pyproject.toml for metadata")
        name = pyproject_toml.get_name()
        version = pyproject_toml.get_version()

        if name:
            return name, version

    setup_py = SetupPY(package_dir)
    if setup_py.exists():
        log.debug("Checking setup.py for metadata")
        name = setup_py.get_name()
        version = setup_py.get_version()

        if name:
            return name, version

    setup_cfg = SetupCFG(package_dir)
    if setup_cfg.exists():
        log.debug("Checking setup.cfg for metadata")
        name = setup_cfg.get_name()
        version = setup_cfg.get_version()

        if name:
            return name, version

    return None, None


def _get_pip_metadata(package_dir: RootedPath) -> tuple[str, Optional[str]]:
    """Attempt to retrieve name and version of a pip package."""
    name, version = _extract_metadata_from_config_files(package_dir)

    if not name:
        name = _infer_package_name_from_origin_url(package_dir)

    log.info("Resolved name %s for package at %s", name, package_dir)
    if version:
        log.info("Resolved version %s for package at %s", version, package_dir)
    else:
        log.warning("Could not resolve version for package at %s", package_dir)

    return name, version


def _any_to_version(obj: Any) -> str:
    """
    Convert any python object to a version string.

    https://github.com/pypa/setuptools/blob/ba209a15247b9578d565b7491f88dc1142ba29e4/setuptools/config.py#L535

    :param obj: object to convert to version
    """
    version = obj

    if not isinstance(version, str):
        if hasattr(version, "__iter__"):
            version = ".".join(map(str, version))
        else:
            version = str(version)

    return canonicalize_version(version, strip_trailing_zero=False)


def _get_top_level_attr(
    body: list[ast.stmt], attr_name: str, before_line: Optional[int] = None
) -> Any:
    """
    Get attribute from module if it is defined at top level and assigned to a literal expression.

    https://github.com/pypa/setuptools/blob/ba209a15247b9578d565b7491f88dc1142ba29e4/setuptools/config.py#L36

    Note that this approach is not equivalent to the setuptools one - setuptools looks for the
    attribute starting from the top, we start at the bottom. Arguably, starting at the bottom
    makes more sense, but it should not make any real difference in practice.

    :param body: The body of an AST node
    :param attr_name: Name of attribute to search for
    :param before_line: Only look for attributes defined before this line

    :rtype: anything that can be expressed as a literal ("primitive" types, collections)
    :raises AttributeError: If attribute not found
    :raises ValueError: If attribute assigned to something that is not a literal
    """
    try:
        return next(
            ast.literal_eval(node.value)
            for node in reversed(body)
            if (before_line is None or node.lineno < before_line) and isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id == attr_name
        )
    except ValueError:
        raise ValueError(f"{attr_name!r} is not assigned to a literal expression")
    except StopIteration:
        raise AttributeError(f"{attr_name!r} not found")


class SetupFile(ABC):
    """Abstract base class for pyproject.toml, setup.cfg, and setup.py handling."""

    def __init__(self, top_dir: RootedPath, file_name: str) -> None:
        """
        Initialize a SetupFile.

        :param top_dir: Path to root of project directory
        :param file_name: Name of Python setup file, expected to be in the root directory
        """
        self._top_dir = top_dir
        self._file_name = file_name

    @property
    def _setup_file(self) -> RootedPath:
        return self._top_dir.join_within_root(self._file_name)

    def exists(self) -> bool:
        """Check if file exists."""
        return self._setup_file.path.is_file()

    @abstractmethod
    def get_name(self) -> Optional[str]:
        """Attempt to determine the package name. Should only be called if file exists."""

    @abstractmethod
    def get_version(self) -> Optional[str]:
        """Attempt to determine the package version. Should only be called if file exists."""


class PyProjectTOML(SetupFile):
    """Extract project name and version from a pyproject.toml file."""

    def __init__(self, top_dir: RootedPath) -> None:
        """
        Initialize a PyProjectTOML.

        :param top_dir: Path to root of project directory
        """
        super().__init__(top_dir, "pyproject.toml")

    def get_name(self) -> Optional[str]:
        """Get project name if present."""
        try:
            return self._parsed_toml["project"]["name"]
        except KeyError:
            log.warning("No project.name in pyproject.toml")
            return None

    def get_version(self) -> Optional[str]:
        """Get project version if present."""
        try:
            return self._parsed_toml["project"]["version"]
        except KeyError:
            log.warning("No project.version in pyproject.toml")
            return None

    @functools.cached_property
    def _parsed_toml(self) -> dict[str, Any]:
        try:
            log.debug("Parsing pyproject.toml at %r", str(self._setup_file))
            return tomlkit.parse(self._setup_file.path.read_text())
        # no exceptions are exposed in the public API reference for tomlkit
        # check https://github.com/python-poetry/tomlkit/issues/399
        except Exception as e:
            log.error("Failed to parse pyproject.toml: %s", e)
            return {}


class SetupCFG(SetupFile):
    """
    Parse metadata.name and metadata.version from a setup.cfg file.

    Aims to match setuptools behaviour as closely as possible, but does make
    some compromises (such as never executing arbitrary Python code).
    """

    # Valid Python name - any sequence of \w characters that does not start with a number
    _name_re = re.compile(r"[^\W\d]\w*")

    def __init__(self, top_dir: RootedPath) -> None:
        """
        Initialize a SetupCFG.

        :param top_dir: Path to root of project directory
        """
        super().__init__(top_dir, "setup.cfg")

    def get_name(self) -> Optional[str]:
        """Get metadata.name if present."""
        name = self._get_option("metadata", "name")
        if not name:
            log.info("No metadata.name in setup.cfg")
            return None

        log.info("Found metadata.name in setup.cfg: %r", name)
        return name

    def get_version(self) -> Optional[str]:
        """
        Get metadata.version if present.

        Partially supports the file: directive (setuptools supports multiple files
        as an argument to file:, this makes no sense for version).

        Partially supports the attr: directive (will only work if the attribute
        being referenced is assigned to a literal expression).
        """
        version = self._get_option("metadata", "version")
        if not version:
            log.info("No metadata.version in setup.cfg")
            return None

        log.debug("Resolving metadata.version in setup.cfg from %r", version)
        version = self._resolve_version(version)
        if not version:
            # Falsy values also count as "failed to resolve" (0, None, "", ...)
            log.info("Failed to resolve metadata.version in setup.cfg")
            return None

        version = _any_to_version(version)
        log.info("Found metadata.version in setup.cfg: %r", version)
        return version

    @functools.cached_property
    def _parsed(self) -> Optional[configparser.ConfigParser]:
        """
        Try to parse config file, return None if parsing failed.

        Will not parse file (or try to) more than once.
        """
        log.debug("Parsing setup.cfg at %r", str(self._setup_file))
        parsed = configparser.ConfigParser()

        with self._setup_file.path.open() as f:
            try:
                parsed.read_file(f)
                return parsed
            except configparser.Error as e:
                log.error("Failed to parse setup.cfg: %s", e)
                return None

    def _get_option(self, section: str, option: str) -> Optional[str]:
        """Get option from config section, return None if option missing or file invalid."""
        if self._parsed is None:
            return None
        try:
            return self._parsed.get(section, option)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return None

    def _resolve_version(self, raw_version: str) -> Optional[str]:
        """Attempt to resolve the version attribute."""
        if raw_version.startswith("file:"):
            file_arg = raw_version[len("file:") :].strip()
            version = self._read_version_from_file(file_arg)
        elif raw_version.startswith("attr:"):
            attr_arg = raw_version[len("attr:") :].strip()
            version = self._read_version_from_attr(attr_arg)
        else:
            version = raw_version
        return version

    def _read_version_from_file(self, file_path: str) -> Optional[str]:
        """Read version from file."""
        version_file = self._top_dir.join_within_root(file_path)
        if version_file.path.is_file():
            version = version_file.path.read_text().strip()
            log.debug("Read version from %r: %r", file_path, version)
            return version
        else:
            log.error("Version file %r does not exist or is not a file", file_path)
            return None

    def _read_version_from_attr(self, attr_spec: str) -> Optional[str]:
        """
        Read version from module attribute.

        Like setuptools, will try to find the attribute by looking for Python
        literals in the AST of the module. Unlike setuptools, will not execute
        the module if this fails.

        https://github.com/pypa/setuptools/blob/ba209a15247b9578d565b7491f88dc1142ba29e4/setuptools/config.py#L354

        :param attr_spec: "import path" of attribute, e.g. package.version.__version__
        """
        module_name, _, attr_name = attr_spec.rpartition(".")
        if not module_name:
            # Assume current directory is a package, look for attribute in __init__.py
            module_name = "__init__"

        log.debug("Attempting to find attribute %r in %r", attr_name, module_name)

        module_file = self._find_module(module_name, self._get_package_dirs())
        if module_file is not None:
            log.debug("Found module %r at %r", module_name, str(module_file))
        else:
            log.error("Module %r not found", module_name)
            return None

        try:
            module_ast = ast.parse(module_file.path.read_text(), module_file.path.name)
        except SyntaxError as e:
            log.error("Syntax error when parsing module: %s", e)
            return None

        try:
            version = _get_top_level_attr(module_ast.body, attr_name)
            log.debug("Found attribute %r in %r: %r", attr_name, module_name, version)
            return version
        except (AttributeError, ValueError) as e:
            log.error("Could not find attribute in %r: %s", module_name, e)
            return None

    def _find_module(
        self, module_name: str, package_dir: Optional[dict[str, str]] = None
    ) -> Optional[RootedPath]:
        """
        Try to find a module in the project directory and return path to source file.

        :param module_name: "import path" of module
        :param package_dir: same semantics as options.package_dir in setup.cfg
        """
        module_path = self._convert_to_path(module_name)
        root_module = module_path.parts[0]

        package_dir = package_dir or {}

        if root_module in package_dir:
            custom_path = Path(package_dir[root_module])
            log.debug("Custom path set for root module %r: %r", root_module, str(custom_path))
            # Custom path replaces the root module
            module_path = custom_path.joinpath(*module_path.parts[1:])
        elif "" in package_dir:
            custom_path = Path(package_dir[""])
            log.debug("Custom path set for all root modules: %r", str(custom_path))
            # Custom path does not replace the root module
            module_path = custom_path / module_path

        package_init = self._top_dir.join_within_root(module_path).join_within_root("__init__.py")
        if package_init.path.is_file():
            return package_init

        module_py = self._top_dir.join_within_root(f"{module_path}.py")
        if module_py.path.is_file():
            return module_py

        return None

    def _convert_to_path(self, module_name: str) -> Path:
        """Check that module name is valid and covert to file path."""
        parts = module_name.split(".")
        if not parts[0]:
            # Relative import (supported only to the extent that one leading '.' is ignored)
            parts.pop(0)
        if not all(self._name_re.fullmatch(part) for part in parts):
            raise PackageRejected(
                f"{module_name!r} is not an accepted module name",
                solution=None,
            )
        return Path(*parts)

    def _get_package_dirs(self) -> Optional[dict[str, str]]:
        """
        Get options.package_dir and convert to dict if present.

        https://github.com/pypa/setuptools/blob/ba209a15247b9578d565b7491f88dc1142ba29e4/setuptools/config.py#L264
        """
        package_dir_value = self._get_option("options", "package_dir")
        if package_dir_value is None:
            return None

        if "\n" in package_dir_value:
            package_items: Iterable[str] = package_dir_value.splitlines()
        else:
            package_items = package_dir_value.split(",")

        # Strip whitespace and discard empty values
        package_items = filter(bool, (p.strip() for p in package_items))

        package_dirs: dict[str, str] = {}
        for item in package_items:
            package, sep, p_dir = item.partition("=")
            if sep:
                # Otherwise value was malformed ('=' was missing)
                package_dirs[package.strip()] = p_dir.strip()

        return package_dirs


@dataclass(frozen=True)
class ASTPathElement:
    """An element of AST path."""

    node: ast.AST
    attr: str  # Child node is (in) this field
    index: Optional[int] = None  # If field is a list, this is the index of the child node

    @property
    def field(self) -> Any:
        """Return field referenced by self.attr."""
        return getattr(self.node, self.attr)

    def field_is_body(self) -> bool:
        r"""
        Check if the field is a body (a list of statement nodes).

        All 'stmt*' attributes here: https://docs.python.org/3/library/ast.html#abstract-grammar

        Check with the following command:

            curl 'https://docs.python.org/3/library/ast.html#abstract-grammar' |
            grep -E 'stmt\* \w+' --only-matching |
            sort -u
        """
        return self.attr in ("body", "orelse", "finalbody")

    def __str__(self) -> str:
        """Make string representation of path element: <type>(<lineno>).<field>[<index>]."""
        s = self.node.__class__.__name__
        if hasattr(self.node, "lineno"):
            s += f"(#{self.node.lineno})"
        s += f".{self.attr}"
        if self.index is not None:
            s += f"[{self.index}]"
        return s


@dataclass(frozen=True)
class SetupBranch:
    """Setup call node, path to setup call from root node."""

    call_node: ast.Call
    node_path: list[ASTPathElement]


class SetupPY(SetupFile):
    """
    Find the setup() call in a setup.py file and extract the `name` and `version` kwargs.

    Will only work for very basic use cases - value of keyword argument must be a literal
    expression or a variable assigned to a literal expression.

    Some supported examples:

    1) trivial

        from setuptools import setup

        setup(name="foo", version="1.0.0")

    2) if __main__

        import setuptools

        name = "foo"
        version = "1.0.0"

        if __name__ == "__main__":
            setuptools.setup(name=name, version=version)

    3) my_setup()

        import setuptools

        def my_setup():
            name = "foo"
            version = "1.0.0"

            setuptools.setup(name=name, version=version)

        my_setup()

    For examples 2) and 3), we do not actually resolve any conditions or check that the
    function containing the setup() call is eventually executed. We simply assume that,
    this being the setup.py script, setup() will end up being called no matter what.
    """

    def __init__(self, top_dir: RootedPath) -> None:
        """
        Initialize a SetupPY.

        :param top_dir: Path to root of project directory
        """
        super().__init__(top_dir, "setup.py")

    def get_name(self) -> Optional[str]:
        """Attempt to extract package name from setup.py."""
        name = self._get_setup_kwarg("name")
        if not name or not isinstance(name, str):
            log.info(
                "Name in setup.py was either not found, or failed to resolve to a valid string"
            )
            return None

        log.info("Found name in setup.py: %r", name)
        return name

    def get_version(self) -> Optional[str]:
        """
        Attempt to extract package version from setup.py.

        As of setuptools version 49.2.1, there is no special logic for passing
        an iterable as version in setup.py. Unlike name, however, it does support
        non-string arguments (except tuples with len() != 1, those break horribly).

        https://github.com/pypa/setuptools/blob/5e60dc50e540a942aeb558aabe7d92ab7eb13d4b/setuptools/dist.py#L462

        Rather than trying to keep edge cases consistent with setuptools, treat them
        consistently in our application.
        """
        version = self._get_setup_kwarg("version")
        if not version:
            # Only truthy values are valid, not any of (0, None, "", ...)
            log.info(
                "Version in setup.py was either not found, or failed to resolve to a valid value"
            )
            return None

        version = _any_to_version(version)
        log.info("Found version in setup.py: %r", version)
        return version

    @functools.cached_property
    def _ast(self) -> Optional[ast.AST]:
        """Try to parse the AST."""
        log.debug("Parsing setup.py at %r", str(self._setup_file))
        try:
            return ast.parse(self._setup_file.path.read_text(), self._setup_file.path.name)
        except SyntaxError as e:
            log.error("Syntax error when parsing setup.py: %s", e)
            return None

    @functools.cached_property
    def _setup_branch(self) -> Optional[SetupBranch]:
        """
        Find setup() call anywhere in the file, return setup branch.

        The file is expected to contain only one setup call. If there are two or more,
        we cannot safely determine which one gets called. In such a case, we will simply
        find and process the first one.

        If setup call not found, return None.
        """
        if self._ast is None:
            return None

        setup_call, setup_path = self._find_setup_call(self._ast)
        if setup_call is None:
            log.error("File does not seem to have a setup call")
            return None

        setup_path.reverse()  # Path is in reverse order
        log.debug("Found setup call on line %s", setup_call.lineno)
        path_repr = " -> ".join(map(str, setup_path))
        log.debug("Pseudo-path: %s", path_repr)
        return SetupBranch(setup_call, setup_path)

    def _find_setup_call(
        self, root_node: ast.AST
    ) -> tuple[Optional[ast.Call], list[ASTPathElement]]:
        """
        Find setup() or setuptools.setup() call anywhere in or under root_node.

        Return call node and path from root node to call node (reversed).
        """
        if self._is_setup_call(root_node):
            return root_node, []

        for name, value in ast.iter_fields(root_node):
            # Value is a node
            if isinstance(value, ast.AST):
                setup_call, setup_path = self._find_setup_call(value)
                if setup_call is not None:
                    setup_path.append(ASTPathElement(root_node, name))
                    return setup_call, setup_path
            # Value is a list of nodes (use any(), nodes will never be mixed with non-nodes)
            elif isinstance(value, list) and any(isinstance(x, ast.AST) for x in value):
                for i, node in enumerate(value):
                    setup_call, setup_path = self._find_setup_call(node)
                    if setup_call is not None:
                        setup_path.append(ASTPathElement(root_node, name, i))
                        return setup_call, setup_path

        return None, []  # No setup call under root_node

    def _is_setup_call(self, node: ast.AST) -> "TypeGuard[ast.Call]":
        """Check if node is setup() or setuptools.setup() call."""
        if not isinstance(node, ast.Call):
            return False

        fn = node.func
        return (isinstance(fn, ast.Name) and fn.id == "setup") or (
            isinstance(fn, ast.Attribute)
            and fn.attr == "setup"
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "setuptools"
        )

    def _get_setup_kwarg(self, arg_name: str) -> Optional[Any]:
        """
        Find setup() call, extract specified argument from keyword arguments.

        If argument value is a variable, then what we do is only a very loose approximation
        of how Python resolves variables. None of the following examples will work:

        1) any indented blocks (unless setup() call appears under the same block)

            with x:
                name = "foo"

            setup(name=name)

        2) late binding

            def my_setup():
                setup(name=name)

            name = "foo"

            my_setup()

        The rationale for not supporting these cases:
        - it is difficult
        - there is no use case for 1) which is both valid and possible to resolve safely
        - 2) seems like a bad enough practice to justify ignoring it
        """
        if self._setup_branch is None:
            return None

        for kw in self._setup_branch.call_node.keywords:
            if kw.arg == arg_name:
                try:
                    value = ast.literal_eval(kw.value)
                    log.debug("setup kwarg %r is a literal: %r", arg_name, value)
                    return value
                except ValueError:
                    pass

                if isinstance(kw.value, ast.Name):
                    log.debug("setup kwarg %r looks like a variable", arg_name)
                    return self._get_variable(
                        kw.value.id, self._setup_branch.call_node, self._setup_branch.node_path
                    )

                expr_type = kw.value.__class__.__name__
                log.error("setup kwarg %r is an unsupported expression: %s", arg_name, expr_type)
                return None

        log.debug("setup kwarg %r not found", arg_name)
        return None

    def _get_variable(
        self, var_name: str, call_node: ast.Call, path_to_call_node: list[ASTPathElement]
    ) -> Optional[Any]:
        """Walk back up the AST along setup branch, look for first assignment of variable."""
        lineno = call_node.lineno

        log.debug("Backtracking up the AST from line %s to find variable %r", lineno, var_name)

        for elem in filter(ASTPathElement.field_is_body, reversed(path_to_call_node)):
            try:
                value = _get_top_level_attr(elem.field, var_name, lineno)
                log.debug("Found variable %r: %r", var_name, value)
                return value
            except ValueError as e:
                log.error("Variable cannot be resolved: %s", e)
                return None
            except AttributeError:
                pass

        log.error("Variable %r not found along the setup call branch", var_name)
        return None


class PipRequirementsFile:
    """Parse requirements from a pip requirements file."""

    # Comment lines start with optional leading spaces followed by "#"
    LINE_COMMENT = re.compile(r"(^|\s)#.*$")

    # Options allowed in a requirements file. The values represent whether or not the option
    # requires a value.
    # https://pip.pypa.io/en/stable/reference/pip_install/#requirements-file-format
    OPTIONS = {
        "--constraint": True,
        "--editable": False,  # The required value is the requirement itself, not a parameter
        "--extra-index-url": True,
        "--find-links": True,
        "--index-url": True,
        "--no-binary": True,
        "--no-index": False,
        "--only-binary": True,
        "--pre": False,
        "--prefer-binary": False,
        "--require-hashes": False,
        "--requirement": True,
        "--trusted-host": True,
        "--use-feature": True,
        "-c": True,
        "-e": False,  # The required value is the requirement itself, not a parameter
        "-f": True,
        "--hash": True,
        "-i": True,
        "-r": True,
    }

    # Options that are specific to a single requirement in the requirements file. All other
    # options apply to all the requirements.
    REQUIREMENT_OPTIONS = {"-e", "--editable", "--hash"}

    def __init__(self, file_path: RootedPath) -> None:
        """Initialize a PipRequirementsFile.

        :param RootedPath file_path: the full path to the requirements file
        """
        self.file_path = file_path

    @classmethod
    def from_requirements_and_options(
        cls, requirements: list["PipRequirement"], options: list[str]
    ) -> "PipRequirementsFile":
        """Create a new PipRequirementsFile instance from given parameters.

        :param list requirements: list of PipRequirement instances
        :param list options: list of strings of global options
        :return: new instance of PipRequirementsFile
        """
        new_instance = cls(RootedPath("/"))
        new_instance._parsed = {"requirements": list(requirements), "options": list(options)}
        return new_instance

    def write(self, file_obj: IO[str]) -> None:
        """Write the options and requirements to a file."""
        if self.options:
            file_obj.write(" ".join(self.options))
            file_obj.write("\n")
        for requirement in self.requirements:
            file_obj.write(str(requirement))
            file_obj.write("\n")

    def generate_file_content(self) -> str:
        """Generate the file content from the parsed options and requirements."""
        fileobj = io.StringIO()
        self.write(fileobj)
        return fileobj.getvalue()

    @property
    def requirements(self) -> list["PipRequirement"]:
        """Return a list of PipRequirement objects."""
        return self._parsed["requirements"]

    @property
    def options(self) -> list[str]:
        """Return a list of options."""
        return self._parsed["options"]

    @functools.cached_property
    def _parsed(self) -> dict[str, Any]:
        """Return the parsed requirements file.

        :return: a dict with the keys ``requirements`` and ``options``
        """
        parsed: dict[str, list[Union[str, PipRequirement]]] = {"requirements": [], "options": []}

        for line in self._read_lines():
            (
                global_options,
                requirement_options,
                requirement_line,
            ) = self._split_options_and_requirement(line)
            if global_options:
                parsed["options"].extend(global_options)

            if requirement_line:
                parsed["requirements"].append(
                    PipRequirement.from_line(requirement_line, requirement_options)
                )

        return parsed

    def _read_lines(self) -> Iterator[str]:
        """Read and yield the lines from the requirements file.

        Lines ending in the line continuation character are joined with the next line.
        Comment lines are ignored.
        """
        buffered_line: list[str] = []

        with open(self.file_path) as f:
            for line in f.read().splitlines():
                if not line.endswith("\\"):
                    buffered_line.append(line)
                    new_line = "".join(buffered_line)
                    new_line = self.LINE_COMMENT.sub("", new_line).strip()
                    if new_line:
                        yield new_line
                    buffered_line = []
                else:
                    buffered_line.append(line.rstrip("\\"))

        # Last line ends in "\"
        if buffered_line:
            yield "".join(buffered_line)

    def _split_options_and_requirement(self, line: str) -> tuple[list[str], list[str], str]:
        """Split global and requirement options from the requirement line.

        :param str line: requirement line from the requirements file
        :return: three-item tuple where the first item is a list of global options, the
            second item a list of requirement options, and the last item a str of the
            requirement without any options.
        """
        global_options: list[str] = []
        requirement_options: list[str] = []
        requirement: list[str] = []

        # Indicates the option must be followed by a value
        _require_value = False
        # Reference to either global_options or requirement_options list
        _context_options = []

        for part in line.split():
            if _require_value:
                _context_options.append(part)
                _require_value = False
            elif part.startswith("-"):
                option = None
                value = None
                if "=" in part:
                    option, value = part.split("=", 1)
                else:
                    option = part

                if option not in self.OPTIONS:
                    raise UnexpectedFormat(f"Unknown requirements file option {part!r}")

                _require_value = self.OPTIONS[option]

                if option in self.REQUIREMENT_OPTIONS:
                    _context_options = requirement_options
                else:
                    _context_options = global_options

                if value and not _require_value:
                    raise UnexpectedFormat(
                        f"Unexpected value for requirements file option {part!r}"
                    )

                _context_options.append(option)
                if value:
                    _context_options.append(value)
                    _require_value = False
            else:
                requirement.append(part)

        if _require_value:
            raise UnexpectedFormat(
                f"Requirements file option {_context_options[-1]!r} requires a value"
            )

        if requirement_options and not requirement:
            raise UnexpectedFormat(
                f"Requirements file option(s) {requirement_options!r} can only be applied to a "
                "requirement",
            )

        return global_options, requirement_options, " ".join(requirement)


class PipRequirement:
    """Parse a requirement and its options from a requirement line."""

    # Regex used to determine if a direct access requirement specifies a
    # package name, e.g. "name @ https://..."
    HAS_NAME_IN_DIRECT_ACCESS_REQUIREMENT = re.compile(r"@.+://")

    def __init__(self) -> None:
        """Initialize a PipRequirement."""
        # The package name after it has been processed by setuptools, e.g. "_" are replaced
        # with "-"
        self.package: str = ""
        # The package name as defined in the requirement line
        self.raw_package: str = ""
        self.extras: set[str] = set()
        self.version_specs: list[tuple[str, str]] = []
        self.environment_marker: Optional[str] = None
        self.hashes: list[str] = []
        self.qualifiers: dict[str, str] = {}

        self.kind: Literal["pypi", "url", "vcs"]
        self.download_line: str = ""

        self.options: list[str] = []

        self._url: Any = None

    @property
    def url(self) -> str:
        """Extract the URL from the download line of a VCS or URL requirement."""
        if self._url is None:
            if self.kind not in ("url", "vcs"):
                raise ValueError(f"Cannot extract URL from {self.kind} requirement")
            # package @ url ; environment_marker
            parts = self.download_line.split()
            self._url = parts[2]

        return self._url

    def __str__(self) -> str:
        """Return the string representation of the PipRequirement."""
        line: list[str] = []
        line.extend(self.options)
        line.append(self.download_line)
        line.extend(f"--hash={h}" for h in self.hashes)
        return " ".join(line)

    def copy(
        self, url: Optional[str] = None, hashes: Optional[list[str]] = None
    ) -> "PipRequirement":
        """Duplicate this instance of PipRequirement.

        :param str url: set a new direct access URL for the requirement. If provided, the
            new requirement is always of ``url`` kind.
        :param list hashes: overwrite hash values for the new requirement
        :return: new PipRequirement instance
        """
        options = list(self.options)
        download_line = self.download_line
        if url:
            download_line_parts: list[str] = []
            download_line_parts.append(self.raw_package)
            download_line_parts.append("@")

            qualifiers_line = "&".join(f"{key}={value}" for key, value in self.qualifiers.items())
            if qualifiers_line:
                download_line_parts.append(f"{url}#{qualifiers_line}")
            else:
                download_line_parts.append(url)

            if self.environment_marker:
                download_line_parts.append(";")
                download_line_parts.append(self.environment_marker)

            download_line = " ".join(download_line_parts)

            # Pip does not support editable mode for requirements installed via an URL, only
            # via VCS. Remove this option to avoid errors later on.
            options = list(set(self.options) - {"-e", "--editable"})
            if self.options != options:
                log.warning(
                    "Removed editable option when copying the requirement %r", self.raw_package
                )

        requirement = self.__class__()
        # Extras are incorrectly treated as part of the URL itself. If we're setting
        # the URL, they must be skipped.
        if not url:
            requirement.extras = set(self.extras)
        requirement.package = self.package
        requirement.raw_package = self.raw_package
        # Version specs are ignored by pip when applied to a URL, let's do the same.
        requirement.version_specs = [] if url else list(self.version_specs)
        requirement.environment_marker = self.environment_marker
        requirement.hashes = list(hashes or self.hashes)
        requirement.qualifiers = dict(self.qualifiers)
        requirement.kind = "url" if url else self.kind
        requirement.download_line = download_line
        requirement.options = options

        return requirement

    @classmethod
    def from_line(cls, line: str, options: list[str]) -> "PipRequirement":
        """Create an instance of PipRequirement from the given requirement and its options.

        Only ``url`` and ``vcs`` direct access requirements are supported. ``file`` is not.

        :param str line: the requirement line
        :param str list: the options associated with the requirement
        :return: PipRequirement instance
        """
        to_be_parsed = line
        qualifiers: dict[str, str] = {}
        requirement = cls()

        if not (direct_access_kind := cls._assess_direct_access_requirement(line)):
            requirement.kind = "pypi"
        else:
            requirement.kind = direct_access_kind
            to_be_parsed, qualifiers = cls._adjust_direct_access_requirement(
                to_be_parsed, cls.HAS_NAME_IN_DIRECT_ACCESS_REQUIREMENT
            )

        try:
            req = Requirement(to_be_parsed)
        except InvalidRequirement as exc:
            # see https://github.com/pypa/setuptools/pull/2137
            raise UnexpectedFormat(f"Unable to parse the requirement {to_be_parsed!r}: {exc}")

        hashes, options = cls._split_hashes_from_options(options)

        requirement.download_line = to_be_parsed
        requirement.options = options
        requirement.package = canonicalize_name(req.name)
        requirement.raw_package = req.name
        requirement.version_specs = [(spec.operator, spec.version) for spec in req.specifier]
        requirement.extras = req.extras
        requirement.environment_marker = str(req.marker) if req.marker else None
        requirement.hashes = hashes
        requirement.qualifiers = qualifiers

        return requirement

    @staticmethod
    def _assess_direct_access_requirement(line: str) -> Optional[Literal["url", "vcs"]]:
        """Determine if the line contains a direct access requirement.

        :param str line: the requirement line
        :return: two-item tuple where the first item is the kind of direct access requirement,
            e.g. "vcs", and the second item is a bool indicating if the requirement is a
            direct access requirement
        """
        URL_SCHEMES = {"http", "https", "ftp"}
        VCS_SCHEMES = {
            "bzr",
            "bzr+ftp",
            "bzr+http",
            "bzr+https",
            "git",
            "git+ftp",
            "git+http",
            "git+https",
            "hg",
            "hg+ftp",
            "hg+http",
            "hg+https",
            "svn",
            "svn+ftp",
            "svn+http",
            "svn+https",
        }
        direct_access_kind: Literal["url", "vcs"]

        if ":" not in line:
            return None
        # Extract the scheme from the line and strip off the package name if needed
        # e.g. name @ https://...
        scheme_parts = line.split(":", 1)[0].split("@")
        if len(scheme_parts) > 2:
            raise UnexpectedFormat(
                f"Unable to extract scheme from direct access requirement {line!r}"
            )
        scheme = scheme_parts[-1].lower().strip()

        if scheme in URL_SCHEMES:
            direct_access_kind = "url"
        elif scheme in VCS_SCHEMES:
            direct_access_kind = "vcs"
        else:
            raise UnsupportedFeature(
                f"Direct references with {scheme!r} scheme are not supported, " f"{line!r}"
            )

        return direct_access_kind

    @staticmethod
    def _adjust_direct_access_requirement(
        line: str, direct_ref_pattern: Pattern[str]
    ) -> tuple[str, dict[str, str]]:
        """Modify the requirement line so it can be parsed and extract qualifiers.

        :param str line: a direct access requirement line
        :param str direct_ref_pattern: a Regex used to determine if a requirement
            specifies a package name
        :return: two-item tuple where the first item is a modified direct access requirement
            line that can be parsed, and the second item is a dict of the
            qualifiers extracted from the direct access URL
        """
        package_name = None
        qualifiers: dict[str, str] = {}
        url = line
        environment_marker = None

        if direct_ref_pattern.search(line):
            package_name, url = line.split("@", 1)

        # For direct access requirements, a space is needed after the semicolon.
        if "; " in url:
            url, environment_marker = url.split("; ", 1)

        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.fragment:
            for section in parsed_url.fragment.split("&"):
                if "=" in section:
                    attr, value = section.split("=", 1)
                    value = urllib.parse.unquote(value)
                    qualifiers[attr] = value
                    if attr == "egg":
                        # Use the egg name as the package name to avoid ambiguity when both are
                        # provided. This matches the behavior of "pip install".
                        package_name = value

        if not package_name:
            raise UnsupportedFeature(
                reason=(
                    f"Dependency name could not be determined from the requirement {line!r} "
                    f"({APP_NAME} needs the name to be explicitly declared)"
                ),
                solution="Please specify the name of the dependency: <name> @ <url>",
                docs=PIP_EXTERNAL_DEPS_DOC,
            )

        requirement_parts = [package_name.strip(), "@", url.strip()]
        if environment_marker:
            # Although a space before the semicolon is not needed by pip, it is needed when
            # using packaging later on.
            requirement_parts.append(";")
            requirement_parts.append(environment_marker.strip())
        return " ".join(requirement_parts), qualifiers

    @staticmethod
    def _split_hashes_from_options(options: list[str]) -> tuple[list[str], list[str]]:
        """Separate the --hash options from the given options.

        :param list options: requirement options
        :return: two-item tuple where the first item is a list of hashes, and the second item
            is a list of options without any ``--hash`` options
        """
        hashes: list[str] = []
        reduced_options: list[str] = []
        is_hash = False

        for item in options:
            if is_hash:
                hashes.append(item)
                is_hash = False
                continue

            is_hash = item == "--hash"
            if not is_hash:
                reduced_options.append(item)

        return hashes, reduced_options


def _process_req(
    req: PipRequirement,
    requirements_file: PipRequirementsFile,
    pip_deps_dir: RootedPath,
    download_info: dict[str, Any],
    dpi: Optional[DistributionPackageInfo] = None,
) -> dict[str, Any]:
    download_info["kind"] = req.kind
    download_info["requirement_file"] = str(requirements_file.file_path.subpath_from_root)
    download_info["missing_req_file_checksum"] = True
    # "package_type" is *only* needed for PyPI deps
    download_info["package_type"] = ""

    def _checksum_must_match_or_path_unlink(
        path: Path, checksum_info: Iterable[ChecksumInfo]
    ) -> None:
        try:
            # returns None, raises PackageRejected on failure
            must_match_any_checksum(path, checksum_info)
        except PackageRejected:
            path.unlink()
            log.warning("Download '%s' was removed from the output directory", path.name)

    if dpi:
        if dpi.req_file_checksums:
            download_info["missing_req_file_checksum"] = False
        if dpi.has_checksums_to_match:
            _checksum_must_match_or_path_unlink(dpi.path, dpi.checksums_to_match)
        if dpi.package_type == "sdist":
            _check_metadata_in_sdist(dpi.path)
        download_info["package_type"] = dpi.package_type
        download_info["index_url"] = dpi.index_url
    elif req.kind == "vcs":
        # `missing_req_file_checksum` is *always* True for VCS deps
        pass
    else:
        if req.kind == "url":
            hashes = req.hashes or [req.qualifiers.get("cachito_hash", "")]
            if hashes:
                download_info["missing_req_file_checksum"] = False
                _checksum_must_match_or_path_unlink(
                    download_info["path"], list(map(_to_checksum_info, hashes))
                )

    log.debug(
        "Successfully processed '%s' in path '%s'",
        req.download_line,
        download_info["path"].relative_to(pip_deps_dir.root),
    )

    return download_info


def _process_pypi_req(
    req: PipRequirement,
    requirements_file: PipRequirementsFile,
    index_url: str,
    pip_deps_dir: RootedPath,
    allow_binary: bool,
) -> list[dict[str, Any]]:
    download_infos: list[dict[str, Any]] = []

    artifacts: list[DistributionPackageInfo] = _process_package_distributions(
        req, pip_deps_dir, allow_binary, index_url
    )

    files: dict[str, Union[str, PathLike[str]]] = {
        dpi.url: dpi.path for dpi in artifacts if not dpi.path.exists()
    }
    asyncio.run(async_download_files(files, get_config().concurrency_limit))

    for artifact in artifacts:
        download_infos.append(
            _process_req(
                req,
                requirements_file,
                pip_deps_dir,
                artifact.download_info,
                dpi=artifact,
            )
        )

    return download_infos


def _process_vcs_req(
    req: PipRequirement, pip_deps_dir: RootedPath, **kwargs: Any
) -> dict[str, Any]:
    return _process_req(
        req,
        pip_deps_dir=pip_deps_dir,
        download_info=_download_vcs_package(req, pip_deps_dir),
        **kwargs,
    )


def _process_url_req(
    req: PipRequirement, pip_deps_dir: RootedPath, trusted_hosts: set[str], **kwargs: Any
) -> dict[str, Any]:
    return _process_req(
        req,
        pip_deps_dir=pip_deps_dir,
        download_info=_download_url_package(req, pip_deps_dir, trusted_hosts),
        **kwargs,
    )


def _download_dependencies(
    output_dir: RootedPath,
    requirements_file: PipRequirementsFile,
    allow_binary: bool = False,
) -> list[dict[str, Any]]:
    """
    Download artifacts of all dependency packages in a requirements.txt file.

    :param output_dir: the root output directory for this request
    :param requirements_file: A requirements.txt file
    :param bool allow_binary: process wheels?
    :return: Info about downloaded packages; all items will contain "kind" and "path" keys
        (and more based on kind, see _download_*_package functions for more details)
    :rtype: list[dict]
    """
    options: dict[str, Any] = _process_options(requirements_file.options)
    trusted_hosts = set(options["trusted_hosts"])
    processed: list[dict[str, Any]] = []

    if options["require_hashes"]:
        log.info("Global --require-hashes option used, will require hashes")
        require_hashes = True
    elif any(req.hashes for req in requirements_file.requirements):
        log.info("At least one dependency uses the --hash option, will require hashes")
        require_hashes = True
    else:
        # URL deps with a `cachito_hash` qualifier (which is a loophole
        # allowing for unhashed VCS deps AND URL deps to coexist in a
        # 'requirements.txt', thus `require_hashes` should NOT be set), will
        # fall through to this branch.
        log.info(
            "No hash options used, will not require hashes unless HTTP(S) dependencies are present."
        )
        require_hashes = False

    _validate_requirements(requirements_file.requirements)
    _validate_provided_hashes(requirements_file.requirements, require_hashes)

    pip_deps_dir: RootedPath = output_dir.join_within_root("deps", "pip")
    pip_deps_dir.path.mkdir(parents=True, exist_ok=True)

    for req in requirements_file.requirements:
        log.info("-- Processing requirement line '%s'", req.download_line)
        if req.kind == "pypi":
            download_infos: list[dict[str, Any]] = _process_pypi_req(
                req,
                requirements_file=requirements_file,
                index_url=options["index_url"] or pypi_simple.PYPI_SIMPLE_ENDPOINT,
                pip_deps_dir=pip_deps_dir,
                allow_binary=allow_binary,
            )
            processed.extend(download_infos)
        elif req.kind == "vcs":
            download_info = _process_vcs_req(
                req,
                requirements_file=requirements_file,
                pip_deps_dir=pip_deps_dir,
            )
            processed.append(download_info)
        elif req.kind == "url":
            download_info = _process_url_req(
                req,
                requirements_file=requirements_file,
                pip_deps_dir=pip_deps_dir,
                trusted_hosts=trusted_hosts,
            )
            processed.append(download_info)
        else:
            # Should not happen
            raise RuntimeError(f"Unexpected requirement kind: '{req.kind!r}'")

        log.info("-- Finished processing requirement line '%s'\n", req.download_line)

    return processed


def _process_options(options: list[str]) -> dict[str, Any]:
    """
    Process global options from a requirements.txt file.

    | Rejected option     | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | --extra-index-url   | We only support one index                               |
    | --no-index          | Index is the only thing we support                      |
    | -f --find-links     | We only support index                                   |
    | --only-binary       | Only sdist                                              |

    | Ignored option      | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | -c --constraint     | All versions must already be pinned                     |
    | -e --editable       | Only relevant when installing                           |
    | --no-binary         | Implied                                                 |
    | --prefer-binary     | Prefer sdist                                            |
    | --pre               | We do not care if version is pre-release (it is pinned) |
    | --use-feature       | We probably do not have that feature                    |
    | -* --*              | Did not exist when this implementation was done         |

    | Undecided option    | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | -r --requirement    | We could support this but there is no good reason to    |

    | Relevant option     | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | -i --index-url      | Supported                                               |
    | --require-hashes    | Hashes are optional, so this makes sense                |
    | --trusted-host      | Disables SSL verification for URL dependencies          |

    :param list[str] options: Global options from a requirements file
    :return: Dict with all the relevant options and their values
    :raise UnsupportedFeature: If any option was rejected
    """
    reject = {
        "--extra-index-url",
        "--no-index",
        "-f",
        "--find-links",
        "--only-binary",
    }

    ignored: list[str] = []
    rejected: list[str] = []

    opts: dict[str, Any] = {
        "require_hashes": False,
        "trusted_hosts": [],
        "index_url": None,
    }

    i = 0
    while i < len(options):
        option = options[i]

        if option == "--require-hashes":
            opts["require_hashes"] = True
        elif option == "--trusted-host":
            opts["trusted_hosts"].append(options[i + 1])
            i += 1
        elif option in ("-i", "--index-url"):
            opts["index_url"] = options[i + 1]
            i += 1
        elif option in reject:
            rejected.append(option)
        elif option.startswith("-"):
            # This is a bit simplistic, option arguments may also start with a '-' but
            # should be good enough for a log message
            ignored.append(option)

        i += 1

    if ignored:
        msg = f"{APP_NAME} will ignore the following options: {', '.join(ignored)}"
        log.info(msg)

    if rejected:
        msg = f"{APP_NAME} does not support the following options: {', '.join(rejected)}"
        raise UnsupportedFeature(msg)

    return opts


def _validate_requirements(requirements: list[PipRequirement]) -> None:
    """
    Validate that all requirements meet our expectations.

    :param list[PipRequirement] requirements: All requirements from a file
    :raise PackageRejected: If any requirement does not meet expectations
    :raise UnsupportedFeature: If any requirement uses unsupported features
    """
    for req in requirements:
        # Fail if PyPI requirement is not pinned to an exact version
        if req.kind == "pypi":
            vspec = req.version_specs
            if len(vspec) != 1 or vspec[0][0] not in ("==", "==="):
                msg = f"Requirement must be pinned to an exact version: {req.download_line}"
                raise PackageRejected(
                    msg,
                    solution=(
                        "Please pin all packages as <name>==<version>\n"
                        "You may wish to use a tool such as pip-compile to pin automatically."
                    ),
                    docs=PIP_REQUIREMENTS_TXT_DOC,
                )

        # Fail if VCS requirement uses any VCS other than git or does not have a valid ref
        elif req.kind == "vcs":
            url = urllib.parse.urlparse(req.url)

            if not url.scheme.startswith("git"):
                raise UnsupportedFeature(
                    f"Unsupported VCS for {req.download_line}: {url.scheme} (only git is supported)"
                )

            if not GIT_REF_IN_PATH.search(url.path):
                msg = f"No git ref in {req.download_line} (expected 40 hexadecimal characters)"
                raise PackageRejected(
                    msg,
                    solution=(
                        "Please specify the full commit hash for git URLs or switch to https URLs."
                    ),
                    docs=PIP_EXTERNAL_DEPS_DOC,
                )

        # Fail if URL requirement does not specify exactly one hash (--hash or #cachito_hash)
        # or does not have a recognized file extension
        elif req.kind == "url":
            n_hashes = len(req.hashes) + (1 if req.qualifiers.get("cachito_hash") else 0)
            if n_hashes != 1:
                msg = (
                    f"URL requirement must specify exactly one hash, but specifies {n_hashes}: "
                    f"{req.download_line}."
                )
                raise PackageRejected(
                    msg,
                    solution=(
                        "Please specify the expected hashes for all plain URLs using "
                        "--hash options (one --hash for each)"
                    ),
                    docs=PIP_EXTERNAL_DEPS_DOC,
                )

            url = urllib.parse.urlparse(req.url)
            if not any(url.path.endswith(ext) for ext in SDIST_FILE_EXTENSIONS):
                msg = (
                    "URL for requirement does not contain any recognized file extension: "
                    f"{req.download_line} (expected one of {', '.join(SDIST_FILE_EXTENSIONS)})"
                )
                raise PackageRejected(msg, solution=None)


def _validate_provided_hashes(requirements: list[PipRequirement], require_hashes: bool) -> None:
    """
    Validate that hashes are not missing and follow the "algorithm:digest" format.

    :param list[PipRequirement] requirements: All requirements from a file
    :param bool require_hashes: True if hashes are required for all requirements
    :raise PackageRejected: If hashes are missing or have invalid format
    """
    for req in requirements:
        if req.kind == "url":
            hashes = req.hashes or [req.qualifiers["cachito_hash"]]
        else:
            hashes = req.hashes

        if require_hashes and not hashes:
            # We shouldn't get here, but it's a definite error if we do.
            # VCS reqs *cannot* be hashed, so we'll always hit
            # this for any VCS req in a 'requirements.txt' which has *any* hash
            # (other than a URL req with `cachito_hash``).
            # For URL # requirements, having a hash is required to pass *basic* validation.
            msg = f"Hash is required, dependency does not specify any: {req.download_line}"
            raise PackageRejected(
                msg,
                solution="Please specify the expected hashes for all dependencies",
                docs=PIP_REQUIREMENTS_TXT_DOC,
            )

        for hash_spec in hashes:
            _, _, digest = hash_spec.partition(":")
            if not digest:
                msg = f"Not a valid hash specifier: {hash_spec!r} (expected 'algorithm:digest')"
                raise PackageRejected(msg, solution=None)


def _process_package_distributions(
    requirement: PipRequirement,
    pip_deps_dir: RootedPath,
    allow_binary: bool = False,
    index_url: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
) -> list[DistributionPackageInfo]:
    """
    Return a list of DPI objects for the provided pip package.

    Scrape the package's PyPI page and generate a list of all available
    artifacts. Filter by version and allowed artifact type. Filter to find the
    best matching sdist artifact. Process wheel artifacts.

    A note on nomenclature (to address a common misconception)

    To strictly follow Python packaging terminology, we should avoid "source",
    since it is an overloaded term - so rather than talking about "source" vs.
    "wheel" we should use "sdist" vs "wheel" instead.

    _sdist_ - "source distribution": a tarball (usually) of a project's entire repo
    _wheel_ - built distribution: a stripped-down version of sdist, containing
    **only** Python modules and necessary application data which are needed to
    install and run the application (note that it doesn't need to and commonly
    won't include Python bytecode modules *.pyc)

    :param requirement: which pip package to process
    :param str pip_deps_dir:
    :param bool allow_binary: process wheels?
    :return: a list of DPI
    :rtype: list[DistributionPackageInfo]
    """
    allowed_distros = ["sdist", "wheel"] if allow_binary else ["sdist"]
    client = pypi_simple.PyPISimple(index_url)
    processed_dpis: list[DistributionPackageInfo] = []
    name = requirement.package
    version = requirement.version_specs[0][1]
    normalized_version = canonicalize_version(version)
    sdists: list[DistributionPackageInfo] = []
    req_file_checksums = set(map(_to_checksum_info, requirement.hashes))
    wheels: list[DistributionPackageInfo] = []

    try:
        timeout = get_config().requests_timeout
        project_page = client.get_project_page(name, timeout)
        packages: list[pypi_simple.DistributionPackage] = project_page.packages
    except (requests.RequestException, pypi_simple.NoSuchProjectError) as e:
        raise FetchError(f"PyPI query failed: {e}")

    def _is_valid(pkg: pypi_simple.DistributionPackage) -> bool:
        return (
            pkg.version is not None
            and canonicalize_version(pkg.version) == normalized_version
            and pkg.package_type is not None
            and pkg.package_type in allowed_distros
        )

    for package in packages:
        if not _is_valid(package):
            continue

        pypi_checksums: set[ChecksumInfo] = {
            ChecksumInfo(algorithm, digest) for algorithm, digest in package.digests.items()
        }

        dpi = DistributionPackageInfo(
            name,
            version,
            cast(Literal["sdist", "wheel"], package.package_type),
            pip_deps_dir.join_within_root(package.filename).path,
            package.url,
            index_url,
            package.is_yanked,
            pypi_checksums,
            req_file_checksums,
        )

        if dpi.should_download():
            if dpi.package_type == "sdist":
                sdists.append(dpi)
            else:
                wheels.append(dpi)
        else:
            log.info("Filtering out %s due to checksum mismatch", package.filename)

    if sdists:
        best_sdist = max(sdists, key=_sdist_preference)
        processed_dpis.append(best_sdist)
        if best_sdist.is_yanked:
            log.warning(
                "The version %s of package %s is yanked, use a different version", version, name
            )
    else:
        log.warning("No sdist found for package %s==%s", name, version)

        if len(wheels) == 0:
            if allow_binary:
                solution = (
                    "Please check that the package exists on PyPI or that the name"
                    " and version are correct.\n"
                )
                docs = None
            else:
                solution = (
                    "It seems that this version does not exist or isn't published as an"
                    " sdist.\n"
                    "Try to specify the dependency directly via a URL instead, for example,"
                    " the tarball for a GitHub release.\n"
                    "Alternatively, allow the use of wheels."
                )
                docs = PIP_NO_SDIST_DOC
            raise PackageRejected(
                f"No distributions found for package {name}=={version}",
                solution=solution,
                docs=docs,
            )

    processed_dpis.extend(wheels)

    return processed_dpis


def _sdist_preference(sdist_pkg: DistributionPackageInfo) -> tuple[int, int]:
    """
    Compute preference for a sdist package, can be used to sort in ascending order.

    Prefer files that are not yanked over ones that are.
    Within the same category (yanked vs. not), prefer .tar.gz > .zip > anything else.

    :param dict sdist_pkg: An item of the "urls" array in a PyPI response
    :return: Tuple of integers to use as sorting key
    """
    # Higher number = higher preference
    yanked_pref = 0 if sdist_pkg.is_yanked else 1

    filename = sdist_pkg.name
    if filename.endswith(".tar.gz"):
        filetype_pref = 2
    elif filename.endswith(".zip"):
        filetype_pref = 1
    else:
        filetype_pref = 0

    return yanked_pref, filetype_pref


def _download_vcs_package(requirement: PipRequirement, pip_deps_dir: RootedPath) -> dict[str, Any]:
    """
    Fetch the source for a Python package from VCS (only git is supported).

    :param PipRequirement requirement: VCS requirement from a requirements.txt file
    :param RootedPath pip_deps_dir: The deps/pip directory in an application request bundle

    :return: Dict with package name, download path and git info
    """
    git_info = extract_git_info(requirement.url)

    download_to = pip_deps_dir.join_within_root(_get_external_requirement_filepath(requirement))
    download_to.path.parent.mkdir(exist_ok=True, parents=True)

    clone_as_tarball(git_info["url"], git_info["ref"], to_path=download_to.path)

    return {
        "package": requirement.package,
        "path": download_to.path,
        **git_info,
    }


def _download_url_package(
    requirement: PipRequirement, pip_deps_dir: RootedPath, trusted_hosts: set[str]
) -> dict[str, Any]:
    """
    Download a Python package from a URL.

    :param PipRequirement requirement: URL requirement from a requirements.txt file
    :param RootedPath pip_deps_dir: The deps/pip directory in an application request bundle
    :param set[str] trusted_hosts: If host (or host:port) is trusted, do not verify SSL

    :return: Dict with package name, download path, original URL and URL with hash
    """
    url = urllib.parse.urlparse(requirement.url)

    download_to = pip_deps_dir.join_within_root(_get_external_requirement_filepath(requirement))
    download_to.path.parent.mkdir(exist_ok=True, parents=True)

    if url.port is not None and f"{url.hostname}:{url.port}" in trusted_hosts:
        log.debug("Disabling SSL verification, %s:%s is a --trusted-host", url.hostname, url.port)
        insecure = True
    elif url.hostname in trusted_hosts:
        log.debug("Disabling SSL verification, %s is a --trusted-host", url.hostname)
        insecure = True
    else:
        insecure = False

    download_binary_file(requirement.url, download_to.path, insecure=insecure)

    if "cachito_hash" in requirement.qualifiers:
        url_with_hash = requirement.url
    else:
        url_with_hash = _add_cachito_hash_to_url(url, requirement.hashes[0])

    return {
        "package": requirement.package,
        "path": download_to.path,
        "original_url": requirement.url,
        "url_with_hash": url_with_hash,
    }


def _add_cachito_hash_to_url(parsed_url: urllib.parse.ParseResult, hash_spec: str) -> str:
    """
    Add the #cachito_hash fragment to URL.

    :param urllib.parse.ParseResult parsed_url: A parsed URL with no cachito_hash in fragment
    :param str hash_spec: A hash specifier - "algorithm:digest", e.g. "sha256:123456"
    :return: Original URL + cachito_hash in fragment
    :rtype: str
    """
    new_fragment = f"cachito_hash={hash_spec}"
    if parsed_url.fragment:
        new_fragment = f"{parsed_url.fragment}&{new_fragment}"
    return parsed_url._replace(fragment=new_fragment).geturl()


def _to_checksum_info(hash_: str) -> ChecksumInfo:
    algorithm, _, digest = hash_.partition(":")
    return ChecksumInfo(algorithm, digest)


def _download_from_requirement_files(
    output_dir: RootedPath, files: list[RootedPath], allow_binary: bool = False
) -> list[dict[str, Any]]:
    """
    Download dependencies listed in the requirement files.

    :param output_dir: the root output directory for this request
    :param files: list of absolute paths to pip requirements files
    :param allow_binary: process wheels?
    :return: Info about downloaded packages; see download_dependencies return docs for further
        reference
    :raises PackageRejected: If requirement file does not exist
    """
    requirements: list[dict[str, Any]] = []
    for req_file in files:
        if not req_file.path.exists():
            raise PackageRejected(
                f"The requirements file does not exist: {req_file}",
                solution="Please check that you have specified correct requirements file paths",
            )
        requirements.extend(
            _download_dependencies(output_dir, PipRequirementsFile(req_file), allow_binary)
        )

    return requirements


def _default_requirement_file_list(path: RootedPath, devel: bool = False) -> list[RootedPath]:
    """
    Get the paths for the default pip requirement files, if they are present.

    :param path: the full path to the application source code
    :param devel: whether to return the build requirement files
    :return: list of str representing the absolute paths to the Python requirement files
    """
    filename = DEFAULT_BUILD_REQUIREMENTS_FILE if devel else DEFAULT_REQUIREMENTS_FILE
    req = path.join_within_root(filename)
    return [req] if req.path.is_file() else []


def _resolve_pip(
    app_path: RootedPath,
    output_dir: RootedPath,
    requirement_files: Optional[list[Path]] = None,
    build_requirement_files: Optional[list[Path]] = None,
    allow_binary: bool = False,
) -> dict[str, Any]:
    """
    Resolve and fetch pip dependencies for the given pip application.

    :param app_path: the full path to the application source code
    :param output_dir: the root output directory for this request
    :param list requirement_files: a list of str representing paths to the Python requirement files
        to be used to compile a list of dependencies to be fetched
    :param list build_requirement_files: a list of str representing paths to the Python build
        requirement files to be used to compile a list of build dependencies to be fetched
    :param bool allow_binary: process wheels?
    :return: a dictionary that has the following keys:
        ``package`` which is the dict representing the main Package,
        ``dependencies`` which is a list of dicts representing the package Dependencies
        ``requirements`` which is a list of absolute paths for the processed requirement files
    :raises PackageRejected | UnsupportedFeature: if the package is not compatible with our
    requirements/expectations
    """
    pkg_name, pkg_version = _get_pip_metadata(app_path)

    def resolve_req_files(req_files: Optional[list[Path]], devel: bool) -> list[RootedPath]:
        resolved: list[RootedPath] = []
        # This could be an empty list
        if req_files is None:
            resolved.extend(_default_requirement_file_list(app_path, devel=devel))
        else:
            resolved.extend([app_path.join_within_root(r) for r in req_files])

        return resolved

    resolved_req_files = resolve_req_files(requirement_files, False)
    resolved_build_req_files = resolve_req_files(build_requirement_files, True)

    requires = _download_from_requirement_files(output_dir, resolved_req_files, allow_binary)
    build_requires = _download_from_requirement_files(
        output_dir, resolved_build_req_files, allow_binary
    )

    # Mark all build dependencies as such
    for dependency in build_requires:
        dependency["build_dependency"] = True

    def _version(dep: dict[str, Any]) -> str:
        if dep["kind"] == "pypi":
            version = dep["version"]
        elif dep["kind"] == "vcs":
            # Version is "git+" followed by the URL used to to fetch from git
            version = f"git+{dep['url']}@{dep['ref']}"
        else:
            # Version is the original URL with #cachito_hash added if it was not present
            version = dep["url_with_hash"]
        return version

    dependencies = [
        {
            "name": dep["package"],
            "version": _version(dep),
            "index_url": dep.get("index_url"),
            "type": "pip",
            "build_dependency": dep.get("build_dependency", False),
            "kind": dep["kind"],
            "requirement_file": dep["requirement_file"],
            "missing_req_file_checksum": dep["missing_req_file_checksum"],
            "package_type": dep["package_type"],
        }
        for dep in (requires + build_requires)
    ]

    return {
        "package": {"name": pkg_name, "version": pkg_version, "type": "pip"},
        "dependencies": dependencies,
        "requirements": [*resolved_req_files, *resolved_build_req_files],
    }


def _get_external_requirement_filepath(requirement: PipRequirement) -> Path:
    """Get the relative path under deps/pip/ where a URL or VCS requirement should be placed."""
    if requirement.kind == "url":
        package = requirement.package
        hashes = requirement.hashes
        hash_spec = hashes[0] if hashes else requirement.qualifiers["cachito_hash"]
        algorithm, _, digest = hash_spec.partition(":")
        orig_url = urllib.parse.urlparse(requirement.url)
        file_ext = next(ext for ext in SDIST_FILE_EXTENSIONS if orig_url.path.endswith(ext))
        # e.g. external-pyarn/pyarn-external-sha256-deadbeef.tar.gz
        filepath = Path(f"external-{package}", f"{package}-external-{algorithm}-{digest}{file_ext}")
    elif requirement.kind == "vcs":
        git_info = extract_git_info(requirement.url)
        repo = git_info["repo"]
        ref = git_info["ref"]
        # e.g. github.com/containerbuildsystem/pyarn/pyarn-external-gitcommit-badbeef.tar.gz
        filepath = Path(
            git_info["host"],
            git_info["namespace"],  # namespaces can contain '/' but pathlib can handle that
            repo,
            f"{repo}-external-gitcommit-{ref}.tar.gz",
        )
    else:
        raise ValueError(f"{requirement.kind=} is neither 'url' nor 'vcs'")

    return filepath


def _iter_zip_file(file_path: Path) -> Iterator[str]:
    with zipfile.ZipFile(file_path, "r") as zf:
        yield from zf.namelist()


def _iter_tar_file(file_path: Path) -> Iterator[str]:
    with tarfile.open(file_path, "r") as tar:
        for member in tar:
            yield member.name


def _is_pkg_info_dir(path: str) -> bool:
    """Simply check whether a path represents the PKG_INFO directory.

    Generally, it is in the format for example: pkg-1.0/PKG_INFO

    :param str path: a path.
    :return: True if it is, otherwise False is returned.
    :rtype: bool
    """
    parts = os.path.split(path)
    return len(parts) == 2 and parts[1] == "PKG-INFO"


def _check_metadata_in_sdist(sdist_path: Path) -> None:
    """Check if a downloaded sdist package has metadata.

    :param sdist_path: the path of a sdist package file.
    :type sdist_path: pathlib.Path
    :raise PackageRejected: if the sdist is invalid.
    """
    if sdist_path.name.endswith(ZIP_FILE_EXT):
        files_iter = _iter_zip_file(sdist_path)
    elif sdist_path.name.endswith(COMPRESSED_TAR_EXT):
        log.warning("Skip checking metadata from compressed sdist %s", sdist_path.name)
        return
    elif any(map(sdist_path.name.endswith, SDIST_FILE_EXTENSIONS)):
        files_iter = _iter_tar_file(sdist_path)
    else:
        # Invalid usage of the method (we don't download files without a known extension)
        raise ValueError(
            f"Cannot check metadata from {sdist_path}, "
            f"which does not have a known supported extension.",
        )

    try:
        if not any(map(_is_pkg_info_dir, files_iter)):
            raise PackageRejected(
                f"{sdist_path.name} does not include metadata (there is no PKG-INFO file). "
                "It is not a valid sdist and cannot be downloaded from PyPI.",
                solution=(
                    "Consider editing your requirements file to download the package from git "
                    "or a direct download URL instead."
                ),
                docs=PIP_NO_SDIST_DOC,
            )
    except tarfile.ReadError as e:
        raise PackageRejected(
            f"Cannot open {sdist_path} as a Tar file. Error: {str(e)}", solution=None
        )
    except zipfile.BadZipFile as e:
        raise PackageRejected(
            f"Cannot open {sdist_path} as a Zip file. Error: {str(e)}", solution=None
        )


def _replace_external_requirements(requirements_file_path: RootedPath) -> Optional[ProjectFile]:
    """Generate an updated requirements file.

    Replace the urls of external dependencies with file paths (templated).
    If no updates are needed, return None.
    """
    requirements_file = PipRequirementsFile(requirements_file_path)

    def maybe_replace(requirement: PipRequirement) -> Optional[PipRequirement]:
        if requirement.kind in ("url", "vcs"):
            path = _get_external_requirement_filepath(requirement)
            templated_abspath = Path("${output_dir}", "deps", "pip", path)
            return requirement.copy(url=f"file://{templated_abspath}")
        return None

    replaced = [maybe_replace(requirement) for requirement in requirements_file.requirements]
    if not any(replaced):
        # No need for a custom requirements file
        return None

    requirements = [
        replaced or original for replaced, original in zip(replaced, requirements_file.requirements)
    ]
    replaced_requirements_file = PipRequirementsFile.from_requirements_and_options(
        requirements, requirements_file.options
    )

    return ProjectFile(
        abspath=Path(requirements_file_path).resolve(),
        template=replaced_requirements_file.generate_file_content(),
    )
