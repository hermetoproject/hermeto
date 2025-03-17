import pytest

from cachi2.core.models.property_semantics import PropertySet
from cachi2.core.models.sbom import Component, Property, merge_component_properties


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
                        Property(name="cdx:npm:package:bundled", value="true"),
                        Property(name="cachi2:found_by", value="cachi2"),
                    ],
                ),
                Component(
                    name="bar",
                    version="2.0.0",
                    purl="pkg:npm/bar@2.0.0",
                    properties=[
                        Property(name="cdx:npm:package:development", value="true"),
                        Property(name="cachi2:found_by", value="cachi2"),
                    ],
                ),
            ],
            [
                Component(
                    name="bar",
                    version="2.0.0",
                    purl="pkg:npm/bar@2.0.0",
                    properties=[
                        Property(name="cachi2:found_by", value="cachi2"),
                        Property(name="cdx:npm:package:development", value="true"),
                    ],
                ),
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name="cachi2:found_by", value="cachi2"),
                        Property(name="cdx:npm:package:bundled", value="true"),
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
                        Property(name="cachi2:found_by", value="cachi2"),
                        Property(name="cachi2:missing_hash:in_file", value="package-lock.json"),
                        Property(name="cdx:npm:package:bundled", value="true"),
                        Property(name="cdx:npm:package:development", value="true"),
                    ],
                ),
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name="cachi2:found_by", value="cachi2"),
                        Property(name="cachi2:missing_hash:in_file", value="yarn.lock"),
                        # not bundled -> the merged result is not bundled
                        Property(name="cdx:npm:package:development", value="true"),
                    ],
                ),
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name="cachi2:found_by", value="cachi2"),
                        Property(name="cachi2:missing_hash:in_file", value="x/package-lock.json"),
                        Property(name="cdx:npm:package:bundled", value="true"),
                        Property(name="cdx:npm:package:development", value="true"),
                    ],
                ),
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:npm/foo@1.0.0",
                    properties=[
                        Property(name="cachi2:found_by", value="cachi2"),
                        Property(name="cachi2:missing_hash:in_file", value="package-lock.json"),
                        Property(name="cachi2:missing_hash:in_file", value="x/package-lock.json"),
                        Property(name="cachi2:missing_hash:in_file", value="yarn.lock"),
                        Property(name="cdx:npm:package:development", value="true"),
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
                        Property(name="cachi2:found_by", value="cachi2"),
                    ],
                ),
                # wheel
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:pip/foo@1.0.0",
                    properties=[
                        Property(name="cachi2:found_by", value="cachi2"),
                        Property(name="cachi2:pip:package:binary", value="true"),
                    ],
                ),
            ],
            [
                Component(
                    name="foo",
                    version="1.0.0",
                    purl="pkg:pip/foo@1.0.0",
                    properties=[
                        Property(name="cachi2:found_by", value="cachi2"),
                        Property(name="cachi2:pip:package:binary", value="true"),
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
                [Property(name="cachi2:found_by", value="cachi2")],
                PropertySet(found_by="cachi2"),
            ),
            (
                [
                    Property(name="cachi2:missing_hash:in_file", value="go.sum"),
                    Property(name="cachi2:missing_hash:in_file", value="foo/go.sum"),
                ],
                PropertySet(missing_hash_in_file=frozenset(["go.sum", "foo/go.sum"])),
            ),
            (
                [Property(name="cdx:npm:package:bundled", value="true")],
                PropertySet(npm_bundled=True),
            ),
            (
                [Property(name="cdx:npm:package:development", value="true")],
                PropertySet(npm_development=True),
            ),
            (
                [Property(name="cachi2:pip:package:binary", value="true")],
                PropertySet(pip_package_binary=True),
            ),
            (
                [
                    Property(name="cachi2:found_by", value="cachi2"),
                    Property(name="cachi2:missing_hash:in_file", value="go.sum"),
                    Property(name="cachi2:missing_hash:in_file", value="foo/go.sum"),
                    Property(name="cdx:npm:package:bundled", value="true"),
                    Property(name="cdx:npm:package:development", value="true"),
                ],
                PropertySet(
                    found_by="cachi2",
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
                PropertySet(found_by="cachi2"),
                PropertySet(found_by="impostor"),
                PropertySet(found_by="cachi2"),
            ),
            (
                PropertySet(found_by=None),
                PropertySet(found_by="cachi2"),
                PropertySet(found_by="cachi2"),
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
