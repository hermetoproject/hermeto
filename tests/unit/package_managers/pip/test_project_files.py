# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from textwrap import dedent
from typing import Any, Literal

import pytest

from hermeto.core.errors import BaseError, PackageRejected
from hermeto.core.package_managers.pip.project_files import PyProjectTOML, SetupCFG, SetupPY
from hermeto.core.rooted_path import PathOutsideRoot, RootedPath
from tests.common_utils import Symlink, write_file_tree


class TestPyprojectTOML:
    """PyProjectTOML tests."""

    @pytest.mark.parametrize("exists", [True, False])
    def test_exists(self, exists: bool, rooted_tmp_path: RootedPath) -> None:
        if exists:
            rooted_tmp_path.join_within_root("pyproject.toml").path.write_text("")

        pyproject_toml = PyProjectTOML(rooted_tmp_path)
        assert pyproject_toml.exists() == exists

    def _assert_has_logs(
        self, expect_logs: list[str], tmpdir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        for log in expect_logs:
            assert log.format(tmpdir=tmpdir) in caplog.text

    @pytest.mark.parametrize(
        "toml_content, expect_name, expect_logs",
        [
            (
                "",
                None,
                [
                    "Parsing pyproject.toml at '{tmpdir}/pyproject.toml'",
                    "No project.name in pyproject.toml",
                ],
            ),
            (
                dedent(
                    """\
                    [project]
                    name
                    version = "0.1.0"
                    description = "A short description of the package."
                    license = "MIT"
                    """
                ),
                None,
                [
                    "Parsing pyproject.toml at '{tmpdir}/pyproject.toml'",
                    "Failed to parse pyproject.toml: ",
                ],
            ),
            (
                dedent(
                    """\
                    [project]
                    name = "my-package"
                    version = "0.1.0"
                    description = "A short description of the package."
                    license = "MIT"
                    """
                ),
                "my-package",
                [
                    "Parsing pyproject.toml at '{tmpdir}/pyproject.toml'",
                ],
            ),
            (
                dedent(
                    """\
                    [project]
                    version = "0.1.0"
                    description = "A short description of the package."
                    license = "MIT"
                    """
                ),
                None,
                [
                    "Parsing pyproject.toml at '{tmpdir}/pyproject.toml'",
                    "No project.name in pyproject.toml",
                ],
            ),
        ],
    )
    def test_get_name(
        self,
        toml_content: str,
        expect_name: str | None,
        expect_logs: list[str],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_name() method."""
        pyproject_toml = rooted_tmp_path.join_within_root("pyproject.toml")
        pyproject_toml.path.write_text(toml_content)

        assert PyProjectTOML(rooted_tmp_path).get_name() == expect_name
        self._assert_has_logs(expect_logs, rooted_tmp_path.path, caplog)

    @pytest.mark.parametrize(
        "toml_content, expect_version, expect_logs",
        [
            (
                "",
                None,
                [
                    "Parsing pyproject.toml at '{tmpdir}/pyproject.toml'",
                    "No project.version in pyproject.toml",
                ],
            ),
            (
                dedent(
                    """\
                    [project]
                    name = "my-package"
                    version = 0.1.0
                    description = "A short description of the package."
                    license = "MIT"
                    """
                ),
                None,
                [
                    "Parsing pyproject.toml at '{tmpdir}/pyproject.toml'",
                    "Failed to parse pyproject.toml: ",
                ],
            ),
            (
                dedent(
                    """\
                    [project]
                    name = "my-package"
                    version = "0.1.0"
                    description = "A short description of the package."
                    license = "MIT"
                    """
                ),
                "0.1.0",
                [
                    "Parsing pyproject.toml at '{tmpdir}/pyproject.toml'",
                ],
            ),
            (
                dedent(
                    """\
                    [project]
                    name = "my-package"
                    description = "A short description of the package."
                    license = "MIT"
                    """
                ),
                None,
                [
                    "Parsing pyproject.toml at '{tmpdir}/pyproject.toml'",
                    "No project.version in pyproject.toml",
                ],
            ),
        ],
    )
    def test_get_version(
        self,
        toml_content: str,
        expect_version: str | None,
        expect_logs: list[str],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_version() method."""
        pyproject_toml = rooted_tmp_path.join_within_root("pyproject.toml")
        pyproject_toml.path.write_text(toml_content)

        assert PyProjectTOML(rooted_tmp_path).get_version() == expect_version
        self._assert_has_logs(expect_logs, rooted_tmp_path.path, caplog)


class TestSetupCFG:
    """SetupCFG tests."""

    @pytest.mark.parametrize("exists", [True, False])
    def test_exists(self, exists: bool, rooted_tmp_path: RootedPath) -> None:
        """Test file existence check."""
        if exists:
            rooted_tmp_path.join_within_root("setup.cfg").path.write_text("")

        setup_cfg = SetupCFG(rooted_tmp_path)
        assert setup_cfg.exists() == exists

    @pytest.mark.parametrize(
        "cfg_content, expect_name, expect_logs",
        [
            (
                "",
                None,
                ["Parsing setup.cfg at '{tmpdir}/setup.cfg'", "No metadata.name in setup.cfg"],
            ),
            ("[metadata]", None, ["No metadata.name in setup.cfg"]),
            (
                dedent(
                    """\
                    [metadata]
                    name = foo
                    """
                ),
                "foo",
                [
                    "Parsing setup.cfg at '{tmpdir}/setup.cfg'",
                    "Found metadata.name in setup.cfg: 'foo'",
                ],
            ),
            (
                "[malformed",
                None,
                [
                    "Parsing setup.cfg at '{tmpdir}/setup.cfg'",
                    "Failed to parse setup.cfg: File contains no section headers",
                    "No metadata.name in setup.cfg",
                ],
            ),
        ],
    )
    def test_get_name(
        self,
        cfg_content: str,
        expect_name: str | None,
        expect_logs: list[str],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_name() method."""
        setup_cfg = rooted_tmp_path.join_within_root("setup.cfg")
        setup_cfg.path.write_text(cfg_content)

        assert SetupCFG(rooted_tmp_path).get_name() == expect_name
        self._assert_has_logs(expect_logs, rooted_tmp_path.path, caplog)

    @pytest.mark.parametrize(
        "cfg_content, expect_version, expect_logs",
        [
            (
                "",
                None,
                ["Parsing setup.cfg at '{tmpdir}/setup.cfg'", "No metadata.version in setup.cfg"],
            ),
            ("[metadata]", None, ["No metadata.version in setup.cfg"]),
            (
                dedent(
                    """\
                    [metadata]
                    version = 1.0.0
                    """
                ),
                "1.0.0",
                [
                    "Parsing setup.cfg at '{tmpdir}/setup.cfg'",
                    "Resolving metadata.version in setup.cfg from '1.0.0'",
                    "Found metadata.version in setup.cfg: '1.0.0'",
                ],
            ),
            (
                "[malformed",
                None,
                [
                    "Parsing setup.cfg at '{tmpdir}/setup.cfg'",
                    "Failed to parse setup.cfg: File contains no section headers",
                    "No metadata.version in setup.cfg",
                ],
            ),
        ],
    )
    def test_get_version_basic(
        self,
        cfg_content: str,
        expect_version: str | None,
        expect_logs: list[str],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_version() method with basic cases."""
        setup_cfg = rooted_tmp_path.join_within_root("setup.cfg")
        setup_cfg.path.write_text(cfg_content)

        assert SetupCFG(rooted_tmp_path).get_version() == expect_version
        self._assert_has_logs(expect_logs, rooted_tmp_path.path, caplog)

    def _assert_has_logs(
        self, expect_logs: list[str], tmpdir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        for log in expect_logs:
            assert log.format(tmpdir=tmpdir) in caplog.text

    def _test_version_with_file_tree(
        self,
        project_tree: dict[str, Any],
        expect_version: str | None,
        expect_logs: list[str],
        expect_error: BaseError | None,
        rooted_tmpdir: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test resolving version from file: or attr: directive."""
        write_file_tree(project_tree, rooted_tmpdir.path)
        setup_cfg = SetupCFG(rooted_tmpdir)

        if expect_error is None:
            assert setup_cfg.get_version() == expect_version
        else:
            with pytest.raises(type(expect_error)):
                setup_cfg.get_version()

        logs = expect_logs.copy()
        # Does not actually have to be at index 0, this is just to be more obvious
        logs.insert(0, f"Parsing setup.cfg at '{rooted_tmpdir.join_within_root('setup.cfg')}'")
        if expect_version is not None:
            logs.append(f"Found metadata.version in setup.cfg: '{expect_version}'")
        elif expect_error is None:
            logs.append("Failed to resolve metadata.version in setup.cfg")

        self._assert_has_logs(logs, rooted_tmpdir.path, caplog)

    @pytest.mark.parametrize(
        "project_tree, expect_version, expect_logs, expect_error",
        [
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: missing.txt
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'file: missing.txt'",
                    "Version file 'missing.txt' does not exist or is not a file",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: version.txt
                        """
                    ),
                    "version.txt": "1.0.0",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'file: version.txt'",
                    "Read version from 'version.txt': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: version.txt
                        """
                    ),
                    "version.txt": "\n1.0.0\n",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'file: version.txt'",
                    "Read version from 'version.txt': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: data/version.txt
                        """
                    ),
                    "data": {"version.txt": "1.0.0"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'file: data/version.txt'",
                    "Read version from 'data/version.txt': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = file: ../version.txt
                        """
                    ),
                },
                None,
                ["Resolving metadata.version in setup.cfg from 'file: ../version.txt'"],
                PathOutsideRoot(""),
            ),
        ],
    )
    def test_get_version_file(
        self,
        project_tree: dict[str, Any],
        expect_version: str | None,
        expect_logs: list[str],
        expect_error: BaseError | None,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_version() method with file: directive."""
        self._test_version_with_file_tree(
            project_tree, expect_version, expect_logs, expect_error, rooted_tmp_path, caplog
        )

    @pytest.mark.parametrize(
        "project_tree, expect_version, expect_logs, expect_error",
        [
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: missing_file.__ver__
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: missing_file.__ver__'",
                    "Attempting to find attribute '__ver__' in 'missing_file'",
                    "Module 'missing_file' not found",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: syntax_error.__ver__
                        """
                    ),
                    "syntax_error.py": "syntax error",
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: syntax_error.__ver__'",
                    "Attempting to find attribute '__ver__' in 'syntax_error'",
                    "Found module 'syntax_error' at '{tmpdir}/syntax_error.py'",
                    "Syntax error when parsing module: invalid syntax (syntax_error.py, line 1)",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: missing_attr.__ver__
                        """
                    ),
                    "missing_attr.py": "",
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: missing_attr.__ver__'",
                    "Attempting to find attribute '__ver__' in 'missing_attr'",
                    "Found module 'missing_attr' at '{tmpdir}/missing_attr.py'",
                    "Could not find attribute in 'missing_attr': '__ver__' not found",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: not_a_literal.__ver__
                        """
                    ),
                    "not_a_literal.py": "__ver__ = get_version()",
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: not_a_literal.__ver__'",
                    "Attempting to find attribute '__ver__' in 'not_a_literal'",
                    "Found module 'not_a_literal' at '{tmpdir}/not_a_literal.py'",
                    (
                        "Could not find attribute in 'not_a_literal': "
                        "'__ver__' is not assigned to a literal expression"
                    ),
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__
                        """
                    ),
                    "module.py": "__ver__ = '1.0.0'",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Found module 'module' at '{tmpdir}/module.py'",
                    "Found attribute '__ver__' in 'module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: package.__ver__
                        """
                    ),
                    "package": {"__init__.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: package.__ver__'",
                    "Attempting to find attribute '__ver__' in 'package'",
                    "Found module 'package' at '{tmpdir}/package/__init__.py'",
                    "Found attribute '__ver__' in 'package': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: package.module.__ver__
                        """
                    ),
                    "package": {"module.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: package.module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'package.module'",
                    "Found module 'package.module' at '{tmpdir}/package/module.py'",
                    "Found attribute '__ver__' in 'package.module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: package_before_module.__ver__
                        """
                    ),
                    "package_before_module": {"__init__.py": "__ver__ = '1.0.0'"},
                    "package_before_module.py": "__ver__ = '2.0.0'",
                },
                "1.0.0",
                [
                    (
                        "Resolving metadata.version in setup.cfg from "
                        "'attr: package_before_module.__ver__'"
                    ),
                    "Attempting to find attribute '__ver__' in 'package_before_module'",
                    (
                        "Found module 'package_before_module' at "
                        "'{tmpdir}/package_before_module/__init__.py'"
                    ),
                    "Found attribute '__ver__' in 'package_before_module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: __ver__
                        """
                    ),
                    "__init__.py": "__ver__ = '1.0.0'",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: __ver__'",
                    "Attempting to find attribute '__ver__' in '__init__'",
                    "Found module '__init__' at '{tmpdir}/__init__.py'",
                    "Found attribute '__ver__' in '__init__': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: .__ver__
                        """
                    ),
                    "__init__.py": "__ver__ = '1.0.0'",
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: .__ver__'",
                    "Attempting to find attribute '__ver__' in '__init__'",
                    "Found module '__init__' at '{tmpdir}/__init__.py'",
                    "Found attribute '__ver__' in '__init__': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: is_tuple.__ver__
                        """
                    ),
                    "is_tuple.py": "__ver__ = (1, 0, 'alpha', 1)",
                },
                "1.0a1",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: is_tuple.__ver__'",
                    "Attempting to find attribute '__ver__' in 'is_tuple'",
                    "Found module 'is_tuple' at '{tmpdir}/is_tuple.py'",
                    "Found attribute '__ver__' in 'is_tuple': (1, 0, 'alpha', 1)",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: is_integer.__ver__
                        """
                    ),
                    "is_integer.py": "__ver__ = 1",
                },
                "1",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: is_integer.__ver__'",
                    "Attempting to find attribute '__ver__' in 'is_integer'",
                    "Found module 'is_integer' at '{tmpdir}/is_integer.py'",
                    "Found attribute '__ver__' in 'is_integer': 1",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: ..module.__ver__
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: ..module.__ver__'",
                    "Attempting to find attribute '__ver__' in '..module'",
                ],
                PackageRejected("", solution=None),
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: /root.module.__ver__
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: /root.module.__ver__'",
                    "Attempting to find attribute '__ver__' in '/root.module'",
                ],
                PackageRejected("", solution=None),
            ),
        ],
    )
    def test_get_version_attr(
        self,
        project_tree: dict[str, Any],
        expect_version: str | None,
        expect_logs: list[str],
        expect_error: BaseError | None,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_version() method with attr: directive."""
        self._test_version_with_file_tree(
            project_tree, expect_version, expect_logs, expect_error, rooted_tmp_path, caplog
        )

    @pytest.mark.parametrize(
        "project_tree, expect_version, expect_logs, expect_error",
        [
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir =
                            =src
                        """
                    ),
                    "src": {"module.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for all root modules: 'src'",
                    "Found module 'module' at '{tmpdir}/src/module.py'",
                    "Found attribute '__ver__' in 'module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir =
                            module = src/module
                        """
                    ),
                    "src": {"module.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for root module 'module': 'src/module'",
                    "Found module 'module' at '{tmpdir}/src/module.py'",
                    "Found attribute '__ver__' in 'module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir = module=src/module, =src
                        """
                    ),
                    "src": {"module.py": "__ver__ = '1.0.0'"},
                },
                "1.0.0",
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for root module 'module': 'src/module'",
                    "Found module 'module' at '{tmpdir}/src/module.py'",
                    "Found attribute '__ver__' in 'module': '1.0.0'",
                ],
                None,
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir =
                            = ..
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for all root modules: '..'",
                ],
                PathOutsideRoot(""),
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__

                        [options]
                        package_dir =
                            module = ../module
                        """
                    ),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                    "Custom path set for root module 'module': '../module'",
                ],
                PathOutsideRoot(""),
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__
                        """
                    ),
                    "module.py": Symlink("../module.py"),
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                ],
                PathOutsideRoot(""),
            ),
            (
                {
                    "setup.cfg": dedent(
                        """\
                        [metadata]
                        version = attr: module.__ver__
                        """
                    ),
                    "module": {
                        "__init__.py": Symlink("../../foo.py"),
                    },
                },
                None,
                [
                    "Resolving metadata.version in setup.cfg from 'attr: module.__ver__'",
                    "Attempting to find attribute '__ver__' in 'module'",
                ],
                PathOutsideRoot(""),
            ),
        ],
    )
    def test_get_version_attr_with_package_dir(
        self,
        project_tree: dict[str, Any],
        expect_version: str | None,
        expect_logs: list[str],
        expect_error: BaseError | None,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_version() method with attr: directive and options.package_dir."""
        self._test_version_with_file_tree(
            project_tree, expect_version, expect_logs, expect_error, rooted_tmp_path, caplog
        )


class TestSetupPY:
    """SetupPY tests."""

    @pytest.mark.parametrize("exists", [True, False])
    def test_exists(self, exists: bool, rooted_tmp_path: RootedPath) -> None:
        """Test file existence check."""
        if exists:
            rooted_tmp_path.join_within_root("setup.py").path.write_text("")

        setup_py = SetupPY(rooted_tmp_path)
        assert setup_py.exists() == exists

    def _test_get_value(
        self,
        rooted_tmpdir: RootedPath,
        caplog: pytest.LogCaptureFixture,
        script_content: str,
        expect_val: str | None,
        expect_logs: list[str],
        what: Literal["name", "version"] = "name",
    ) -> None:
        """Test getting name or version from setup.py."""
        rooted_tmpdir.join_within_root("setup.py").path.write_text(script_content.format(what=what))
        setup_py = SetupPY(rooted_tmpdir)

        if what == "name":
            value = setup_py.get_name()
        else:
            value = setup_py.get_version()

        assert value == expect_val

        logs = expect_logs.copy()
        # Does not actually have to be at index 0, this is just to be more obvious
        logs.insert(0, f"Parsing setup.py at '{rooted_tmpdir.join_within_root('setup.py')}'")
        if expect_val is None:
            msg = (
                "Version in setup.py was either not found, or failed to resolve to a valid value"
                if what == "version"
                else "Name in setup.py was either not found, or failed to resolve to a valid string"
            )
            logs.append(msg)
        else:
            logs.append(f"Found {what} in setup.py: '{expect_val}'")

        for log in logs:
            assert log.format(tmpdir=rooted_tmpdir, what=what) in caplog.text

    @pytest.mark.parametrize(
        "script_content, expect_val, expect_logs",
        [
            ("", None, ["File does not seem to have a setup call"]),
            ("my_module.setup()", None, ["File does not seem to have a setup call"]),
            (
                "syntax error",
                None,
                ["Syntax error when parsing setup.py: invalid syntax (setup.py, line 1)"],
            ),
            (
                # Note that it absolutely does not matter whether you imported anything
                "setup()",
                None,
                [
                    "Found setup call on line 1",
                    "Pseudo-path: Module.body[0] -> Expr(#1).value",
                    "setup kwarg '{what}' not found",
                ],
            ),
            (
                "setuptools.setup()",
                None,
                [
                    "Found setup call on line 1",
                    "Pseudo-path: Module.body[0] -> Expr(#1).value",
                    "setup kwarg '{what}' not found",
                ],
            ),
            (
                dedent(
                    """\
                    from setuptools import setup; setup()
                    """
                ),
                None,
                [
                    "Found setup call on line 1",
                    "Pseudo-path: Module.body[1] -> Expr(#1).value",
                    "setup kwarg '{what}' not found",
                ],
            ),
            (
                dedent(
                    """\
                    from setuptools import setup

                    setup()
                    """
                ),
                None,
                [
                    "Found setup call on line 3",
                    "Pseudo-path: Module.body[1] -> Expr(#3).value",
                    "setup kwarg '{what}' not found",
                ],
            ),
            (
                dedent(
                    """\
                    from setuptools import setup

                    setup({what}=None)
                    """
                ),
                None,
                [
                    "Found setup call on line 3",
                    "Pseudo-path: Module.body[1] -> Expr(#3).value",
                    "setup kwarg '{what}' is a literal: None",
                ],
            ),
            (
                dedent(
                    """\
                    from setuptools import setup

                    setup({what}="foo")
                    """
                ),
                "foo",
                [
                    "Found setup call on line 3",
                    "Pseudo-path: Module.body[1] -> Expr(#3).value",
                    "setup kwarg '{what}' is a literal: 'foo'",
                ],
            ),
        ],
    )
    @pytest.mark.parametrize("what", ["name", "version"])
    def test_get_kwarg_literal(
        self,
        script_content: str,
        expect_val: str | None,
        expect_logs: list[str],
        what: Literal["name", "version"],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """
        Basic tests for getting kwarg value from a literal.

        Test cases only call setup() at top level, location of setup call is much more
        important for tests with variables.
        """
        self._test_get_value(
            rooted_tmp_path, caplog, script_content, expect_val, expect_logs, what=what
        )

    @pytest.mark.parametrize(
        "version_val, expect_version",
        [("1.0.alpha.1", "1.0a1"), (1, "1"), ((1, 0, "alpha", 1), "1.0a1")],
    )
    def test_get_version_special(
        self,
        version_val: Any,
        expect_version: str,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test cases where version values get special handling."""
        script_content = f"setup(version={version_val!r})"
        expect_logs = [
            "Found setup call on line 1",
            "Pseudo-path: Module.body[0] -> Expr(#1).value",
            f"setup kwarg 'version' is a literal: {version_val!r}",
        ]
        self._test_get_value(
            rooted_tmp_path, caplog, script_content, expect_version, expect_logs, what="version"
        )

    @pytest.mark.parametrize(
        "script_content, expect_val, expect_logs",
        [
            (
                "setup({what}=foo)",
                None,
                [
                    "Pseudo-path: Module.body[0] -> Expr(#1).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    setup({what}=foo)

                    foo = "bar"
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[0] -> Expr(#1).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    if True:
                        foo = "bar"

                    setup({what}=foo)
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[1] -> Expr(#4).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    foo = get_version()

                    setup({what}=foo)
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[1] -> Expr(#3).value",
                    "Variable cannot be resolved: 'foo' is not assigned to a literal expression",
                ],
            ),
            (
                dedent(
                    """\
                    foo = None

                    setup({what}=foo)
                    """
                ),
                None,
                ["Pseudo-path: Module.body[1] -> Expr(#3).value", "Found variable 'foo': None"],
            ),
            (
                dedent(
                    """\
                    foo = "bar"

                    setup({what}=foo)
                    """
                ),
                "bar",
                ["Pseudo-path: Module.body[1] -> Expr(#3).value", "Found variable 'foo': 'bar'"],
            ),
            (
                dedent(
                    """\
                    foo = "bar"

                    if True:
                        setup({what}=foo)
                    """
                ),
                "bar",
                [
                    "Pseudo-path: Module.body[1] -> If(#3).body[0] -> Expr(#4).value",
                    "Found variable 'foo': 'bar'",
                ],
            ),
            (
                # Variable will be found only if it is in the same branch
                dedent(
                    """\
                    if True:
                        foo = "bar"
                    else:
                        setup({what}=foo)
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[0] -> If(#1).orelse[0] -> Expr(#4).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    if True:
                        foo = "bar"
                        setup({what}=foo)
                    """
                ),
                "bar",
                [
                    "Pseudo-path: Module.body[0] -> If(#1).body[1] -> Expr(#3).value",
                    "Found variable 'foo': 'bar'",
                ],
            ),
            (
                # Try statements are kinda special, because not only do they have 3 bodies,
                # they also have a list of 'handlers' (1 for each except clause)
                dedent(
                    """\
                    try:
                        pass
                    except A:
                        foo = "bar"
                    except B:
                        setup({what}=foo)
                    else:
                        pass
                    finally:
                        pass
                    """
                ),
                None,
                [
                    (
                        "Pseudo-path: Module.body[0] -> Try(#1).handlers[1] "
                        "-> ExceptHandler(#5).body[0] -> Expr(#6).value"
                    ),
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                dedent(
                    """\
                    try:
                        pass
                    except A:
                        pass
                    except B:
                        foo = "bar"
                        setup({what}=foo)
                    else:
                        pass
                    finally:
                        pass
                    """
                ),
                "bar",
                [
                    (
                        "Pseudo-path: Module.body[0] -> Try(#1).handlers[1] "
                        "-> ExceptHandler(#5).body[1] -> Expr(#7).value"
                    ),
                    "Found variable 'foo': 'bar'",
                ],
            ),
            (
                # setup() inside a FunctionDef is pretty much the same thing as setup()
                # inside an If, except this could support late binding and doesn't
                dedent(
                    """\
                    def f():
                        setup({what}=foo)

                    foo = "bar"

                    f()
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[0] -> FunctionDef(#1).body[0] -> Expr(#2).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                # Variable defined closer should take precedence
                dedent(
                    """\
                    foo = "baz"

                    if True:
                        foo = "bar"
                        setup({what}=foo)
                    """
                ),
                "bar",
                [
                    "Pseudo-path: Module.body[1] -> If(#3).body[1] -> Expr(#5).value",
                    "Found variable 'foo': 'bar'",
                ],
            ),
            (
                # Search for setup() should be depth-first, i.e. find the first setup()
                # call even if it is at a deeper level of indentation
                dedent(
                    """\
                    if True:
                        setup({what}=foo)

                    foo = "bar"
                    setup({what}=foo)
                    """
                ),
                None,
                [
                    "Pseudo-path: Module.body[0] -> If(#1).body[0] -> Expr(#2).value",
                    "Variable 'foo' not found along the setup call branch",
                ],
            ),
            (
                # Sanity check: all statements with bodies (except async def / async for)
                dedent(
                    """\
                    foo = "bar"

                    class C:
                        def f():
                            if True:
                                for x in y:
                                    while True:
                                        with x:
                                            try:
                                                pass
                                            except:
                                                setup({what}=foo)
                    """
                ),
                "bar",
                [
                    (
                        "Pseudo-path: Module.body[1] -> ClassDef(#3).body[0] "
                        "-> FunctionDef(#4).body[0] -> If(#5).body[0] -> For(#6).body[0] "
                        "-> While(#7).body[0] -> With(#8).body[0] -> Try(#9).handlers[0] "
                        "-> ExceptHandler(#11).body[0] -> Expr(#12).value"
                    ),
                    "Found variable 'foo': 'bar'",
                ],
            ),
        ],
    )
    @pytest.mark.parametrize("what", ["name", "version"])
    def test_get_kwarg_var(
        self,
        script_content: str,
        expect_val: str | None,
        expect_logs: list[str],
        what: Literal["name", "version"],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Tests for getting kwarg value from a variable."""
        lineno = next(
            i + 1 for i, line in enumerate(script_content.splitlines()) if "setup" in line
        )
        logs = expect_logs + [
            f"Found setup call on line {lineno}",
            "setup kwarg '{what}' looks like a variable",
            f"Backtracking up the AST from line {lineno} to find variable 'foo'",
        ]
        self._test_get_value(rooted_tmp_path, caplog, script_content, expect_val, logs, what=what)

    @pytest.mark.parametrize(
        "version_val, expect_version",
        [("1.0.alpha.1", "1.0a1"), (1, "1"), ((1, 0, "alpha", 1), "1.0a1")],
    )
    def test_version_var_special(
        self,
        version_val: Any,
        expect_version: str,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test that special version values are supported also for variables."""
        script_content = dedent(
            f"""\
            foo = {version_val!r}

            setup(version=foo)
            """
        )
        expect_logs = [
            "Found setup call on line 3",
            "Pseudo-path: Module.body[1] -> Expr(#3).value",
            "setup kwarg 'version' looks like a variable",
            "Backtracking up the AST from line 3 to find variable 'foo'",
            f"Found variable 'foo': {version_val!r}",
        ]
        self._test_get_value(
            rooted_tmp_path, caplog, script_content, expect_version, expect_logs, what="version"
        )

    @pytest.mark.parametrize("what", ["name", "version"])
    def test_kwarg_unsupported_expr(
        self,
        what: Literal["name", "version"],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Value of kwarg is neither a literal nor a Name."""
        script_content = f"setup({what}=get_version())"
        expect_logs = [
            "Found setup call on line 1",
            "Pseudo-path: Module.body[0] -> Expr(#1).value",
            f"setup kwarg '{what}' is an unsupported expression: Call",
        ]
        self._test_get_value(rooted_tmp_path, caplog, script_content, None, expect_logs, what=what)
