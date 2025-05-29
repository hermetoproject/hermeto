"""
This module provides classes for parsing and extracting metadata from Python project files.

- pyproject.toml
- setup.cfg
- setup.py

These files are commonly used in Python projects to define package name, version, dependencies,
and other configuration settings.
"""

import ast
import configparser
import functools
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import tomlkit
import tomlkit.exceptions
from packaging.utils import canonicalize_version
from typing_extensions import TypeGuard

from hermeto.core.errors import PackageRejected
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


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
        except tomlkit.exceptions.ParseError as e:
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
