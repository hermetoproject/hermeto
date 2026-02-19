# SPDX-License-Identifier: GPL-3.0-only
import pytest

from hermeto import APP_NAME
from hermeto.core.models.property_semantics import Property, PropertyEnum, PropertySet
from hermeto.core.models.sbom import Component, merge_component_properties


@pytest.mark.parametrize(
    "components, expect_merged",
    [
        pytest.param([], [], id="empty_components"),
        pytest.param(
            # don't merge different components, just sort them by purl and sort their properties
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_BUNDLED, value="true"),
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                    ],
                ),
                Component(
                    name="bar",
                    version="2.0.0",
                    purl="pkg:npm/bar@2.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT, value="true"),
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                    ],
                ),
            ],
            [
                Component(
                    name="bar",
                    version="2.0.0",
                    purl="pkg:npm/bar@2.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT, value="true"),
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                    ],
                ),
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_BUNDLED, value="true"),
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                    ],
                ),
            ],
            id="no_merging_just_sorted_components",
        ),
        pytest.param(
            # do merge identical components
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                        Property(
                            name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="package-lock.json"
                        ),
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_BUNDLED, value="true"),
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT, value="true"),
                    ],
                ),
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                        Property(name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="yarn.lock"),
                        # not bundled -> the merged result is not bundled
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT, value="true"),
                    ],
                ),
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                        Property(
                            name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="x/package-lock.json"
                        ),
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_BUNDLED, value="true"),
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT, value="true"),
                    ],
                ),
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT, value="true"),
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                        Property(
                            name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="package-lock.json"
                        ),
                        Property(
                            name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="x/package-lock.json"
                        ),
                        Property(name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="yarn.lock"),
                    ],
                ),
            ],
            id="merge_identical_components",
        ),
        pytest.param(
            # validate that "wheel" property is merged correctly
            [
                # sdist
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:pip/foo@1.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                    ],
                ),
                # wheel
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:pip/foo@1.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                        Property(name=PropertyEnum.PROP_PIP_PACKAGE_BINARY, value="true"),
                    ],
                ),
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:pip/foo@1.0.0",
                    properties=[
                        Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                        Property(name=PropertyEnum.PROP_PIP_PACKAGE_BINARY, value="true"),
                    ],
                )
            ],
            id="merge_pip_sdist_and_wheel",
        ),
    ],
)
def test_merge_component_properties(
    components: list[Component], expect_merged: list[Component]
) -> None:
    assert merge_component_properties(components) == expect_merged


class TestPropertySet:
    @pytest.mark.parametrize(
        "properties, property_set",
        [
            ([], PropertySet()),
            (
                [Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}")],
                PropertySet(found_by=f"{APP_NAME}"),
            ),
            (
                [
                    Property(name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="go.sum"),
                    Property(name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="foo/go.sum"),
                ],
                PropertySet(missing_hash_in_file=frozenset(["go.sum", "foo/go.sum"])),
            ),
            (
                [Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_BUNDLED, value="true")],
                PropertySet(npm_bundled=True),
            ),
            (
                [Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT, value="true")],
                PropertySet(npm_development=True),
            ),
            (
                [Property(name=PropertyEnum.PROP_PIP_PACKAGE_BINARY, value="true")],
                PropertySet(pip_package_binary=True),
            ),
            (
                [
                    Property(name=PropertyEnum.PROP_FOUND_BY, value=f"{APP_NAME}"),
                    Property(name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="go.sum"),
                    Property(name=PropertyEnum.PROP_MISSING_HASH_IN_FILE, value="foo/go.sum"),
                    Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_BUNDLED, value="true"),
                    Property(name=PropertyEnum.PROP_CDX_NPM_PACKAGE_DEVELOPMENT, value="true"),
                ],
                PropertySet(
                    found_by=f"{APP_NAME}",
                    missing_hash_in_file=frozenset(["go.sum", "foo/go.sum"]),
                    npm_bundled=True,
                    npm_development=True,
                ),
            ),
        ],
    )
    def test_conversion_from_and_to_properties(
        self, properties: list[Property], property_set: PropertySet
    ) -> None:
        assert PropertySet.from_properties(properties) == property_set
        assert property_set.to_properties() == sorted(properties, key=lambda p: (p.name, p.value))

    @pytest.mark.parametrize(
        "set_a, set_b, expect_merged",
        [
            (
                PropertySet(),
                PropertySet(),
                PropertySet(),
            ),
            (
                PropertySet(found_by=f"{APP_NAME}"),
                PropertySet(found_by="impostor"),
                PropertySet(found_by=f"{APP_NAME}"),
            ),
            (
                PropertySet(found_by=None),
                PropertySet(found_by=f"{APP_NAME}"),
                PropertySet(found_by=f"{APP_NAME}"),
            ),
            (
                PropertySet(missing_hash_in_file=frozenset(["go.sum"])),
                PropertySet(missing_hash_in_file=frozenset(["foo/go.sum"])),
                PropertySet(missing_hash_in_file=frozenset(["go.sum", "foo/go.sum"])),
            ),
            (
                PropertySet(npm_bundled=True),
                PropertySet(npm_bundled=False),
                PropertySet(npm_bundled=False),
            ),
            (
                PropertySet(npm_bundled=True),
                PropertySet(npm_bundled=True),
                PropertySet(npm_bundled=True),
            ),
            (
                PropertySet(npm_development=True),
                PropertySet(npm_development=False),
                PropertySet(npm_development=False),
            ),
            (
                PropertySet(npm_development=True),
                PropertySet(npm_development=True),
                PropertySet(npm_development=True),
            ),
            (
                PropertySet(),
                PropertySet(pip_package_binary=True),
                PropertySet(pip_package_binary=True),
            ),
        ],
    )
    def test_merge(
        self, set_a: PropertySet, set_b: PropertySet, expect_merged: PropertySet
    ) -> None:
        assert set_a.merge(set_b) == expect_merged
