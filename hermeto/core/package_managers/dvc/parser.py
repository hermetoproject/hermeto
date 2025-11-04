"""Parser for DVC lockfiles and SBOM generation."""

import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml
from packageurl import PackageURL
from pydantic import ValidationError

from hermeto import APP_NAME
from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import PackageRejected
from hermeto.core.models.sbom import Component, ExternalReference
from hermeto.core.package_managers.dvc.models import DVCDep, DVCLockfile

log = logging.getLogger(__name__)

# HuggingFace URL pattern: https://huggingface.co/{repo}/resolve/{revision}/{file_path}
HF_URL_PATTERN = re.compile(
    r"https://huggingface\.co/([^/]+/[^/]+|[^/]+)/resolve/([a-f0-9]{40})/(.+)"
)


def load_dvc_lockfile(lockfile_path: Path) -> DVCLockfile:
    """
    Load and validate a DVC lockfile.

    :param lockfile_path: Path to dvc.lock file
    :return: Validated DVCLockfile object
    :raises PackageRejected: If lockfile is invalid or cannot be read
    """
    if not lockfile_path.exists():
        raise PackageRejected(
            f"DVC lockfile '{lockfile_path}' does not exist",
            solution=(
                "Make sure your repository has a dvc.lock file checked in. "
                "You can generate it by running DVC commands like 'dvc repro' or 'dvc commit'."
            ),
        )

    try:
        with open(lockfile_path) as f:
            lockfile_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise PackageRejected(
            f"DVC lockfile '{lockfile_path}' has invalid YAML format: {e}",
            solution="Check correct YAML syntax in the lockfile.",
        ) from e

    if not lockfile_data:
        raise PackageRejected(
            f"DVC lockfile '{lockfile_path}' is empty",
            solution="Make sure the lockfile contains valid DVC stage definitions.",
        )

    try:
        return DVCLockfile.model_validate(lockfile_data)
    except ValidationError as e:
        loc = e.errors()[0]["loc"]
        msg = e.errors()[0]["msg"]
        raise PackageRejected(
            f"DVC lockfile '{lockfile_path}' format is not valid: '{loc}: {msg}'",
            solution=(
                "Check the lockfile format is correct. "
                "Only DVC schema version 2.0+ is supported."
            ),
        ) from e


def validate_checksums_present(
    lockfile: DVCLockfile, strict: bool = True
) -> None:
    """
    Validate that all external dependencies have checksums.

    :param lockfile: Parsed DVC lockfile
    :param strict: If True, raise error on missing checksums; if False, only warn
    :raises PackageRejected: If strict mode and checksums are missing
    """
    external_deps = lockfile.get_all_external_deps()

    if not external_deps:
        log.info("No external dependencies found in dvc.lock")
        return

    missing_checksums = []
    for stage_name, dep in external_deps:
        if not dep.checksum_value:
            missing_checksums.append((stage_name, dep.path))

    if missing_checksums:
        msg = "The following external dependencies are missing checksums:\n"
        for stage_name, path in missing_checksums:
            msg += f"  - Stage '{stage_name}': {path}\n"

        if strict:
            raise PackageRejected(
                f"External dependencies missing checksums in dvc.lock\n{msg}",
                solution=(
                    "Run DVC commands to populate checksums, or use --mode=permissive "
                    "to allow missing checksums."
                ),
            )
        else:
            log.warning(f"Missing checksums (permissive mode):\n{msg}")


def generate_sbom_components(lockfile: DVCLockfile) -> list[Component]:
    """
    Generate SBOM components from DVC lockfile dependencies.

    Groups HuggingFace models by repository and creates generic components
    for other URLs.

    :param lockfile: Parsed DVC lockfile
    :return: List of SBOM components
    """
    external_deps = lockfile.get_all_external_deps()

    if not external_deps:
        log.info("No external dependencies to include in SBOM")
        return []

    # Group HuggingFace dependencies by repository
    hf_deps_by_repo: dict[str, list[tuple[str, DVCDep]]] = {}
    generic_deps: list[tuple[str, DVCDep]] = []

    for stage_name, dep in external_deps:
        if _is_huggingface_url(dep.path):
            repo_id, _, _ = _parse_huggingface_url(dep.path)
            if repo_id:
                if repo_id not in hf_deps_by_repo:
                    hf_deps_by_repo[repo_id] = []
                hf_deps_by_repo[repo_id].append((stage_name, dep))
            else:
                # Couldn't parse as HF URL, treat as generic
                generic_deps.append((stage_name, dep))
        else:
            generic_deps.append((stage_name, dep))

    components = []

    # Create HuggingFace components
    for repo_id, deps in hf_deps_by_repo.items():
        component = _create_huggingface_component(repo_id, deps)
        components.append(component)

    # Create generic components
    for stage_name, dep in generic_deps:
        component = _create_generic_component(stage_name, dep)
        components.append(component)

    return components


def _is_huggingface_url(url: str) -> bool:
    """Check if URL is from HuggingFace."""
    return "huggingface.co" in url


def _parse_huggingface_url(url: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parse HuggingFace URL to extract repo, revision, and file path.

    :param url: HuggingFace URL
    :return: Tuple of (repo_id, revision, file_path) or (None, None, None)
    """
    match = HF_URL_PATTERN.match(url)
    if not match:
        return None, None, None

    repo_id = match.group(1)
    revision = match.group(2)
    file_path = match.group(3)

    return repo_id, revision, file_path


def _create_huggingface_component(
    repo_id: str, deps: list[tuple[str, DVCDep]]
) -> Component:
    """
    Create SBOM component for HuggingFace repository.

    :param repo_id: HuggingFace repository ID (e.g., "microsoft/deberta-v3-base")
    :param deps: List of (stage_name, dep) tuples for this repository
    :return: SBOM Component
    """
    # Extract revision from first dep (should be same for all files in repo)
    _, revision, _ = _parse_huggingface_url(deps[0][1].path)

    # Determine namespace and name
    if "/" in repo_id:
        namespace, name = repo_id.split("/", 1)
    else:
        namespace = None
        name = repo_id

    # Create PURL
    purl_kwargs = {
        "type": "huggingface",
        "name": name,
        "version": revision,
    }
    if namespace:
        purl_kwargs["namespace"] = namespace

    purl = str(PackageURL(**purl_kwargs))

    # Create component
    component = Component(
        type="library",
        name=repo_id,
        version=revision or "unknown",
        purl=purl,
    )

    # Add download URL as external reference
    download_url = f"https://huggingface.co/{repo_id}"
    component.external_references = [
        ExternalReference(
            url=download_url,
            type="distribution",
        )
    ]

    return component


def _create_generic_component(stage_name: str, dep: DVCDep) -> Component:
    """
    Create SBOM component for generic URL dependency.

    :param stage_name: DVC stage name
    :param dep: Dependency object
    :return: SBOM Component
    """
    # Extract filename from URL
    parsed_url = urlparse(dep.path)
    filename = Path(parsed_url.path).name or "unknown"

    # Create PURL with checksum in qualifiers
    purl_kwargs = {
        "type": "generic",
        "name": filename,
    }

    qualifiers = {}
    if dep.checksum_value and dep.checksum_algorithm:
        qualifiers["checksum"] = f"{dep.checksum_algorithm}:{dep.checksum_value}"
    qualifiers["download_url"] = dep.path

    if qualifiers:
        purl_kwargs["qualifiers"] = qualifiers

    purl = str(PackageURL(**purl_kwargs))

    # Use checksum as version if available, otherwise "unknown"
    version = dep.checksum_value[:8] if dep.checksum_value else "unknown"

    component = Component(
        type="library",
        name=filename,
        version=version,
        purl=purl,
    )

    # Add download URL as external reference
    component.external_references = [
        ExternalReference(
            url=dep.path,
            type="distribution",
        )
    ]

    return component
