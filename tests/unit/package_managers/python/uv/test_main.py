# SPDX-License-Identifier: GPL-3.0-only
import textwrap

import pytest

from hermeto.core.errors import (
    PackageRejected,
)
from hermeto.core.package_managers.python.uv.main import (
    _get_pyproject_metadata,
)
from hermeto.core.rooted_path import RootedPath


def write_pyproject_toml(rooted_path: RootedPath, content: str) -> None:
    (rooted_path.path / "pyproject.toml").write_text(textwrap.dedent(content))


@pytest.mark.parametrize(
    "pyproject, expected_metadata, expected_log",
    [
        pytest.param(
            """
            [project]
            name = "example"
            version = "1.0.0"
            """,
            ("example", "1.0.0"),
            None,
            id="name_and_version_both_declared",
        ),
        pytest.param(
            """
            [project]
            name = "example"
            """,
            ("example", None),
            "Could not resolve version",
            id="undeclared_version_is_reported_as_none",
        ),
    ],
)
def test_get_pyproject_metadata(
    pyproject: str,
    expected_metadata: tuple[str, str | None],
    expected_log: str | None,
    rooted_tmp_path: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_pyproject_toml(rooted_tmp_path, pyproject)

    assert _get_pyproject_metadata(rooted_tmp_path) == expected_metadata
    if expected_log is None:
        assert "Could not resolve version" not in caplog.text
    else:
        assert expected_log in caplog.text


def test_get_pyproject_metadata_missing_name(rooted_tmp_path: RootedPath) -> None:
    write_pyproject_toml(rooted_tmp_path, "[project]\n")
    with pytest.raises(PackageRejected):
        _get_pyproject_metadata(rooted_tmp_path)
