# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from unittest import mock

import pytest

from hermeto import APP_NAME
from hermeto.core.models.output import EnvironmentVariable, RequestOutput
from hermeto.core.models.sbom import Component, Property
from hermeto.core.package_managers.javascript.npm.main import (
    _generate_component_list,
    fetch_npm_source,
)
from hermeto.core.package_managers.javascript.npm.project import NpmComponentInfo


@pytest.mark.parametrize(
    "components, expected_components",
    [
        (
            [
                {
                    "name": "foo",
                    "purl": "pkg:npm/foo@1.0.0",
                    "version": "1.0.0",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
                {
                    "name": "bar",
                    "purl": "pkg:npm/bar@1.0.0",
                    "version": "1.0.0",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
            ],
            [
                Component(name="foo", version="1.0.0", purl="pkg:npm/foo@1.0.0"),
                Component(name="bar", version="1.0.0", purl="pkg:npm/bar@1.0.0"),
            ],
        ),
        (
            [
                {
                    "name": "foo",
                    "purl": "pkg:npm/foo@1.0.0",
                    "version": "1.0.0",
                    "bundled": False,
                    "dev": True,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name="cdx:npm:package:development", value="true"),
                        Property(name=f"{APP_NAME}:found_by", value=f"{APP_NAME}"),
                    ],
                ),
            ],
        ),
        (
            [
                {
                    "name": "foo",
                    "purl": "pkg:npm/foo@1.0.0",
                    "version": "1.0.0",
                    "bundled": True,
                    "dev": False,
                    "missing_hash_in_file": None,
                    "external_refs": None,
                },
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name="cdx:npm:package:bundled", value="true"),
                        Property(name=f"{APP_NAME}:found_by", value=f"{APP_NAME}"),
                    ],
                ),
            ],
        ),
        (
            [
                {
                    "name": "foo",
                    "purl": "pkg:npm/foo@1.0.0",
                    "version": "1.0.0",
                    "bundled": False,
                    "dev": False,
                    "missing_hash_in_file": Path("path/to/foo/package-lock.json"),
                    "external_refs": None,
                },
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(
                            name=f"{APP_NAME}:missing_hash:in_file",
                            value="path/to/foo/package-lock.json",
                        ),
                    ],
                ),
            ],
        ),
    ],
)
def test_generate_component_list(
    components: list[NpmComponentInfo], expected_components: list[Component]
) -> None:
    """Test _generate_component_list with different NpmComponentInfo inputs."""
    merged_components = _generate_component_list(components)
    assert merged_components == expected_components


@mock.patch(
    "hermeto.core.package_managers.javascript.npm.main.create_backend_annotation",
    return_value=None,
)
@mock.patch("hermeto.core.package_managers.javascript.npm.main._resolve_npm")
def test_fetch_npm_source_sets_build_from_source_env_var(
    mock_resolve_npm: mock.Mock,
    mock_create_annotation: mock.Mock,
    rooted_tmp_path: "RootedPath",
) -> None:
    """fetch_npm_source must set npm_config_build_from_source=true so that
    prebuildify packages compile from source instead of using prebuilt binaries.
    See https://github.com/hermetoproject/hermeto/issues/1015."""
    from hermeto.core.models.input import Request
    from hermeto.core.rooted_path import RootedPath

    (rooted_tmp_path.path / ".").mkdir(exist_ok=True)
    request = Request(
        source_dir=rooted_tmp_path,
        output_dir=rooted_tmp_path.join_within_root("output"),
        packages=[{"type": "npm", "path": "."}],
    )
    mock_resolve_npm.return_value = {
        "package": {
            "name": "foo",
            "version": "1.0.0",
            "purl": "pkg:npm/foo@1.0.0",
            "bundled": False,
            "dev": False,
            "missing_hash_in_file": None,
            "external_refs": None,
        },
        "dependencies": [],
        "projectfiles": [],
    }

    output = fetch_npm_source(request)

    assert EnvironmentVariable(name="npm_config_build_from_source", value="true") in (
        output.build_config.environment_variables
    )
