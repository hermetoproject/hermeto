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
