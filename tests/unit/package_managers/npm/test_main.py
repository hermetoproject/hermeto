# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from hermeto import APP_NAME
from hermeto.core.models.sbom import Component, Property
from hermeto.core.package_managers.npm.main import _generate_component_list
from hermeto.core.package_managers.npm.project import NpmComponentInfo


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
