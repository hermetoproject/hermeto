# SPDX-License-Identifier: GPL-3.0-only
"""Utilities for testing registry proxy mode."""

import copy
import os
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from packageurl import PackageURL

from hermeto import APP_NAME
from hermeto.core.models.property_semantics import PropertyEnum
from hermeto.core.models.sbom import (
    BACKEND_ANNOTATION_PREFIX,
    PROXY_COMMENT,
    PROXY_REF_TYPE,
    Annotation,
    Component,
    ExternalReference,
)
from tests.nexusserver import DEFAULT_NEXUS_HOST, DEFAULT_NEXUS_TLS_PORT

_NEXUS_BASE_URL = f"https://{DEFAULT_NEXUS_HOST}:{DEFAULT_NEXUS_TLS_PORT}"

_PROXY_URL_ENV_PATTERN = re.compile(rf"^{APP_NAME}_([A-Z0-9_]+)__PROXY_URL$", re.IGNORECASE)
DEFAULT_LOCAL_NEXUS_PROXY_ENV: dict[str, str] = {
    f"{APP_NAME}_NPM__PROXY_URL": f"{_NEXUS_BASE_URL}/repository/npm-proxy/",
}

_DIRECT_SOURCE_QUALIFIERS = frozenset({"vcs_url", "download_url", "repository_url"})


def is_local_nexus_enabled() -> bool:
    """Return True when a local Nexus instance should be running."""
    return (
        os.getenv("HERMETO_TEST_LOCAL_NEXUS") == "1"
        or os.getenv("HERMETO_TEST_LOCAL_NEXUS_PROXY") == "1"
    )


def is_local_nexus_proxy_enabled() -> bool:
    """Return True when local Nexus proxy defaults should be enabled."""
    return os.getenv("HERMETO_TEST_LOCAL_NEXUS_PROXY") == "1"


def parse_proxy_env(env: Mapping[str, str]) -> dict[str, str]:
    """Extract a {backend: proxy_url} mapping from Hermeto env vars.

    Example: HERMETO_YARN_CLASSIC__PROXY_URL -> {"yarn-classic": <url>}
    """
    backend_proxy_urls = {}
    for env_var, proxy_url in env.items():
        if match := _PROXY_URL_ENV_PATTERN.match(env_var):
            backend = match.group(1).lower().replace("_", "-")
            backend_proxy_urls[backend] = proxy_url

    return backend_proxy_urls


def _get_bom_ref_backends(sbom: dict[str, Any]) -> dict[str, set[str]]:
    """Return a mapping of component bom-refs to backend names."""
    backends_by_bom_ref: dict[str, set[str]] = defaultdict(set)
    for raw_annotation in sbom.get("annotations", []):
        annotation = Annotation(**raw_annotation)
        if not annotation.text.startswith(BACKEND_ANNOTATION_PREFIX):
            continue

        # Can be backend:foo or backend:experimental:foo
        if not (backend_name := annotation.text.split(":")[-1]):
            continue

        for subject in annotation.subjects:
            backends_by_bom_ref[subject].add(backend_name)

    return dict(backends_by_bom_ref)


def _is_registry_component(component: Component) -> bool:
    """Return True if the component was fetched from a registry (not bundled or direct-source)."""
    is_bundled = any(
        p.name == PropertyEnum.PROP_CDX_NPM_PACKAGE_BUNDLED and p.value == "true"
        for p in component.properties
    )
    if is_bundled:
        return False

    purl = PackageURL.from_string(component.purl)
    return not (purl.qualifiers.keys() & _DIRECT_SOURCE_QUALIFIERS)


def _partition_proxy_external_refs(
    component: Component,
) -> tuple[list[ExternalReference], list[ExternalReference]]:
    """Partition external refs into (proxy_refs, non_proxy_refs)."""
    if component.external_references is None:
        return [], []

    is_proxy_ref = lambda ref: ref.type == PROXY_REF_TYPE and ref.comment == PROXY_COMMENT
    proxy_refs: list[ExternalReference] = []
    non_proxy_refs: list[ExternalReference] = []
    for ref in component.external_references:
        (proxy_refs if is_proxy_ref(ref) else non_proxy_refs).append(ref)

    return proxy_refs, non_proxy_refs


def _expected_proxy_urls_for_component(
    component: Component,
    bom_ref_backends: Mapping[str, set[str]],
    backend_proxy_urls: Mapping[str, str],
) -> set[str]:
    """Return the set of proxy URLs expected for a component, empty if none apply."""
    component_backends = bom_ref_backends.get(component.bom_ref, set())
    proxy_enabled_backends = component_backends & backend_proxy_urls.keys()

    if not proxy_enabled_backends or not _is_registry_component(component):
        return set()

    return set(backend_proxy_urls[backend] for backend in proxy_enabled_backends)


def _strip_proxy_refs(
    component_raw: dict[str, Any], non_proxy_refs: list[ExternalReference]
) -> None:
    """Remove proxy external references from a raw component dict in place."""
    if non_proxy_refs:
        component_raw["externalReferences"] = [
            ref.model_dump(exclude_none=True) for ref in non_proxy_refs
        ]
    elif "externalReferences" in component_raw:
        del component_raw["externalReferences"]


def validate_and_strip_proxy_refs(
    sbom: dict[str, Any], backend_proxy_urls: Mapping[str, str]
) -> dict[str, Any]:
    """Validate proxy refs exist and have correct URLs, return SBOM with them stripped."""
    sbom_copy = copy.deepcopy(sbom)
    bom_ref_backends = _get_bom_ref_backends(sbom_copy)
    mismatches: list[dict[str, Any]] = []

    for component_raw in sbom_copy.get("components", []):
        component = Component(**component_raw)

        proxy_refs, non_proxy_refs = _partition_proxy_external_refs(component)
        actual_proxy_urls = set(ref.url for ref in proxy_refs)
        expected_proxy_urls = _expected_proxy_urls_for_component(
            component, bom_ref_backends, backend_proxy_urls
        )

        if actual_proxy_urls != expected_proxy_urls:
            mismatches.append(
                {
                    "purl": component.purl,
                    "expected_proxy_urls": sorted(expected_proxy_urls),
                    "actual_proxy_urls": sorted(actual_proxy_urls),
                }
            )
            continue

        if expected_proxy_urls:
            _strip_proxy_refs(component_raw, non_proxy_refs)

    assert not mismatches, f"Proxy URL mismatches for {len(mismatches)} component(s): {mismatches}"

    return sbom_copy
