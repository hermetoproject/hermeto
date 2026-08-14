# SPDX-License-Identifier: GPL-3.0-only
import pytest
from git.repo import Repo
from pydantic import HttpUrl

from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import PROXY_COMMENT
from hermeto.core.package_managers.bundler.gem_models import (
    GemDependency,
    GemPlatformSpecificDependency,
    GitDependency,
    PathDependency,
)
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF
from tests.unit.package_managers.bundler.test_main import FAKE_PROXY_URL


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


def test_to_component_gem_with_proxy_attaches_external_ref() -> None:
    """Gem deps fetched through a proxy record the proxy URL as an external reference."""
    dep = GemDependency(name="rails", version="7.0.0", source="https://rubygems.org/")

    component = dep.to_component(proxy_url=HttpUrl(FAKE_PROXY_URL))

    assert component.external_references is not None
    assert len(component.external_references) == 1
    ref = component.external_references[0]
    assert ref.url == FAKE_PROXY_URL
    assert ref.comment == PROXY_COMMENT


def test_to_component_non_registry_deps_ignore_proxy(rooted_tmp_path_repo: RootedPath) -> None:
    """Non-registry deps must not receive proxy external references."""
    Repo(rooted_tmp_path_repo.path).create_remote("origin", "git@github.com:user/repo.git")

    deps = [
        GitDependency(
            name="my_gem",
            version="0.1.0",
            url="https://github.com/user/repo.git",
            ref=GIT_REF,
        ),
        PathDependency(name="local_gem", version="1.0.0", root=rooted_tmp_path_repo, subpath="."),
    ]

    for dep in deps:
        component = dep.to_component(proxy_url=HttpUrl(FAKE_PROXY_URL))
        assert component.external_references is None


@pytest.mark.parametrize(
    ("dep", "expected_purl"),
    [
        pytest.param(
            GemDependency(name="rails", version="7.0.0", source="https://rubygems.org/"),
            "pkg:gem/rails@7.0.0",
            id="default-rubygems-source",
        ),
        pytest.param(
            GemDependency(name="rails", version="7.0.0", source="http://rubygems.org/"),
            "pkg:gem/rails@7.0.0",
            id="default-rubygems-source-http",
        ),
        pytest.param(
            GemDependency(name="custom_gem", version="1.0.0", source="https://gems.example.com"),
            "pkg:gem/custom_gem@1.0.0?repository_url=https://gems.example.com",
            id="custom-registry-source",
        ),
        pytest.param(
            GemDependency(
                name="trailing_slash_gem", version="1.0.0", source="https://gems.example.com/"
            ),
            "pkg:gem/trailing_slash_gem@1.0.0?repository_url=https://gems.example.com",
            id="custom-registry-source-trailing-slash",
        ),
    ],
)
def test_gem_dependency_purl(dep: GemDependency, expected_purl: str) -> None:
    """PURLs for non-default registries must include repository_url qualifier."""
    assert dep.purl == expected_purl


def test_purl_authenticated_default_registry() -> None:
    gem = GemDependency(
        name="test-gem", version="1.0.0", source="https://secretuser:secretpass@rubygems.org/"
    )

    assert gem.purl == "pkg:gem/test-gem@1.0.0"
