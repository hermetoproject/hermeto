# SPDX-License-Identifier: GPL-3.0-or-later
import re
from copy import deepcopy
from pathlib import Path
from textwrap import dedent
from typing import Any, Collection, Literal, Optional, Union
from unittest import mock
from urllib.parse import urlparse

import pypi_simple
import pytest
from _pytest.logging import LogCaptureFixture
from git import Repo

from hermeto import APP_NAME
from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import (
    BaseError,
    FetchError,
    PackageRejected,
    UnexpectedFormat,
    UnsupportedFeature,
)
from hermeto.core.models.input import PackageInput, Request
from hermeto.core.models.output import ProjectFile
from hermeto.core.models.sbom import Component, Property
from hermeto.core.package_managers import pip
from hermeto.core.rooted_path import PathOutsideRoot, RootedPath
from tests.common_utils import GIT_REF, Symlink, write_file_tree

CUSTOM_PYPI_ENDPOINT = "https://my-pypi.org/simple/"


def make_dpi(
    name: str,
    version: str = "1.0",
    package_type: Literal["sdist", "wheel"] = "sdist",
    path: Path = Path(""),
    url: str = "",
    index_url: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
    is_yanked: bool = False,
    pypi_checksum: Collection[ChecksumInfo] = (),
    req_file_checksums: Collection[ChecksumInfo] = (),
) -> pip.DistributionPackageInfo:
    return pip.DistributionPackageInfo(
        name=name,
        version=version,
        package_type=package_type,
        path=path,
        url=url,
        index_url=index_url,
        is_yanked=is_yanked,
        pypi_checksums=set(pypi_checksum),
        req_file_checksums=set(req_file_checksums),
    )


@mock.patch("hermeto.core.package_managers.pip.PyProjectTOML")
def test_get_pip_metadata_from_pyproject_toml(
    mock_pyproject_toml: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: LogCaptureFixture,
) -> None:
    pyproject_toml = mock_pyproject_toml.return_value
    pyproject_toml.exists.return_value = True
    pyproject_toml.get_name.return_value = "foo"
    pyproject_toml.get_version.return_value = "0.1.0"

    name, version = pip._get_pip_metadata(rooted_tmp_path)
    assert name == "foo"
    assert version == "0.1.0"
    assert "Checking pyproject.toml for metadata" in caplog.messages

    # check logs
    assert f"Resolved name {name} for package at {rooted_tmp_path}" in caplog.messages
    assert f"Resolved version {version} for package at {rooted_tmp_path}" in caplog.messages


@mock.patch("hermeto.core.package_managers.pip.SetupPY")
def test_get_pip_metadata_from_setup_py(
    mock_setup_py: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: LogCaptureFixture,
) -> None:
    setup_py = mock_setup_py.return_value
    setup_py.exists.return_value = True
    setup_py.get_name.return_value = "foo"
    setup_py.get_version.return_value = "0.1.0"

    name, version = pip._get_pip_metadata(rooted_tmp_path)
    assert name == "foo"
    assert version == "0.1.0"

    # check logs
    assert "Checking setup.py for metadata" in caplog.messages
    assert f"Resolved name {name} for package at {rooted_tmp_path}" in caplog.messages
    assert f"Resolved version {version} for package at {rooted_tmp_path}" in caplog.messages


@mock.patch("hermeto.core.package_managers.pip.SetupCFG")
def test_get_pip_metadata_from_setup_cfg(
    mock_setup_cfg: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: LogCaptureFixture,
) -> None:
    setup_cfg = mock_setup_cfg.return_value
    setup_cfg.exists.return_value = True
    setup_cfg.get_name.return_value = "foo"
    setup_cfg.get_version.return_value = "0.1.0"

    name, version = pip._get_pip_metadata(rooted_tmp_path)
    assert name == "foo"
    assert version == "0.1.0"

    # check logs
    assert "Checking setup.cfg for metadata" in caplog.messages
    assert f"Resolved name {name} for package at {rooted_tmp_path}" in caplog.messages
    assert f"Resolved version {version} for package at {rooted_tmp_path}" in caplog.messages


@mock.patch("hermeto.core.package_managers.pip.PyProjectTOML")
@mock.patch("hermeto.core.package_managers.pip.SetupCFG")
@mock.patch("hermeto.core.package_managers.pip.SetupPY")
def test_extract_metadata_from_config_files_with_fallbacks(
    mock_setup_py: mock.Mock,
    mock_setup_cfg: mock.Mock,
    mock_pyproject_toml: mock.Mock,
    rooted_tmp_path: RootedPath,
    caplog: LogCaptureFixture,
) -> None:
    # Case 1: Only pyproject.toml exists with name but no version
    pyproject_toml = mock_pyproject_toml.return_value
    pyproject_toml.exists.return_value = True
    pyproject_toml.get_name.return_value = "name_from_pyproject_toml"
    pyproject_toml.get_version.return_value = None

    setup_cfg = mock_setup_cfg.return_value
    setup_cfg.exists.return_value = False

    setup_py = mock_setup_py.return_value
    setup_py.exists.return_value = False

    name, version = pip._extract_metadata_from_config_files(rooted_tmp_path)
    assert name == "name_from_pyproject_toml"
    assert version is None
    assert "Checking pyproject.toml for metadata" in caplog.messages

    # Case 2: pyproject.toml exists but without a name; fallback to setup.py with name and version
    pyproject_toml.get_name.return_value = None

    setup_py.exists.return_value = True
    setup_py.get_name.return_value = "name_from_setup_py"
    setup_py.get_version.return_value = "0.1.0"

    name, version = pip._extract_metadata_from_config_files(rooted_tmp_path)
    assert name == "name_from_setup_py"
    assert version == "0.1.0"
    assert "Checking setup.py for metadata" in caplog.messages

    # Case 3: Both pyproject.toml and setup.py lack names; fallback to setup.cfg with complete metadata
    setup_py.get_name.return_value = None

    setup_cfg.exists.return_value = True
    setup_cfg.get_name.return_value = "name_from_setup_cfg"
    setup_cfg.get_version.return_value = "0.2.0"

    name, version = pip._extract_metadata_from_config_files(rooted_tmp_path)
    assert name == "name_from_setup_cfg"
    assert version == "0.2.0"
    assert "Checking setup.cfg for metadata" in caplog.messages

    # Case 4: None of the config files have names, resulting in None, None
    setup_cfg.get_name.return_value = None

    name, version = pip._extract_metadata_from_config_files(rooted_tmp_path)
    assert name is None
    assert version is None


@pytest.mark.parametrize(
    "origin_exists",
    [True, False],
)
@mock.patch("hermeto.core.package_managers.pip.PyProjectTOML")
@mock.patch("hermeto.core.package_managers.pip.SetupPY")
@mock.patch("hermeto.core.package_managers.pip.SetupCFG")
def test_get_pip_metadata_from_remote_origin(
    mock_setup_cfg: mock.Mock,
    mock_setup_py: mock.Mock,
    mock_pyproject_toml: mock.Mock,
    origin_exists: bool,
    rooted_tmp_path_repo: RootedPath,
    caplog: LogCaptureFixture,
) -> None:
    pyproject_toml = mock_pyproject_toml.return_value
    pyproject_toml.exists.return_value = False

    setup_py = mock_setup_py.return_value
    setup_py.exists.return_value = False

    setup_cfg = mock_setup_cfg.return_value
    setup_cfg.exists.return_value = False

    if origin_exists:
        repo = Repo(rooted_tmp_path_repo)
        repo.create_remote("origin", "git@github.com:user/repo.git")

        name, version = pip._get_pip_metadata(rooted_tmp_path_repo)
        assert name == "repo"
        assert version is None

        assert f"Resolved name repo for package at {rooted_tmp_path_repo}" in caplog.messages
        assert f"Could not resolve version for package at {rooted_tmp_path_repo}" in caplog.messages
    else:
        with pytest.raises(PackageRejected) as exc_info:
            pip._get_pip_metadata(rooted_tmp_path_repo)

        assert str(exc_info.value) == "Unable to infer package name from origin URL"


class TestPyprojectTOML:
    """PyProjectTOML tests."""

    @pytest.mark.parametrize("exists", [True, False])
    def test_exists(self, exists: bool, rooted_tmp_path: RootedPath) -> None:
        if exists:
            rooted_tmp_path.join_within_root("pyproject.toml").path.write_text("")

        pyproject_toml = pip.PyProjectTOML(rooted_tmp_path)
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
        expect_name: Optional[str],
        expect_logs: list[str],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_name() method."""
        pyproject_toml = rooted_tmp_path.join_within_root("pyproject.toml")
        pyproject_toml.path.write_text(toml_content)

        assert pip.PyProjectTOML(rooted_tmp_path).get_name() == expect_name
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
        expect_version: Optional[str],
        expect_logs: list[str],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_version() method."""
        pyproject_toml = rooted_tmp_path.join_within_root("pyproject.toml")
        pyproject_toml.path.write_text(toml_content)

        assert pip.PyProjectTOML(rooted_tmp_path).get_version() == expect_version
        self._assert_has_logs(expect_logs, rooted_tmp_path.path, caplog)


class TestSetupCFG:
    """SetupCFG tests."""

    @pytest.mark.parametrize("exists", [True, False])
    def test_exists(self, exists: bool, rooted_tmp_path: RootedPath) -> None:
        """Test file existence check."""
        if exists:
            rooted_tmp_path.join_within_root("setup.cfg").path.write_text("")

        setup_cfg = pip.SetupCFG(rooted_tmp_path)
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
        expect_name: Optional[str],
        expect_logs: list[str],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_name() method."""
        setup_cfg = rooted_tmp_path.join_within_root("setup.cfg")
        setup_cfg.path.write_text(cfg_content)

        assert pip.SetupCFG(rooted_tmp_path).get_name() == expect_name
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
        expect_version: Optional[str],
        expect_logs: list[str],
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test get_version() method with basic cases."""
        setup_cfg = rooted_tmp_path.join_within_root("setup.cfg")
        setup_cfg.path.write_text(cfg_content)

        assert pip.SetupCFG(rooted_tmp_path).get_version() == expect_version
        self._assert_has_logs(expect_logs, rooted_tmp_path.path, caplog)

    def _assert_has_logs(
        self, expect_logs: list[str], tmpdir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        for log in expect_logs:
            assert log.format(tmpdir=tmpdir) in caplog.text

    def _test_version_with_file_tree(
        self,
        project_tree: dict[str, Any],
        expect_version: Optional[str],
        expect_logs: list[str],
        expect_error: Optional[BaseError],
        rooted_tmpdir: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test resolving version from file: or attr: directive."""
        write_file_tree(project_tree, rooted_tmpdir.path)
        setup_cfg = pip.SetupCFG(rooted_tmpdir)

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
        expect_version: Optional[str],
        expect_logs: list[str],
        expect_error: Optional[BaseError],
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
        expect_version: Optional[str],
        expect_logs: list[str],
        expect_error: Optional[BaseError],
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
        expect_version: Optional[str],
        expect_logs: list[str],
        expect_error: Optional[BaseError],
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

        setup_py = pip.SetupPY(rooted_tmp_path)
        assert setup_py.exists() == exists

    def _test_get_value(
        self,
        rooted_tmpdir: RootedPath,
        caplog: pytest.LogCaptureFixture,
        script_content: str,
        expect_val: Optional[str],
        expect_logs: list[str],
        what: Literal["name", "version"] = "name",
    ) -> None:
        """Test getting name or version from setup.py."""
        rooted_tmpdir.join_within_root("setup.py").path.write_text(script_content.format(what=what))
        setup_py = pip.SetupPY(rooted_tmpdir)

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
        expect_val: Optional[str],
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
        expect_val: Optional[str],
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


class TestPipRequirementsFile:
    """PipRequirementsFile tests."""

    PIP_REQUIREMENT_ATTRS: dict[str, Any] = {
        "download_line": None,
        "environment_marker": None,
        "extras": [],
        "hashes": [],
        "kind": None,
        "options": [],
        "package": None,
        "qualifiers": {},
        "raw_package": None,
        "version_specs": [],
    }

    @pytest.mark.parametrize(
        "file_contents, expected_requirements, expected_global_options",
        (
            # Dependency from pypi
            (
                "aiowsgi",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi",
                        "raw_package": "aiowsgi",
                    }
                ],
                [],
            ),
            # Dependency from pypi with pinned version
            (
                "aiowsgi==0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with minimum version
            (
                "aiowsgi>=0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi>=0.7",
                        "version_specs": [(">=", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with version range
            (
                "aiowsgi>=0.7,<1.0",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi>=0.7,<1.0",
                        "version_specs": [(">=", "0.7"), ("<", "1.0")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with picky version
            (
                "aiowsgi>=0.7,<1.0,!=0.8",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi>=0.7,<1.0,!=0.8",
                        "version_specs": [(">=", "0.7"), ("<", "1.0"), ("!=", "0.8")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with extras
            (
                "aiowsgi[spam,bacon]==0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi[spam,bacon]==0.7",
                        "version_specs": [("==", "0.7")],
                        "extras": ["spam", "bacon"],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with major version compatibility
            (
                "aiowsgi~=0.6",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi~=0.6",
                        "version_specs": [("~=", "0.6")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with environment markers
            (
                'aiowsgi; python_version < "2.7"',
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": 'aiowsgi; python_version < "2.7"',
                        "environment_marker": 'python_version < "2.7"',
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with hashes
            (
                dedent(
                    """\
                    amqp==2.5.2 \\
                       --hash=sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545 \\
                       --hash=sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8
                    """
                ),
                [
                    {
                        "package": "amqp",
                        "kind": "pypi",
                        "download_line": "amqp==2.5.2",
                        "version_specs": [("==", "2.5.2")],
                        "hashes": [
                            "sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545",
                            "sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8",
                        ],
                        "raw_package": "amqp",
                    },
                ],
                [],
            ),
            # Dependency from URL with egg name
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with package name
            (
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                        ),
                        "raw_package": "cnr_server",
                        "url": "https://github.com/quay/appr/archive/58c88e49.tar.gz",
                    },
                ],
                [],
            ),
            # Dependency from URL with both egg and package names
            (
                "ignored @ https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Editable dependency from URL
            (
                "-e https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "options": ["-e"],
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with hashes
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server "
                    "--hash=sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d91"
                    "2c7f55ec2e6c70c8"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "hashes": [
                            "sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f55"
                            "ec2e6c70c8",
                        ],
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with a percent-escaped #cachito_hash
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                    "&cachito_hash=sha256%3A4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb3218"
                    "9d912c7f55ec2e6c70c8"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server&cachito_hash=sha256%3A4fd9429bfbb796a48c0bde6bd30"
                            "1ff5b3cc02adb32189d912c7f55ec2e6c70c8"
                        ),
                        "qualifiers": {
                            "egg": "cnr_server",
                            "cachito_hash": (
                                "sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f55"
                                "ec2e6c70c8"
                            ),
                        },
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                            "&cachito_hash=sha256%3A4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb3218"
                            "9d912c7f55ec2e6c70c8"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with environment markers
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server; "
                    'python_version < "2.7"'
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                            ' ; python_version < "2.7"'
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "environment_marker": 'python_version < "2.7"',
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with multiple qualifiers
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz"
                    "#egg=cnr_server&spam=maps&bacon=nocab"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                        "qualifiers": {"egg": "cnr_server", "spam": "maps", "bacon": "nocab"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from VCS with egg name
            (
                "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                    },
                ],
                [],
            ),
            # Dependency from VCS with package name
            (
                "cnr_server @ git+https://github.com/quay/appr.git@58c88e49",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                        ),
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49",
                    },
                ],
                [],
            ),
            # Dependency from VCS with both egg and package names
            (
                "ignored @ git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                    },
                ],
                [],
            ),
            # Editable dependency from VCS
            (
                "-e git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server"
                        ),
                        "options": ["-e"],
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                    },
                ],
                [],
            ),
            # Dependency from VCS with multiple qualifiers
            (
                (
                    "git+https://github.com/quay/appr.git@58c88e49"
                    "#egg=cnr_server&spam=maps&bacon=nocab"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                        "qualifiers": {"egg": "cnr_server", "spam": "maps", "bacon": "nocab"},
                        "raw_package": "cnr_server",
                        "url": (
                            "git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                    },
                ],
                [],
            ),
            # No dependencies
            ("", [], []),
            # Comments are ignored
            (
                dedent(
                    """\
                    aiowsgi==0.7 # inline comment
                    # Line comment
                    asn1crypto==1.3.0 # inline comment \
                    with line continuation
                    # Line comment \
                    with line continuation
                        # Line comment with multiple leading white spaces
                    """
                ),
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                    {
                        "package": "asn1crypto",
                        "kind": "pypi",
                        "download_line": "asn1crypto==1.3.0",
                        "version_specs": [("==", "1.3.0")],
                        "raw_package": "asn1crypto",
                    },
                ],
                [],
            ),
            # Empty lines are ignored
            (
                dedent(
                    """\
                    aiowsgi==0.7
                            \

                    asn1crypto==1.3.0

                    """
                ),
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                    {
                        "package": "asn1crypto",
                        "kind": "pypi",
                        "download_line": "asn1crypto==1.3.0",
                        "version_specs": [("==", "1.3.0")],
                        "raw_package": "asn1crypto",
                    },
                ],
                [],
            ),
            # Line continuation is honored
            (
                dedent(
                    """\
                    aiowsgi\\
                    \\
                    ==\\
                    \\
                    \\
                    \\
                    0.7\\
                    """
                ),
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Global options
            (
                "--only-binary :all:",
                [],
                ["--only-binary", ":all:"],
            ),
            # Global options with a requirement
            (
                "aiowsgi==0.7 --only-binary :all:",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                ["--only-binary", ":all:"],
            ),
        ),
    )
    def test_parsing_of_valid_cases(
        self,
        file_contents: str,
        expected_requirements: list[dict[str, str]],
        expected_global_options: list[dict[str, str]],
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Test the various valid use cases of requirements in a requirements file."""
        requirements_file = rooted_tmp_path.join_within_root("requirements.txt")
        requirements_file.path.write_text(file_contents)

        pip_requirements = pip.PipRequirementsFile(requirements_file)

        assert pip_requirements.options == expected_global_options
        assert len(pip_requirements.requirements) == len(expected_requirements)
        for pip_requirement, expected_requirement in zip(
            pip_requirements.requirements, expected_requirements
        ):
            self._assert_pip_requirement(pip_requirement, expected_requirement)

    @pytest.mark.parametrize(
        "file_contents, expected_error",
        (
            # Invalid (probably) format
            ("--spam", "Unknown requirements file option '--spam'"),
            (
                "--prefer-binary=spam",
                "Unexpected value for requirements file option '--prefer-binary=spam'",
            ),
            ("--only-binary", "Requirements file option '--only-binary' requires a value"),
            ("aiowsgi --hash", "Requirements file option '--hash' requires a value"),
            (
                "-e",
                re.escape(
                    "Requirements file option(s) ['-e'] can only be applied to a requirement"
                ),
            ),
            (
                "aiowsgi==0.7 asn1crypto==1.3.0",
                "Unable to parse the requirement 'aiowsgi==0.7 asn1crypto==1.3.0'",
            ),
            (
                "cnr_server@foo@https://github.com/quay/appr/archive/58c88e49.tar.gz",
                "Unable to extract scheme from direct access requirement",
            ),
            # Valid format but we don't support it
            (
                "pip @ file:///localbuilds/pip-1.3.1.zip",
                UnsupportedFeature("Direct references with 'file' scheme are not supported"),
            ),
            (
                "file:///localbuilds/pip-1.3.1.zip",
                UnsupportedFeature("Direct references with 'file' scheme are not supported"),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz",
                UnsupportedFeature("Dependency name could not be determined from the requirement"),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=",
                UnsupportedFeature("Dependency name could not be determined from the requirement"),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg",
                UnsupportedFeature("Dependency name could not be determined from the requirement"),
            ),
        ),
    )
    def test_parsing_of_invalid_cases(
        self, file_contents: str, expected_error: Union[str, Exception], rooted_tmp_path: RootedPath
    ) -> None:
        """Test the invalid use cases of requirements in a requirements file."""
        requirements_file = rooted_tmp_path.join_within_root("requirements.txt")
        requirements_file.path.write_text(file_contents)

        pip_requirements = pip.PipRequirementsFile(requirements_file)

        expected_err_type = (
            type(expected_error) if isinstance(expected_error, Exception) else UnexpectedFormat
        )

        with pytest.raises(expected_err_type, match=str(expected_error)):
            pip_requirements.requirements

    def test_corner_cases_when_parsing_single_line(self) -> None:
        """Test scenarios in PipRequirement that cannot be triggered via PipRequirementsFile."""
        # Empty lines are NOT ignored
        with pytest.raises(UnexpectedFormat, match="Unable to parse the requirement"):
            assert pip.PipRequirement.from_line("     ", []) is None

        with pytest.raises(UnexpectedFormat, match="Unable to parse the requirement"):
            pip.PipRequirement.from_line("aiowsgi==0.7 \nasn1crypto==1.3.0", [])

    def test_replace_requirements(self, rooted_tmp_path: RootedPath) -> None:
        """Test generating a new requirements file with replacements."""
        original_file_path = rooted_tmp_path.join_within_root("original-requirements.txt")
        new_file_path = rooted_tmp_path.join_within_root("new-requirements.txt")

        original_file_path.path.write_text(
            dedent(
                """\
                https://github.com/quay/appr/archive/58c88.tar.gz#egg=cnr_server --hash=sha256:123
                -e spam @ git+https://github.com/monty/spam.git@123456
                aiowsgi==0.7
                asn1crypto==1.3.0
                """
            )
        )

        # Mapping of the new URL value to be used in modified requirements
        new_urls = {
            "cnr_server": "https://cachito/nexus/58c88.tar.gz",
            "spam": "https://cachito/nexus/spam-123456.tar.gz",
            "asn1crypto": "https://cachito/nexus/asn1crypto-1.3.0.tar.gz",
        }

        # Mapping of the new hash values to be used in modified requirements
        new_hashes = {
            "spam": ["sha256:45678"],
            "aiowsgi": ["sha256:90123"],
            "asn1crypto": ["sha256:01234"],
        }

        expected_new_file = dedent(
            """\
            cnr_server @ https://cachito/nexus/58c88.tar.gz#egg=cnr_server --hash=sha256:123
            spam @ https://cachito/nexus/spam-123456.tar.gz --hash=sha256:45678
            aiowsgi==0.7 --hash=sha256:90123
            asn1crypto @ https://cachito/nexus/asn1crypto-1.3.0.tar.gz --hash=sha256:01234
            """
        )

        expected_attr_changes: dict[str, dict[str, Any]] = {
            "cnr_server": {
                "download_line": "cnr_server @ https://cachito/nexus/58c88.tar.gz#egg=cnr_server",
                "url": "https://cachito/nexus/58c88.tar.gz#egg=cnr_server",
            },
            "spam": {
                "hashes": ["sha256:45678"],
                "options": [],
                "kind": "url",
                "download_line": "spam @ https://cachito/nexus/spam-123456.tar.gz",
                "url": "https://cachito/nexus/spam-123456.tar.gz",
            },
            "aiowsgi": {"hashes": ["sha256:90123"]},
            "asn1crypto": {
                "download_line": "asn1crypto @ https://cachito/nexus/asn1crypto-1.3.0.tar.gz",
                "hashes": ["sha256:01234"],
                "kind": "url",
                "version_specs": [],
                "url": "https://cachito/nexus/asn1crypto-1.3.0.tar.gz",
            },
        }

        pip_requirements = pip.PipRequirementsFile(original_file_path)

        new_requirements = []
        for pip_requirement in pip_requirements.requirements:
            url = new_urls.get(pip_requirement.raw_package)
            hashes = new_hashes.get(pip_requirement.raw_package)
            new_requirements.append(pip_requirement.copy(url=url, hashes=hashes))

        # Verify a new PipRequirementsFile can be loaded in memory and written correctly to disk.
        new_file = pip.PipRequirementsFile.from_requirements_and_options(
            new_requirements, pip_requirements.options
        )

        assert new_file.generate_file_content() == expected_new_file

        with open(new_file_path, "w") as f:
            new_file.write(f)

        # Parse the newly generated requirements file to ensure it's parsed correctly.
        new_pip_requirements = pip.PipRequirementsFile(new_file_path)

        assert new_pip_requirements.options == pip_requirements.options
        for new_pip_requirement, pip_requirement in zip(
            new_pip_requirements.requirements, pip_requirements.requirements
        ):
            for attr in self.PIP_REQUIREMENT_ATTRS:
                expected_value = expected_attr_changes.get(pip_requirement.raw_package, {}).get(
                    attr, getattr(pip_requirement, attr)
                )
                assert (
                    getattr(new_pip_requirement, attr) == expected_value
                ), f"unexpected {attr!r} value for package {pip_requirement.raw_package!r}"

    def test_write_requirements_file(self, rooted_tmp_path: RootedPath) -> None:
        """Test PipRequirementsFile.write method."""
        original_file_path = rooted_tmp_path.join_within_root("original-requirements.txt")
        new_file_path = rooted_tmp_path.join_within_root("test-requirements.txt")

        content = dedent(
            """\
            --only-binary :all:
            aiowsgi==0.7
            asn1crypto==1.3.0
            """
        )

        original_file_path.path.write_text(content)
        assert original_file_path.path.exists()
        pip_requirements = pip.PipRequirementsFile(original_file_path)
        assert pip_requirements.requirements
        assert pip_requirements.options

        with open(new_file_path, "w") as f:
            pip_requirements.write(f)

        with open(new_file_path) as f:
            assert f.read() == content

    @pytest.mark.parametrize(
        "requirement_line, requirement_options, expected_str_line",
        (
            ("aiowsgi==1.2.3", [], "aiowsgi==1.2.3"),
            ("aiowsgi>=0.7", [], "aiowsgi>=0.7"),
            ('aiowsgi; python_version < "2.7"', [], 'aiowsgi; python_version < "2.7"'),
            (
                "amqp==2.5.2",
                [
                    "--hash",
                    "sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545",
                    "--hash",
                    "sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8",
                ],
                (
                    "amqp==2.5.2 --hash=sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545 "
                    "--hash=sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8"
                ),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [],
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
            ),
            (
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz",
                [],
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz",
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["-e"],
                (
                    "-e cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                    "#egg=cnr_server"
                ),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["--hash", "sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f"],
                (
                    "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz#"
                    "egg=cnr_server --hash=sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02ad"
                    "b32189d912c7f"
                ),
            ),
            (
                "git+https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [],
                (
                    "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz#"
                    "egg=cnr_server"
                ),
            ),
            (
                "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz",
                [],
                "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz",
            ),
            (
                "git+https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["-e"],
                (
                    "-e cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz"
                    "#egg=cnr_server"
                ),
            ),
            (
                "git+https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["--hash", "sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f"],
                (
                    "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz#"
                    "egg=cnr_server --hash=sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02ad"
                    "b32189d912c7f"
                ),
            ),
        ),
    )
    def test_pip_requirement_to_str(
        self, requirement_line: str, requirement_options: list[str], expected_str_line: str
    ) -> None:
        """Test PipRequirement.__str__ method."""
        assert (
            str(pip.PipRequirement.from_line(requirement_line, requirement_options))
            == expected_str_line
        )

    @pytest.mark.parametrize(
        "requirement_line, requirement_options, new_values, expected_changes",
        (
            # Existing hashes are retained
            ("spam", ["--hash", "sha256:123"], {}, {}),
            # Existing hashes are replaced
            (
                "spam",
                ["--hash", "sha256:123"],
                {"hashes": ["sha256:234"]},
                {"hashes": ["sha256:234"]},
            ),
            # Hashes are added
            ("spam", [], {"hashes": ["sha256:234"]}, {"hashes": ["sha256:234"]}),
            # pypi is modified to url
            (
                "spam",
                [],
                {"url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz"},
                {
                    "download_line": "spam @ https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                },
            ),
            # url is modified to another url
            (
                "https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # vcs is modified to URL
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Editable option, "-e", is dropped when setting url
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["-e"],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "options": [],
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Editable option, "--e", is not dropped when url is not set
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["-e"],
                {},
                {},
            ),
            # Editable option, "--editable", is dropped when setting url
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["--editable"],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "options": [],
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Editable option, "--editable", is not dropped when url is not set
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["--editable"],
                {},
                {},
            ),
            # Environment markers persist
            (
                (
                    "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam"
                    '; python_version < "2.7"'
                ),
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam "
                        '; python_version < "2.7"'
                    ),
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Extras are cleared when setting a new URL
            (
                "spam[SALTY]",
                [],
                {"url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz"},
                {
                    "download_line": "spam @ https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                    "kind": "url",
                    "extras": [],
                    "url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                },
            ),
            # Extras are NOT cleared when a new URL is not set
            (
                "spam[SALTY]",
                [],
                {},
                {},
            ),
            # Version specs are cleared when setting a new URL
            (
                "spam==1.2.3",
                [],
                {"url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz"},
                {
                    "download_line": "spam @ https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                    "kind": "url",
                    "version_specs": [],
                    "url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                },
            ),
            # Version specs are NOT cleared when a new URL is not set
            (
                "spam==1.2.3",
                [],
                {},
                {},
            ),
            # Qualifiers persists
            (
                "https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam&spam=maps",
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#"
                        "egg=spam&spam=maps"
                    ),
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam&spam=maps",
                },
            ),
        ),
    )
    def test_pip_requirement_copy(
        self,
        requirement_line: str,
        requirement_options: list[str],
        new_values: Union[dict[str, str], dict[str, list[str]]],
        expected_changes: dict[str, str],
    ) -> None:
        """Test PipRequirement.copy method."""
        original_requirement = pip.PipRequirement.from_line(requirement_line, requirement_options)
        new_requirement = original_requirement.copy(**new_values)

        for attr in self.PIP_REQUIREMENT_ATTRS:
            expected_changes.setdefault(attr, getattr(original_requirement, attr))

        self._assert_pip_requirement(new_requirement, expected_changes)

    def test_invalid_kind_for_url(self) -> None:
        """Test extracting URL from a requirement that does not have one."""
        requirement = pip.PipRequirement()
        requirement.download_line = "aiowsgi==0.7"
        requirement.kind = "pypi"

        with pytest.raises(ValueError, match="Cannot extract URL from pypi requirement"):
            _ = requirement.url

    def _assert_pip_requirement(self, pip_requirement: Any, expected_requirement: Any) -> None:
        for attr, default_value in self.PIP_REQUIREMENT_ATTRS.items():
            expected_requirement.setdefault(attr, default_value)

        for attr, expected_value in expected_requirement.items():
            if attr in ("version_specs", "extras"):
                # Account for differences in order
                assert set(getattr(pip_requirement, attr)) == set(
                    expected_value
                ), f"unexpected value for {attr!r}"
            else:
                assert (
                    getattr(pip_requirement, attr) == expected_value
                ), f"unexpected value for {attr!r}"


class TestDownload:
    """Tests for dependency downloading."""

    def mock_requirements_file(
        self, requirements: Optional[list] = None, options: Optional[list] = None
    ) -> Any:
        """Mock a requirements.txt file."""
        return mock.Mock(requirements=requirements or [], options=options or [])

    def mock_requirement(
        self,
        package: Any,
        kind: Any,
        version_specs: Any = None,
        download_line: Any = None,
        hashes: Any = None,
        qualifiers: Any = None,
        url: Any = None,
    ) -> Any:
        """Mock a requirements.txt item. By default should pass validation."""
        if url is None and kind == "vcs":
            url = f"git+https://github.com/example@{GIT_REF}"
        elif url is None and kind == "url":
            url = "https://example.org/file.tar.gz"

        if hashes is None and qualifiers is None and kind == "url":
            qualifiers = {"cachito_hash": "sha256:abcdef"}

        return mock.Mock(
            package=package,
            kind=kind,
            version_specs=version_specs if version_specs is not None else [("==", "1")],
            download_line=download_line or package,
            hashes=hashes or [],
            qualifiers=qualifiers or {},
            url=url,
        )

    def mock_pypi_simple_package(
        self,
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

    @mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
    def test_process_non_existing_package_distributions(
        self,
        mock_get_project_page: mock.Mock,
        rooted_tmp_path: RootedPath,
    ) -> None:
        package_name = "does-not-exists"
        mock_requirement = self.mock_requirement(
            package_name, "pypi", version_specs=[("==", "1.0.0")]
        )

        mock_get_project_page.side_effect = pypi_simple.NoSuchProjectError(package_name, "URL")
        with pytest.raises(FetchError) as exc_info:
            pip._process_package_distributions(mock_requirement, rooted_tmp_path)

        assert (
            str(exc_info.value)
            == f"PyPI query failed: No details about project '{package_name}' available at URL"
        )

    @mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
    def test_process_existing_wheel_only_package(
        self,
        mock_get_project_page: mock.Mock,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        package_name = "aiowsgi"
        version = "0.1.0"
        mock_requirement = self.mock_requirement(
            package_name, "pypi", version_specs=[("==", version)]
        )

        file_1 = package_name + "-" + version + "-py3-none-any.whl"
        file_2 = package_name + "-" + version + "-manylinux1_x86_64.whl"

        mock_get_project_page.return_value = pypi_simple.ProjectPage(
            package_name,
            [
                self.mock_pypi_simple_package(file_1, version, "wheel"),
                self.mock_pypi_simple_package(file_2, version, "wheel"),
            ],
            None,
            None,
        )
        artifacts = pip._process_package_distributions(
            mock_requirement, rooted_tmp_path, allow_binary=True
        )
        assert artifacts[0].package_type != "sdist"
        assert len(artifacts) == 2
        assert f"No sdist found for package {package_name}=={version}" in caplog.text

    @pytest.mark.parametrize("allow_binary", (True, False))
    @mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
    def test_process_existing_package_without_any_distributions(
        self,
        mock_get_project_page: mock.Mock,
        allow_binary: bool,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        package_name = "aiowsgi"
        version = "0.1.0"
        mock_requirement = self.mock_requirement(
            package_name, "pypi", version_specs=[("==", version)]
        )

        with pytest.raises(PackageRejected) as exc_info:
            pip._process_package_distributions(
                mock_requirement, rooted_tmp_path, allow_binary=allow_binary
            )

        assert f"No sdist found for package {package_name}=={version}" in caplog.text
        assert (
            str(exc_info.value) == f"No distributions found for package {package_name}=={version}"
        )

        if allow_binary:
            assert str(exc_info.value.solution) == (
                "Please check that the package exists on PyPI or that the name"
                " and version are correct.\n"
            )
        else:
            assert str(exc_info.value.solution) == (
                "It seems that this version does not exist or isn't published as an"
                " sdist.\n"
                "Try to specify the dependency directly via a URL instead, for example,"
                " the tarball for a GitHub release.\n"
                "Alternatively, allow the use of wheels."
            )

    @mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
    def test_process_yanked_package_distributions(
        self,
        mock_get_project_page: mock.Mock,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        package_name = "aiowsgi"
        version = "0.1.0"
        mock_requirement = self.mock_requirement(
            package_name, "pypi", version_specs=[("==", version)]
        )

        mock_get_project_page.return_value = pypi_simple.ProjectPage(
            package_name,
            [self.mock_pypi_simple_package(filename=package_name, version=version, is_yanked=True)],
            None,
            None,
        )

        pip._process_package_distributions(mock_requirement, rooted_tmp_path)
        assert (
            f"The version {version} of package {package_name} is yanked, use a different version"
            in caplog.text
        )

    @pytest.mark.parametrize("use_user_hashes", (True, False))
    @pytest.mark.parametrize("use_pypi_digests", (True, False))
    @mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
    def test_process_package_distributions_with_checksums(
        self,
        mock_get_project_page: mock.Mock,
        use_user_hashes: bool,
        use_pypi_digests: bool,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        package_name = "aiowsgi"
        version = "0.1.0"
        mock_requirement = self.mock_requirement(
            package_name,
            "pypi",
            version_specs=[("==", version)],
            hashes=["sha128:abcdef", "sha256:abcdef", "sha512:xxxxxx"] if use_user_hashes else [],
        )

        mock_get_project_page.return_value = pypi_simple.ProjectPage(
            package_name,
            [
                self.mock_pypi_simple_package(package_name, version, "sdist"),
                self.mock_pypi_simple_package(
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
        artifacts = pip._process_package_distributions(
            mock_requirement, rooted_tmp_path, allow_binary=True
        )

        if use_user_hashes and use_pypi_digests:
            assert (
                f"{package_name}: using intersection of requirements-file and PyPI-reported checksums"
                in caplog.text
            )
            assert artifacts[1].checksums_to_match == set(
                [ChecksumInfo("sha128", "abcdef"), ChecksumInfo("sha256", "abcdef")]
            )

        elif use_user_hashes and not use_pypi_digests:
            assert f"{package_name}: using requirements-file checksums" in caplog.text
            assert artifacts[1].checksums_to_match == set(
                [
                    ChecksumInfo("sha128", "abcdef"),
                    ChecksumInfo("sha256", "abcdef"),
                    ChecksumInfo("sha512", "xxxxxx"),
                ]
            )

        elif use_pypi_digests and not use_user_hashes:
            assert f"{package_name}: using PyPI-reported checksums" in caplog.text
            assert artifacts[1].checksums_to_match == set(
                [
                    ChecksumInfo("sha128", "abcdef"),
                    ChecksumInfo("sha256", "abcdef"),
                    ChecksumInfo("sha512", "yyyyyy"),
                ]
            )

        elif not use_user_hashes and not use_pypi_digests:
            assert (
                f"{package_name}: no checksums reported by PyPI or specified in requirements file"
                in caplog.text
            )
            assert artifacts[1].checksums_to_match == set()

    @mock.patch.object(pypi_simple.PyPISimple, "get_project_page")
    def test_process_package_distributions_with_different_checksums(
        self,
        mock_get_project_page: mock.Mock,
        rooted_tmp_path: RootedPath,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        package_name = "aiowsgi"
        version = "0.1.0"
        mock_requirement = self.mock_requirement(
            package_name, "pypi", version_specs=[("==", version)], hashes=["sha128:abcdef"]
        )

        mock_get_project_page.return_value = pypi_simple.ProjectPage(
            package_name,
            [
                self.mock_pypi_simple_package(package_name, version),
                self.mock_pypi_simple_package(
                    package_name, version, "wheel", digests={"sha256": "abcdef"}
                ),
            ],
            None,
            None,
        )

        artifacts = pip._process_package_distributions(
            mock_requirement, rooted_tmp_path, allow_binary=True
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
        self,
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

        mock_requirement = self.mock_requirement(
            "foo", "pypi", version_specs=[("==", requested_version)]
        )
        mock_get_project_page.return_value = pypi_simple.ProjectPage(
            "foo",
            [
                self.mock_pypi_simple_package(filename="foo.tar.gz", version=actual_version),
                self.mock_pypi_simple_package(filename="foo-manylinux.whl", version=actual_version),
            ],
            None,
            None,
        )

        artifacts = pip._process_package_distributions(mock_requirement, rooted_tmp_path)
        assert artifacts[0].package_type == "sdist"
        assert artifacts[0].version == requested_version
        assert all(w.version == requested_version for w in artifacts[1:])

    def test_sdist_sorting(self) -> None:
        """Test that sdist preference key can be used for sorting in the expected order."""
        unyanked_tar_gz = make_dpi(name="unyanked.tar.gz", is_yanked=False)
        unyanked_zip = make_dpi(name="unyanked.zip", is_yanked=False)
        unyanked_tar_bz2 = make_dpi(name="unyanked.tar.bz2", is_yanked=False)
        yanked_tar_gz = make_dpi(name="yanked.tar.gz", is_yanked=True)
        yanked_zip = make_dpi(name="yanked.zip", is_yanked=True)
        yanked_tar_bz2 = make_dpi(name="yanked.tar.bz2", is_yanked=True)

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

        sdists.sort(key=pip._sdist_preference)
        assert sdists == expect_order

    @mock.patch("hermeto.core.package_managers.pip.clone_as_tarball")
    def test_download_vcs_package(
        self,
        mock_clone_as_tarball: Any,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Test downloading of a single VCS package."""
        vcs_url = f"git+https://github.com/spam/eggs@{GIT_REF}"

        mock_requirement = self.mock_requirement(
            "eggs", "vcs", url=vcs_url, download_line=f"eggs @ {vcs_url}"
        )

        download_info = pip._download_vcs_package(mock_requirement, rooted_tmp_path)

        assert download_info == {
            "package": "eggs",
            "path": rooted_tmp_path.join_within_root(
                "github.com", "spam", "eggs", f"eggs-external-gitcommit-{GIT_REF}.tar.gz"
            ).path,
            "url": "https://github.com/spam/eggs",
            "ref": GIT_REF,
            "namespace": "spam",
            "repo": "eggs",
            "host": "github.com",
        }

        download_path = download_info["path"]

        mock_clone_as_tarball.assert_called_once_with(
            "https://github.com/spam/eggs", GIT_REF, to_path=download_path
        )

    @pytest.mark.parametrize("hash_as_qualifier", [True, False])
    @pytest.mark.parametrize(
        "host_in_url, trusted_hosts, host_is_trusted",
        [
            ("example.org", [], False),
            ("example.org", ["example.org"], True),
            ("example.org:443", ["example.org:443"], True),
            # 'host' in URL does not match 'host:port' in trusted hosts
            ("example.org", ["example.org:443"], False),
            # 'host:port' in URL *does* match 'host' in trusted hosts
            ("example.org:443", ["example.org"], True),
        ],
    )
    @mock.patch("hermeto.core.package_managers.pip.download_binary_file")
    def test_download_url_package(
        self,
        mock_download_file: Any,
        hash_as_qualifier: bool,
        host_in_url: bool,
        trusted_hosts: list[str],
        host_is_trusted: bool,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Test downloading of a single URL package."""
        # Add the #cachito_package fragment to make sure the .tar.gz extension
        # will be found even if the URL does not end with it
        original_url = f"https://{host_in_url}/foo.tar.gz#cachito_package=foo"
        url_with_hash = f"{original_url}&cachito_hash=sha256:abcdef"
        if hash_as_qualifier:
            original_url = url_with_hash

        mock_requirement = self.mock_requirement(
            "foo",
            "url",
            url=original_url,
            download_line=f"foo @ {original_url}",
            hashes=["sha256:abcdef"] if not hash_as_qualifier else [],
            qualifiers={"cachito_hash": "sha256:abcdef"} if hash_as_qualifier else {},
        )

        download_info = pip._download_url_package(
            mock_requirement,
            rooted_tmp_path,
            set(trusted_hosts),
        )

        assert download_info == {
            "package": "foo",
            "path": rooted_tmp_path.join_within_root(
                "external-foo", "foo-external-sha256-abcdef.tar.gz"
            ).path,
            "original_url": original_url,
            "url_with_hash": url_with_hash,
        }

        download_path = download_info["path"]
        mock_download_file.assert_called_once_with(
            original_url, download_path, insecure=host_is_trusted
        )

    @pytest.mark.parametrize(
        "original_url, url_with_hash",
        [
            (
                "http://example.org/file.zip",
                "http://example.org/file.zip#cachito_hash=sha256:abcdef",
            ),
            (
                "http://example.org/file.zip#egg=spam",
                "http://example.org/file.zip#egg=spam&cachito_hash=sha256:abcdef",
            ),
        ],
    )
    def test_add_cachito_hash_to_url(self, original_url: str, url_with_hash: str) -> None:
        """Test adding the #cachito_hash fragment to URLs."""
        hsh = "sha256:abcdef"
        assert pip._add_cachito_hash_to_url(urlparse(original_url), hsh) == url_with_hash

    def test_ignored_and_rejected_options(self, caplog: LogCaptureFixture) -> None:
        """
        Test ignored and rejected options.

        All ignored options should be logged, all rejected options should be in error message.
        """
        all_rejected = [
            "--extra-index-url",
            "--no-index",
            "-f",
            "--find-links",
            "--only-binary",
        ]
        options = all_rejected + ["-c", "constraints.txt", "--use-feature", "some_feature", "--foo"]
        req_file = self.mock_requirements_file(options=options)
        with pytest.raises(UnsupportedFeature) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        err_msg = (
            f"{APP_NAME} does not support the following options: --extra-index-url, "
            "--no-index, -f, --find-links, --only-binary"
        )
        assert str(exc_info.value) == err_msg

        log_msg = f"{APP_NAME} will ignore the following options: -c, --use-feature, --foo"
        assert log_msg in caplog.text

    @pytest.mark.parametrize(
        "version_specs",
        [
            [],
            [("<", "1")],
            [("==", "1"), ("<", "2")],
            [("==", "1"), ("==", "1")],  # Probably no reason to handle this?
        ],
    )
    def test_pypi_dep_not_pinned(self, version_specs: list[str]) -> None:
        """Test that unpinned PyPI deps cause a PackageRejected error."""
        req = self.mock_requirement("foo", "pypi", version_specs=version_specs)
        req_file = self.mock_requirements_file(requirements=[req])
        with pytest.raises(PackageRejected) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)
        msg = f"Requirement must be pinned to an exact version: {req.download_line}"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize(
        "url",
        [
            # there is no ref
            "git+https://github.com/spam/eggs",
            "git+https://github.com/spam/eggs@",
            # ref is too short
            "git+https://github.com/spam/eggs@abcdef",
            # ref is in the wrong place
            f"git+https://github.com@{GIT_REF}/spam/eggs",
            f"git+https://github.com/spam/eggs#@{GIT_REF}",
        ],
    )
    def test_vcs_dep_no_git_ref(self, url: str) -> None:
        """Test that VCS deps with no git ref cause a PackageRejected error."""
        req = self.mock_requirement("eggs", "vcs", url=url, download_line=f"eggs @ {url}")
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(PackageRejected) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        msg = f"No git ref in {req.download_line} (expected 40 hexadecimal characters)"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize("scheme", ["svn", "svn+https"])
    def test_vcs_dep_not_git(self, scheme: str) -> None:
        """Test that VCS deps not from git cause an UnsupportedFeature error."""
        url = f"{scheme}://example.org/spam/eggs"
        req = self.mock_requirement("eggs", "vcs", url=url, download_line=f"eggs @ {url}")
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(UnsupportedFeature) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        msg = f"Unsupported VCS for {req.download_line}: {scheme} (only git is supported)"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize(
        "hashes, cachito_hash, total",
        [
            ([], None, 0),  # No --hash, no #cachito_hash
            (["sha256:123456", "sha256:abcdef"], None, 2),  # 2x --hash
            (["sha256:123456"], "sha256:abcdef", 2),  # 1x --hash, #cachito_hash
        ],
    )
    def test_url_dep_invalid_hash_count(
        self, hashes: list[str], cachito_hash: Optional[str], total: int
    ) -> None:
        """Test that if URL requirement specifies 0 or more than 1 hash, validation fails."""
        if cachito_hash:
            qualifiers = {"cachito_hash": cachito_hash}
        else:
            qualifiers = {}

        url = "http://example.org/foo.tar.gz"
        req = self.mock_requirement(
            "foo", "url", hashes=hashes, qualifiers=qualifiers, download_line=f"foo @ {url}"
        )
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(PackageRejected) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        assert str(exc_info.value) == (
            f"URL requirement must specify exactly one hash, but specifies {total}: foo @ {url}."
        )

    @pytest.mark.parametrize(
        "url",
        [
            # .rar is not a valid sdist extension
            "http://example.org/file.rar",
            # extension is in the wrong place
            "http://example.tar.gz/file",
            "http://example.org/file?filename=file.tar.gz",
        ],
    )
    def test_url_dep_unknown_file_ext(self, url: str) -> None:
        """Test that missing / unknown file extension in URL causes a validation error."""
        req = self.mock_requirement("foo", "url", url=url, download_line=f"foo @ {url}")
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(PackageRejected) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        assert str(exc_info.value) == (
            f"URL for requirement does not contain any recognized file extension: "
            f"{req.download_line} (expected one of .zip, .tar.gz, .tar.bz2, .tar.xz, .tar.Z, .tar)"
        )

    @pytest.mark.parametrize(
        "global_require_hash, local_hash", [(True, False), (False, True), (True, True)]
    )
    @pytest.mark.parametrize("requirement_kind", ["pypi", "vcs"])
    def test_requirement_missing_hash(
        self,
        global_require_hash: bool,
        local_hash: bool,
        requirement_kind: str,
        caplog: LogCaptureFixture,
    ) -> None:
        """Test that missing hashes cause a validation error."""
        if global_require_hash:
            options = ["--require-hashes"]
        else:
            options = []

        if local_hash:
            req_1 = self.mock_requirement("foo", requirement_kind, hashes=["sha256:abcdef"])
        else:
            req_1 = self.mock_requirement("foo", requirement_kind)

        req_2 = self.mock_requirement("bar", requirement_kind)
        req_file = self.mock_requirements_file(requirements=[req_1, req_2], options=options)

        with pytest.raises(PackageRejected) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        if global_require_hash:
            assert "Global --require-hashes option used, will require hashes" in caplog.text
            bad_req = req_2 if local_hash else req_1
        else:
            msg = "At least one dependency uses the --hash option, will require hashes"
            assert msg in caplog.text
            bad_req = req_2

        msg = f"Hash is required, dependency does not specify any: {bad_req.download_line}"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize(
        "requirement_kind, hash_in_url",
        [("pypi", False), ("vcs", False), ("url", True), ("url", False)],
    )
    def test_malformed_hash(self, requirement_kind: str, hash_in_url: bool) -> None:
        """Test that invalid hash specifiers cause a validation error."""
        if hash_in_url:
            hashes = []
            qualifiers = {"cachito_hash": "malformed"}
        else:
            hashes = ["malformed"]
            qualifiers = {}

        req = self.mock_requirement("foo", requirement_kind, hashes=hashes, qualifiers=qualifiers)
        req_file = self.mock_requirements_file(requirements=[req])

        with pytest.raises(PackageRejected) as exc_info:
            pip._download_dependencies(RootedPath("/output"), req_file)

        msg = "Not a valid hash specifier: 'malformed' (expected 'algorithm:digest')"
        assert str(exc_info.value) == msg

    @pytest.mark.parametrize("allow_binary", [True, False])
    @pytest.mark.parametrize(
        "index_url", [None, pypi_simple.PYPI_SIMPLE_ENDPOINT, CUSTOM_PYPI_ENDPOINT]
    )
    @pytest.mark.parametrize("missing_req_file_checksum", [True, False])
    @mock.patch("hermeto.core.package_managers.pip._process_package_distributions")
    @mock.patch("hermeto.core.package_managers.pip.must_match_any_checksum")
    @mock.patch.object(Path, "unlink")
    @mock.patch("hermeto.core.package_managers.pip.async_download_files")
    @mock.patch("hermeto.core.package_managers.pip._check_metadata_in_sdist")
    def test_download_dependencies_pypi(
        self,
        mock_check_metadata_in_sdist: mock.Mock,
        mock_async_download_files: mock.Mock,
        mock_unlink: mock.Mock,
        mock_must_match_any_checksum: mock.Mock,
        mock_process_package_distributions: mock.Mock,
        missing_req_file_checksum: bool,
        index_url: Optional[str],
        allow_binary: bool,
        rooted_tmp_path: RootedPath,
        caplog: LogCaptureFixture,
    ) -> None:
        """
        Test dependency downloading.

        Mock the helper functions used for downloading here, test them properly elsewhere.
        """
        # <setup>
        req = self.mock_requirement(
            "foo", "pypi", download_line="foo==1.0", version_specs=[("==", "1.0")]
        )
        # match sdist hash, match wheel0 hash, mismatch wheel1 hash, no hash
        # for wheel2
        req.hashes = ["sha256:abcdef", "sha256:defabc", "sha256:feebaa"]

        pypi_checksum_sdist = ChecksumInfo("sha256", "abcdef")
        pypi_checksum_wheels = [
            ChecksumInfo("sha256", "defabc"),
            ChecksumInfo("sha256", "fedbac"),
            ChecksumInfo("sha256", "cbafed"),
        ]
        req_file_checksum_sdist: ChecksumInfo = pypi_checksum_sdist
        # This isn't being auto-created as expected, due to mocking
        # wheel0 hash, mismatch wheel1 hash, no hash for wheel2
        req_file_checksums_wheels = {
            pypi_checksum_wheels[0],
            pypi_checksum_wheels[1],
        }

        options = []
        if index_url:
            options.append("--index-url")
            options.append(index_url)

        req_file = self.mock_requirements_file(
            requirements=[req],
            options=options,
        )

        expect_index_url = index_url or pypi_simple.PYPI_SIMPLE_ENDPOINT

        pip_deps = rooted_tmp_path.join_within_root("deps", "pip")

        sdist_download = pip_deps.join_within_root("foo-1.0.tar.gz").path

        sdist_DPI = make_dpi(
            "foo",
            path=sdist_download,
            index_url=expect_index_url,
            pypi_checksum={pypi_checksum_sdist},
            req_file_checksums=set() if missing_req_file_checksum else {req_file_checksum_sdist},
        )
        sdist_d_i = sdist_DPI.download_info | {
            "kind": "pypi",
            "requirement_file": str(req_file.file_path.subpath_from_root),
            "missing_req_file_checksum": missing_req_file_checksum,
            "package_type": "sdist",
            "index_url": expect_index_url,
        }
        verify_sdist_checksum_call = mock.call(sdist_download, {pypi_checksum_sdist})
        expected_downloads = [sdist_d_i]

        wheels_DPI: list[pip.DistributionPackageInfo] = []
        if allow_binary:
            wheel_0_download = pip_deps.join_within_root("foo-1.0-cp35-many-linux.whl").path
            wheel_1_download = pip_deps.join_within_root("foo-1.0-cp25-win32.whl").path
            wheel_2_download = pip_deps.join_within_root("foo-1.0-any.whl").path
            wheel_downloads: list[dict[str, Any]] = []

            for wheel_path, pypi_checksum in zip(
                [wheel_0_download, wheel_1_download, wheel_2_download],
                pypi_checksum_wheels,
            ):
                dpi = make_dpi(
                    "foo",
                    package_type="wheel",
                    path=wheel_path,
                    index_url=expect_index_url,
                    pypi_checksum={pypi_checksum},
                    req_file_checksums=(
                        set() if missing_req_file_checksum else req_file_checksums_wheels
                    ),
                )
                wheels_DPI.append(dpi)
                wheel_downloads.append(
                    dpi.download_info
                    | {
                        "kind": "pypi",
                        "requirement_file": str(req_file.file_path.subpath_from_root),
                        "missing_req_file_checksum": missing_req_file_checksum,
                        "package_type": "wheel",
                        "index_url": expect_index_url,
                    }
                )

            verify_wheel0_checksum_call = mock.call(
                wheel_0_download, {ChecksumInfo("sha256", "defabc")}
            )
            verify_wheel1_checksum_call = mock.call(
                wheel_1_download, {ChecksumInfo("sha256", "fedbac")}
            )
            verify_wheel2_checksum_call = mock.call(
                wheel_2_download, {ChecksumInfo("sha256", "cbafed")}
            )
            expected_downloads.extend(wheel_downloads)

        mock_process_package_distributions.return_value = [sdist_DPI] + wheels_DPI

        if allow_binary:
            mock_must_match_any_checksum.side_effect = [
                None,  # sdist_download
                None,  # wheel_0_download - checksums OK
                PackageRejected("", solution=None),  # wheel_1_download - checksums NOK
                PackageRejected("", solution=None),  # wheel_2_download - no checksums to verify
            ]
        else:
            mock_must_match_any_checksum.side_effect = [
                None,  # sdist_download
            ]
        # </setup>

        # <call>
        found_downloads = pip._download_dependencies(rooted_tmp_path, req_file, allow_binary)
        assert found_downloads == expected_downloads
        assert pip_deps.path.is_dir()
        # </call>

        # <check calls that must always be made>
        mock_check_metadata_in_sdist.assert_called_once_with(sdist_DPI.path)
        mock_process_package_distributions.assert_called_once_with(
            req, pip_deps, allow_binary, expect_index_url
        )
        # </check calls that must always be made>

        verify_checksums_calls = [
            verify_sdist_checksum_call,
        ]

        if allow_binary:
            if missing_req_file_checksum:
                verify_checksums_calls.extend(
                    [
                        verify_wheel0_checksum_call,
                        verify_wheel1_checksum_call,
                        verify_wheel2_checksum_call,
                    ]
                )
            # req file checksums exist
            else:
                verify_checksums_calls.extend(
                    [
                        verify_wheel0_checksum_call,
                        verify_wheel1_checksum_call,
                    ]
                )

        mock_must_match_any_checksum.assert_has_calls(verify_checksums_calls)
        assert mock_must_match_any_checksum.call_count == len(verify_checksums_calls)

        # </check calls to checksum verification method>

        # <check basic logging output>
        assert f"-- Processing requirement line '{req.download_line}'" in caplog.text
        assert (
            f"Successfully processed '{req.download_line}' in path 'deps/pip/foo-1.0.tar.gz'"
        ) in caplog.text
        # </check basic logging output>

        # <check downloaded wheels>
        if allow_binary:
            # wheel 1 does not match any checksums
            assert (
                f"Download '{wheel_1_download.name}' was removed from the output directory"
            ) in caplog.text
        # </check downloaded wheels>

    @pytest.mark.parametrize("checksum_match", [True, False])
    @pytest.mark.parametrize("trusted_hosts", [[], ["example.org"]])
    @mock.patch("hermeto.core.package_managers.pip._download_url_package")
    @mock.patch("hermeto.core.package_managers.pip.must_match_any_checksum")
    @mock.patch.object(Path, "unlink")
    @mock.patch("hermeto.core.package_managers.pip.async_download_files")
    @mock.patch("hermeto.core.package_managers.pip.download_binary_file")
    def test_download_dependencies_url(
        self,
        mock_download_binary_file: mock.Mock,
        mock_async_download_files: mock.Mock,
        mock_unlink: mock.Mock,
        mock_must_match_any_checksum: mock.Mock,
        mock_download_url_package: mock.Mock,
        trusted_hosts: list[str],
        checksum_match: bool,
        rooted_tmp_path: RootedPath,
        caplog: LogCaptureFixture,
    ) -> None:
        """
        Test dependency downloading.

        Mock the helper functions used for downloading here, test them properly
        elsewhere.

        Note that we're only testing the `cachito_hash` scenario. URL deps can
        also be hashed in 'requirements.txt' like any other pip dep. We really
        should expand this test, at some point, to include testing the `--hash`
        option in 'requirements.txt'.

        URL deps *must always* have a checksum, so we're only testing the case
        where the checksum *doesn't match* (we check for *missing*
        checksums elsewhere for URL deps).
        """
        # <setup>
        plain_url = "https://example.org/bar.tar.gz#cachito_hash=sha256:654321"
        url_req = self.mock_requirement(
            "bar",
            "url",
            download_line=f"bar @ {plain_url}",
            url=plain_url,
            qualifiers={"cachito_hash": "sha256:654321"},
        )

        options = []
        for host in trusted_hosts:
            options.append("--trusted-host")
            options.append(host)

        req_file = self.mock_requirements_file(
            requirements=[
                url_req,
            ],
            options=options,
        )

        pip_deps = rooted_tmp_path.join_within_root("deps", "pip")

        url_download = pip_deps.join_within_root(
            "external-bar", "bar-external-sha256-654321.tar.gz"
        ).path

        url_download_info = {
            "package": "bar",
            "path": url_download,
            "requirement_file": str(req_file.file_path.subpath_from_root),
            # Checksums are *mandatory*
            "missing_req_file_checksum": False,
            "package_type": "",
            "original_url": plain_url,
            "url_with_hash": plain_url,
        }

        mock_download_url_package.return_value = deepcopy(url_download_info)

        mock_must_match_any_checksum.side_effect = [
            None if checksum_match else PackageRejected("", solution=None),
        ]
        # </setup>

        # <call>
        found_download = pip._download_dependencies(rooted_tmp_path, req_file, False)
        expected_download = [
            url_download_info | {"kind": "url"},
        ]
        assert found_download == expected_download
        assert pip_deps.path.is_dir()
        # </call>

        # <check calls that must always be made>
        mock_download_url_package.assert_called_once_with(url_req, pip_deps, set(trusted_hosts))
        # </check calls that must always be made>

        # <check calls to checksum verification method>
        if checksum_match:
            # This looks confusing, but as mentioned above, we're currently only
            # testing the `cachito_hash` hash, which is a loophole allowing
            # hashed URLs and unhashed VCS deps to coexist in a
            # 'requirements.txt' file.
            msg = "No hash options used, will not require hashes unless HTTP(S) dependencies"
        else:
            msg = (
                "Download 'bar-external-sha256-654321.tar.gz' was removed from the output directory"
            )
        assert msg in caplog.text
        verify_checksum_call = [mock.call(url_download, [ChecksumInfo("sha256", "654321")])]
        mock_must_match_any_checksum.assert_has_calls(verify_checksum_call)
        assert mock_must_match_any_checksum.call_count == 1
        # </check calls to checksum verification method>

        # <check basic logging output>
        assert f"-- Processing requirement line '{url_req.download_line}'" in caplog.text
        assert (
            f"Successfully processed '{url_req.download_line}' in path 'deps/pip/external-bar/"
            f"bar-external-sha256-654321.tar.gz'"
        ) in caplog.text
        # </check basic logging output>

    @mock.patch("hermeto.core.package_managers.pip._download_vcs_package")
    @mock.patch.object(Path, "unlink")
    @mock.patch("hermeto.core.package_managers.pip.async_download_files")
    @mock.patch("hermeto.core.scm.clone_as_tarball")
    def test_download_dependencies_vcs(
        self,
        mock_clone_as_tarball: mock.Mock,
        mock_async_download_files: mock.Mock,
        mock_unlink: mock.Mock,
        mock_download_vcs_package: mock.Mock,
        rooted_tmp_path: RootedPath,
        caplog: LogCaptureFixture,
    ) -> None:
        """
        Test dependency downloading.

        Mock the helper functions used for downloading here, test them properly elsewhere.

        VCS deps *cannot* be hashed, so we are not checking any checksum-related functions.
        """
        # <setup>
        # "egg" has a very specific meaning in Python packaging world. Let's avoid
        # confusion
        git_url = f"https://github.com/spam/bacon@{GIT_REF}"

        vcs_req = self.mock_requirement(
            "bacon", "vcs", download_line=f"bacon @ git+{git_url}", url=f"git+{git_url}"
        )

        req_file = self.mock_requirements_file(
            requirements=[vcs_req],
        )

        pip_deps = rooted_tmp_path.join_within_root("deps", "pip")

        vcs_download = pip_deps.join_within_root(
            "github.com",
            "spam",
            "bacon",
            f"bacon-external-gitcommit-{GIT_REF}.tar.gz",
        ).path

        vcs_download_info = {
            "package": "bacon",
            "path": vcs_download,
            "requirement_file": str(req_file.file_path.subpath_from_root),
            # vcs deps *can't have* checksums
            "missing_req_file_checksum": True,
            "package_type": "",
            "repo": "bacon",
            # etc., not important for this test
        }

        mock_download_vcs_package.return_value = deepcopy(vcs_download_info)
        # </setup>

        # <call>
        found_download = pip._download_dependencies(rooted_tmp_path, req_file, False)
        expected_download = [
            vcs_download_info | {"kind": "vcs"},
        ]
        assert found_download == expected_download
        assert pip_deps.path.is_dir()
        # </call>

        # <check calls that must always be made>
        mock_download_vcs_package.assert_called_once_with(vcs_req, pip_deps)
        # </check calls that must always be made>

        # <check calls to checksum verification method>
        msg = (
            "No hash options used, will not require hashes unless HTTP(S) dependencies are present."
        )
        assert msg in caplog.text
        # </check calls to checksum verification method>

        # <check basic logging output>
        assert f"-- Processing requirement line '{vcs_req.download_line}'" in caplog.text
        assert (
            f"Successfully processed '{vcs_req.download_line}' in path 'deps/pip/github.com/spam/bacon/"
            f"bacon-external-gitcommit-{GIT_REF}.tar.gz'"
        ) in caplog.text
        # </check basic logging output>

    @mock.patch("hermeto.core.package_managers.pip._process_package_distributions")
    @mock.patch("hermeto.core.package_managers.pip.async_download_files")
    @mock.patch("hermeto.core.package_managers.pip._check_metadata_in_sdist")
    def test_download_from_requirement_files(
        self,
        _check_metadata_in_sdist: mock.Mock,
        async_download_files: mock.Mock,
        _process_package_distributions: mock.Mock,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Test downloading dependencies from a requirement file list."""
        req_file1 = rooted_tmp_path.join_within_root("requirements.txt")
        req_file1.path.write_text("foo==1.0.0")
        req_file2 = rooted_tmp_path.join_within_root("requirements-alt.txt")
        req_file2.path.write_text("bar==0.0.1")

        pip_deps = rooted_tmp_path.join_within_root("deps", "pip")

        pypi_download1 = pip_deps.join_within_root("foo", "foo-1.0.0.tar.gz").path
        pypi_download2 = pip_deps.join_within_root("bar", "bar-0.0.1.tar.gz").path

        pypi_package1 = make_dpi("foo", "1.0.0", path=pypi_download1)
        pypi_package2 = make_dpi("bar", "0.0.1", path=pypi_download2)

        _process_package_distributions.side_effect = [[pypi_package1], [pypi_package2]]

        downloads = pip._download_from_requirement_files(rooted_tmp_path, [req_file1, req_file2])
        assert downloads == [
            pypi_package1.download_info
            | {
                "kind": "pypi",
                "requirement_file": str(req_file1.subpath_from_root),
                "missing_req_file_checksum": True,
                "package_type": "sdist",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            },
            pypi_package2.download_info
            | {
                "kind": "pypi",
                "requirement_file": str(req_file2.subpath_from_root),
                "missing_req_file_checksum": True,
                "package_type": "sdist",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            },
        ]
        _check_metadata_in_sdist.assert_has_calls(
            [mock.call(pypi_package1.path), mock.call(pypi_package2.path)], any_order=True
        )


@pytest.mark.parametrize("exists", [True, False])
@pytest.mark.parametrize("devel", [True, False])
def test_default_requirement_file_list(
    rooted_tmp_path: RootedPath, exists: bool, devel: bool
) -> None:
    req_file = None
    requirements = pip.DEFAULT_REQUIREMENTS_FILE
    build_requirements = pip.DEFAULT_BUILD_REQUIREMENTS_FILE
    if exists:
        filename = build_requirements if devel else requirements
        req_file = rooted_tmp_path.join_within_root(filename)
        req_file.path.write_text("nothing to see here\n")

    req_files = pip._default_requirement_file_list(rooted_tmp_path, devel)
    expected = [req_file] if req_file else []
    assert req_files == expected


@mock.patch("hermeto.core.package_managers.pip._get_pip_metadata")
def test_resolve_pip_no_deps(mock_metadata: mock.Mock, rooted_tmp_path: RootedPath) -> None:
    mock_metadata.return_value = ("foo", "1.0")
    pkg_info = pip._resolve_pip(
        rooted_tmp_path,
        rooted_tmp_path.join_within_root("output"),
        rooted_tmp_path.join_within_root("."),
    )
    expected = {
        "package": {"name": "foo", "version": "1.0", "type": "pip"},
        "dependencies": [],
        "packages_containing_rust_code": [],
        "requirements": [],
    }
    assert pkg_info == expected


@mock.patch("hermeto.core.package_managers.pip._get_pip_metadata")
def test_resolve_pip_invalid_req_file_path(
    mock_metadata: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    mock_metadata.return_value = ("foo", "1.0")
    invalid_path = Path("foo/bar.txt")
    expected_error = (
        f"The requirements file does not exist: {rooted_tmp_path.join_within_root(invalid_path)}"
    )
    requirement_files = [invalid_path]
    with pytest.raises(PackageRejected, match=expected_error):
        pip._resolve_pip(
            rooted_tmp_path,
            rooted_tmp_path.join_within_root("output"),
            rooted_tmp_path.join_within_root("."),
            requirement_files,
            None,
        )


@mock.patch("hermeto.core.package_managers.pip._get_pip_metadata")
def test_resolve_pip_invalid_bld_req_file_path(
    mock_metadata: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    mock_metadata.return_value = ("foo", "1.0")
    invalid_path = Path("foo/bar.txt")
    expected_error = (
        f"The requirements file does not exist: {rooted_tmp_path.join_within_root(invalid_path)}"
    )
    build_requirement_files = [invalid_path]
    with pytest.raises(PackageRejected, match=expected_error):
        pip._resolve_pip(
            rooted_tmp_path,
            rooted_tmp_path.join_within_root("output"),
            rooted_tmp_path.join_within_root("."),
            None,
            build_requirement_files,
        )


@pytest.mark.parametrize("custom_requirements", [True, False])
@mock.patch("hermeto.core.package_managers.pip._get_pip_metadata")
@mock.patch("hermeto.core.package_managers.pip._download_dependencies")
@mock.patch("hermeto.core.package_managers.pip._filter_packages_with_rust_code")
def test_resolve_pip(
    mock_filter_cargo_packages: mock.Mock,
    mock_download: mock.Mock,
    mock_metadata: mock.Mock,
    rooted_tmp_path: RootedPath,
    custom_requirements: bool,
) -> None:
    relative_req_file_path = Path("req.txt")
    relative_build_req_file_path = Path("breq.txt")
    req_file = rooted_tmp_path.join_within_root(pip.DEFAULT_REQUIREMENTS_FILE)
    build_req_file = rooted_tmp_path.join_within_root(pip.DEFAULT_BUILD_REQUIREMENTS_FILE)
    if custom_requirements:
        req_file = rooted_tmp_path.join_within_root(relative_req_file_path)
        build_req_file = rooted_tmp_path.join_within_root(relative_build_req_file_path)

    req_file.path.write_text("bar==2.1")
    build_req_file.path.write_text("baz==0.0.5")
    mock_filter_cargo_packages.return_value = []
    mock_metadata.return_value = ("foo", "1.0")
    mock_download.side_effect = [
        [
            {
                "version": "2.1",
                "kind": "pypi",
                "package": "bar",
                "path": "some/path",
                "requirement_file": str(req_file.subpath_from_root),
                "missing_req_file_checksum": False,
                "package_type": "sdist",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            }
        ],
        [
            {
                "version": "0.0.5",
                "kind": "pypi",
                "package": "baz",
                "path": "another/path",
                "requirement_file": str(build_req_file.subpath_from_root),
                "missing_req_file_checksum": False,
                "package_type": "sdist",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            }
        ],
    ]
    if custom_requirements:
        pkg_info = pip._resolve_pip(
            rooted_tmp_path,
            rooted_tmp_path.join_within_root("output"),
            rooted_tmp_path.join_within_root("."),
            requirement_files=[relative_req_file_path],
            build_requirement_files=[relative_build_req_file_path],
        )
    else:
        pkg_info = pip._resolve_pip(
            rooted_tmp_path,
            rooted_tmp_path.join_within_root("output"),
            rooted_tmp_path.join_within_root("."),
        )

    expected = {
        "package": {"name": "foo", "version": "1.0", "type": "pip"},
        "dependencies": [
            {
                "name": "bar",
                "version": "2.1",
                "type": "pip",
                "build_dependency": False,
                "kind": "pypi",
                "requirement_file": "req.txt" if custom_requirements else "requirements.txt",
                "missing_req_file_checksum": False,
                "package_type": "sdist",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            },
            {
                "name": "baz",
                "version": "0.0.5",
                "type": "pip",
                "build_dependency": True,
                "kind": "pypi",
                "requirement_file": "breq.txt" if custom_requirements else "requirements-build.txt",
                "missing_req_file_checksum": False,
                "package_type": "sdist",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            },
        ],
        "packages_containing_rust_code": [],
        "requirements": [req_file, build_req_file],
    }
    assert pkg_info == expected


@pytest.mark.parametrize(
    "component_kind, url",
    (
        ["vcs", f"git+https://github.com/cachito/mypkg.git@{'f' * 40}?egg=mypkg"],
        ["url", "https://files.cachito.rocks/mypkg.tar.gz"],
    ),
)
def test_get_external_requirement_filepath(component_kind: str, url: str) -> None:
    requirement = mock.Mock(
        kind=component_kind, url=url, package="package", hashes=["sha256:noRealHash"]
    )
    filepath = pip._get_external_requirement_filepath(requirement)
    if component_kind == "url":
        assert filepath == Path("external-package", "package-external-sha256-noRealHash.tar.gz")
    elif component_kind == "vcs":
        assert filepath == Path(
            "github.com", "cachito", "mypkg", f"mypkg-external-gitcommit-{'f' * 40}.tar.gz"
        )
    else:
        raise AssertionError()


@pytest.mark.parametrize(
    "sdist_filename",
    [
        "myapp-0.1.tar",
        "myapp-0.1.tar.bz2",
        "myapp-0.1.tar.gz",
        "myapp-0.1.tar.xz",
        "myapp-0.1.zip",
    ],
)
def test_check_metadata_from_sdist(sdist_filename: str, data_dir: Path) -> None:
    sdist_path = data_dir / sdist_filename
    pip._check_metadata_in_sdist(sdist_path)


@pytest.mark.parametrize(
    "sdist_filename",
    [
        "myapp-0.1.tar.Z",
        "myapp-without-pkg-info.tar.Z",
    ],
)
def test_skip_check_on_tar_z(
    sdist_filename: str, data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sdist_path = data_dir / sdist_filename
    pip._check_metadata_in_sdist(sdist_path)
    assert f"Skip checking metadata from compressed sdist {sdist_path.name}" in caplog.text


@pytest.mark.parametrize(
    "sdist_filename,expected_error",
    [
        ["myapp-0.1.tar.fake.zip", "a Zip file. Error:"],
        ["myapp-0.1.zip.fake.tar", "a Tar file. Error:"],
        ["myapp-without-pkg-info.tar.gz", "not include metadata"],
    ],
)
def test_metadata_check_fails_from_sdist(
    sdist_filename: Path, expected_error: str, data_dir: Path
) -> None:
    sdist_path = data_dir / sdist_filename
    with pytest.raises(PackageRejected, match=expected_error):
        pip._check_metadata_in_sdist(sdist_path)


def test_metadata_check_invalid_argument() -> None:
    with pytest.raises(ValueError, match="Cannot check metadata"):
        pip._check_metadata_in_sdist(Path("myapp-0.2.tar.ZZZ"))


@pytest.mark.parametrize(
    "original_content, expect_replaced",
    [
        (
            dedent(
                """\
                foo==1.0.0
                bar==2.0.0
                """
            ),
            None,
        ),
        (
            dedent(
                f"""\
                foo==1.0.0
                bar @ git+https://github.com/org/bar@{GIT_REF}
                """
            ),
            dedent(
                f"""\
                foo==1.0.0
                bar @ file://${{output_dir}}/deps/pip/github.com/org/bar/bar-external-gitcommit-{GIT_REF}.tar.gz
                """
            ),
        ),
        (
            dedent(
                """\
                foo==1.0.0
                bar @ https://github.com/org/bar/archive/refs/tags/bar-2.0.0.zip#cachito_hash=sha256:fedcba
                """
            ),
            dedent(
                """\
                foo==1.0.0
                bar @ file://${output_dir}/deps/pip/external-bar/bar-external-sha256-fedcba.zip#cachito_hash=sha256:fedcba
                """
            ),
        ),
        (
            dedent(
                """\
                --require-hashes
                foo==1.0.0 --hash=sha256:abcdef
                bar @ https://github.com/org/bar/archive/refs/tags/bar-2.0.0.zip --hash=sha256:fedcba
                """
            ),
            dedent(
                """\
                --require-hashes
                foo==1.0.0 --hash=sha256:abcdef
                bar @ file://${output_dir}/deps/pip/external-bar/bar-external-sha256-fedcba.zip --hash=sha256:fedcba
                """
            ),
        ),
    ],
)
def test_replace_external_requirements(
    original_content: str, expect_replaced: Optional[str], rooted_tmp_path: RootedPath
) -> None:
    requirements_file = rooted_tmp_path.join_within_root("requirements.txt")
    requirements_file.path.write_text(original_content)

    replaced_file = pip._replace_external_requirements(requirements_file)
    if expect_replaced is None:
        assert replaced_file is None
    else:
        assert replaced_file is not None
        assert replaced_file.template == expect_replaced
        assert replaced_file.abspath == requirements_file.path


@pytest.mark.parametrize(
    "packages, n_pip_packages",
    [
        pytest.param(
            [{"type": "gomod"}],
            0,
            id="not_python_project",
        ),
        pytest.param(
            [{"type": "pip", "requirements_files": ["requirements.txt"]}],
            1,
            id="single_python_package",
        ),
        pytest.param(
            [
                {"type": "pip", "requirements_files": ["requirements.txt"]},
                {"type": "pip", "path": "foo", "requirements_build_files": []},
            ],
            2,
            id="multiple_python_packages",
        ),
    ],
)
@mock.patch("hermeto.core.scm.Repo")
@mock.patch("hermeto.core.package_managers.pip._replace_external_requirements")
@mock.patch("hermeto.core.package_managers.pip._resolve_pip")
@mock.patch("hermeto.core.package_managers.pip._filter_packages_with_rust_code")
def test_fetch_pip_source(
    mock_filter_cargo_packages: mock.Mock,
    mock_resolve_pip: mock.Mock,
    mock_replace_requirements: mock.Mock,
    mock_git_repo: mock.Mock,
    packages: list[PackageInput],
    n_pip_packages: int,
    rooted_tmp_path: RootedPath,
) -> None:
    source_dir = rooted_tmp_path.re_root("source")
    output_dir = rooted_tmp_path.re_root("output")
    source_dir.path.mkdir()
    source_dir.join_within_root("foo").path.mkdir()

    request = Request(source_dir=source_dir, output_dir=output_dir, packages=packages)

    mock_filter_cargo_packages.return_value = []
    resolved_a = {
        "package": {"name": "foo", "version": "1.0", "type": "pip"},
        "dependencies": [
            {
                "name": "bar",
                "version": "https://x.org/bar.zip#cachito_hash=sha256:aaaaaaaaaa",
                "type": "pip",
                "build_dependency": False,
                "kind": "url",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": False,
                "package_type": "",
            },
            {
                "name": "baz",
                "version": "0.0.5",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
                "type": "pip",
                "build_dependency": True,
                "kind": "pypi",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": False,
                "package_type": "wheel",
            },
        ],
        "packages_containing_rust_code": [],
        "requirements": ["/package_a/requirements.txt", "/package_a/requirements-build.txt"],
    }
    resolved_b = {
        "package": {"name": "spam", "version": "2.1", "type": "pip"},
        "dependencies": [
            {
                "name": "ham",
                "version": "3.2",
                "index_url": CUSTOM_PYPI_ENDPOINT,
                "type": "pip",
                "build_dependency": False,
                "kind": "pypi",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": True,
                "package_type": "sdist",
            },
            {
                "name": "eggs",
                "version": "https://x.org/eggs.zip#cachito_hash=sha256:aaaaaaaaaa",
                "type": "pip",
                "build_dependency": False,
                "kind": "url",
                "requirement_file": "requirements.txt",
                "missing_req_file_checksum": True,
                "package_type": "",
            },
        ],
        "packages_containing_rust_code": [],
        "requirements": ["/package_b/requirements.txt"],
    }

    replaced_file_a = ProjectFile(
        abspath=Path("/package_a/requirements.txt"),
        template="bar @ file://${output_dir}/deps/pip/...",
    )
    replaced_file_b = ProjectFile(
        abspath=Path("/package_b/requirements.txt"),
        template="eggs @ file://${output_dir}/deps/pip/...",
    )

    mock_resolve_pip.side_effect = [resolved_a, resolved_b]
    mock_replace_requirements.side_effect = [replaced_file_a, None, replaced_file_b]

    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = "https://github.com/my-org/my-repo"
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    output = pip.fetch_pip_source(request)

    expect_components_package_a = [
        Component(
            name="foo",
            version="1.0",
            purl=f"pkg:pypi/foo@1.0?vcs_url=git%2Bhttps://github.com/my-org/my-repo%40{'f' * 40}",
        ),
        Component(
            name="bar",
            purl="pkg:pypi/bar?checksum=sha256:aaaaaaaaaa&download_url=https://x.org/bar.zip",
        ),
        Component(
            name="baz",
            version="0.0.5",
            purl="pkg:pypi/baz@0.0.5",
            properties=[
                Property(name=f"{APP_NAME}:pip:package:binary", value="true"),
                Property(name=f"{APP_NAME}:pip:package:build-dependency", value="true"),
            ],
        ),
    ]

    expect_components_package_b = [
        Component(
            name="spam",
            version="2.1",
            purl=f"pkg:pypi/spam@2.1?vcs_url=git%2Bhttps://github.com/my-org/my-repo%40{'f' * 40}#foo",
        ),
        Component(
            name="ham",
            version="3.2",
            purl=f"pkg:pypi/ham@3.2?repository_url={CUSTOM_PYPI_ENDPOINT}",
            properties=[
                Property(name=f"{APP_NAME}:missing_hash:in_file", value="requirements.txt")
            ],
        ),
        Component(
            name="eggs",
            purl="pkg:pypi/eggs?checksum=sha256:aaaaaaaaaa&download_url=https://x.org/eggs.zip",
            properties=[
                Property(name=f"{APP_NAME}:missing_hash:in_file", value="requirements.txt")
            ],
        ),
    ]

    if n_pip_packages == 0:
        expect_packages = []
        expect_files = []
    elif n_pip_packages == 1:
        expect_packages = expect_components_package_a
        expect_files = [replaced_file_a]
    elif n_pip_packages == 2:
        expect_packages = expect_components_package_a + expect_components_package_b
        expect_files = [replaced_file_a, replaced_file_b]
    else:
        assert False

    assert output.components == expect_packages
    assert output.build_config.project_files == expect_files
    assert len(output.build_config.environment_variables) == (2 if n_pip_packages > 0 else 0)

    if n_pip_packages >= 1:
        mock_resolve_pip.assert_any_call(
            source_dir, output_dir, source_dir, [Path("requirements.txt")], None, False
        )
        mock_replace_requirements.assert_any_call("/package_a/requirements.txt")
        mock_replace_requirements.assert_any_call("/package_a/requirements-build.txt")
    if n_pip_packages >= 2:
        mock_resolve_pip.assert_any_call(
            source_dir.join_within_root("foo"), output_dir, source_dir, None, [], False
        )
        mock_replace_requirements.assert_any_call("/package_b/requirements.txt")


@pytest.mark.parametrize(
    "dependency, expected_purl",
    [
        (
            {
                "name": "pypi_package",
                "version": "1.0.0",
                "type": "pip",
                "dev": False,
                "kind": "pypi",
                "index_url": pypi_simple.PYPI_SIMPLE_ENDPOINT,
            },
            "pkg:pypi/pypi-package@1.0.0",
        ),
        (
            {
                "name": "mypypi_package",
                "version": "2.0.0",
                "type": "pip",
                "dev": False,
                "kind": "pypi",
                "index_url": CUSTOM_PYPI_ENDPOINT,
            },
            f"pkg:pypi/mypypi-package@2.0.0?repository_url={CUSTOM_PYPI_ENDPOINT}",
        ),
        (
            {
                "name": "git_dependency",
                "version": f"git+https://github.com/my-org/git_dependency@{'a' * 40}",
                "type": "pip",
                "dev": False,
                "kind": "vcs",
            },
            f"pkg:pypi/git-dependency?vcs_url=git%2Bhttps://github.com/my-org/git_dependency%40{'a' * 40}",
        ),
        (
            {
                "name": "Git_dependency",
                "version": f"git+file:///github.com/my-org/git_dependency@{'a' * 40}",
                "type": "pip",
                "dev": False,
                "kind": "vcs",
            },
            f"pkg:pypi/git-dependency?vcs_url=git%2Bfile:///github.com/my-org/git_dependency%40{'a' * 40}",
        ),
        (
            {
                "name": "git_dependency",
                "version": f"git+ssh://git@github.com/my-org/git_dependency@{'a' * 40}",
                "type": "pip",
                "dev": False,
                "kind": "vcs",
            },
            f"pkg:pypi/git-dependency?vcs_url=git%2Bssh://git%40github.com/my-org/git_dependency%40{'a' * 40}",
        ),
        (
            {
                "name": "git_dependency",
                "version": f"git+https://github.com/my-org/git_dependency@{'a' * 40}",
                "type": "pip",
                "dev": False,
                "kind": "vcs",
            },
            f"pkg:pypi/git-dependency?vcs_url=git%2Bhttps://github.com/my-org/git_dependency%40{'a' * 40}",
        ),
        (
            {
                "name": "https_dependency",
                "version": f"https://github.com/my-org/https_dependency/{'a' * 40}/file.tar.gz#egg=https_dependency&cachito_hash=sha256:de526c1",
                "type": "pip",
                "dev": False,
                "kind": "url",
            },
            f"pkg:pypi/https-dependency?checksum=sha256:de526c1&download_url=https://github.com/my-org/https_dependency/{'a' * 40}/file.tar.gz",
        ),
    ],
)
def test_generate_purl_dependencies(dependency: dict[str, Any], expected_purl: str) -> None:
    purl = pip._generate_purl_dependency(dependency)

    assert purl == expected_purl


@pytest.mark.parametrize(
    "subpath, expected_purl",
    [
        (
            ".",
            f"pkg:pypi/foo@1.0.0?vcs_url=git%2Bssh://git%40github.com/my-org/my-repo%40{'f' * 40}",
        ),
        (
            "path/to/package",
            f"pkg:pypi/foo@1.0.0?vcs_url=git%2Bssh://git%40github.com/my-org/my-repo%40{'f' * 40}#path/to/package",
        ),
    ],
)
@mock.patch("hermeto.core.scm.Repo")
def test_generate_purl_main_package(
    mock_git_repo: Any, subpath: Path, expected_purl: str, rooted_tmp_path: RootedPath
) -> None:
    package = {"name": "foo", "version": "1.0.0", "type": "pip"}

    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = "ssh://git@github.com/my-org/my-repo"
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    purl = pip._generate_purl_main_package(package, rooted_tmp_path.join_within_root(subpath))

    assert purl == expected_purl
