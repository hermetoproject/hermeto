# SPDX-License-Identifier: GPL-3.0-only
import pytest

from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.package_managers.bundler.gem_models import (
    GemDependency,
    GemPlatformSpecificDependency,
)


@pytest.mark.parametrize(
    "dep, expected_binary",
    [
        pytest.param(
            GemDependency(name="rails", version="7.0.0", source="https://rubygems.org/"),
            False,
            id="gem",
        ),
        pytest.param(
            GemPlatformSpecificDependency(
                name="nokogiri",
                version="1.15.4",
                source="https://rubygems.org/",
                platform="x86_64-linux",
            ),
            True,
            id="platform-specific-gem",
        ),
    ],
)
def test_to_component_binary_property(dep: GemDependency, expected_binary: bool) -> None:
    """Only platform-specific gems are marked as binary in the SBOM."""
    component = dep.to_component()
    props = PropertySet.from_properties(component.properties)

    assert props.bundler_package_binary is expected_binary
