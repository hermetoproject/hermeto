"""Main logic for fetching Hugging Face models and datasets."""

import logging
import tempfile
from pathlib import Path
from typing import Optional, Union

import yaml
from huggingface_hub import dataset_info, hf_hub_download, model_info
from huggingface_hub.hf_api import DatasetInfo, ModelInfo
from huggingface_hub.utils import HfHubHTTPError
from pydantic import ValidationError

from hermeto import APP_NAME
from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, RequestOutput
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.huggingface.cache import HFCacheManager
from hermeto.core.package_managers.huggingface.models import HuggingFaceLockfile, HuggingFaceModel
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE_NAME = "huggingface.lock.yaml"
DEFAULT_DEPS_DIR = "deps/huggingface/hub"
DEFAULT_HF_ENDPOINT = "https://huggingface.co"


def fetch_huggingface_source(request: Request) -> RequestOutput:
    """
    Resolve and fetch Hugging Face dependencies for a given request.

    :param request: the request to process
    :return: Request output with fetched components
    """
    components = []
    for package in request.huggingface_packages:
        path = request.source_dir.join_within_root(package.path)
        lockfile = package.lockfile or path.join_within_root(DEFAULT_LOCKFILE_NAME).path

        components.extend(_resolve_huggingface_lockfile(lockfile, request.output_dir))

    return RequestOutput.from_obj_list(
        components=components, environment_variables=_generate_environment_variables()
    )


def _resolve_huggingface_lockfile(lockfile_path: Path, output_dir: RootedPath) -> list[Component]:
    """
    Resolve the Hugging Face lockfile and fetch the models/datasets.

    :param lockfile_path: Absolute path to the lockfile
    :param output_dir: Output directory to store dependencies
    :return: List of SBOM components
    """
    if not lockfile_path.exists():
        raise PackageRejected(
            f"{APP_NAME} Hugging Face lockfile '{lockfile_path}' does not exist",
            solution=(
                f"Make sure your repository has {APP_NAME} Hugging Face lockfile "
                f"'{DEFAULT_LOCKFILE_NAME}' checked in, or the supplied lockfile path is correct."
            ),
        )

    # Re-root output directory
    cache_root = output_dir.join_within_root(DEFAULT_DEPS_DIR)
    cache_root.path.mkdir(parents=True, exist_ok=True)

    log.info(f"Reading Hugging Face lockfile: {lockfile_path}")
    lockfile = _load_lockfile(lockfile_path)

    cache_manager = HFCacheManager(cache_root.path)
    components = []

    for model_entry in lockfile.models:
        log.info(
            f"Fetching {model_entry.type} '{model_entry.repository}' at revision {model_entry.revision}"
        )
        component = _fetch_model(model_entry, cache_manager)
        components.append(component)

    return components


def _load_lockfile(lockfile_path: Path) -> HuggingFaceLockfile:
    """
    Load and validate the Hugging Face lockfile.

    :param lockfile_path: Path to the lockfile
    :return: Validated lockfile object
    """
    try:
        with open(lockfile_path) as f:
            lockfile_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise PackageRejected(
            f"{APP_NAME} Hugging Face lockfile '{lockfile_path}' has invalid YAML format: {e}",
            solution="Check correct YAML syntax in the lockfile.",
        ) from e

    try:
        return HuggingFaceLockfile.model_validate(lockfile_data)
    except ValidationError as e:
        loc = e.errors()[0]["loc"]
        msg = e.errors()[0]["msg"]
        raise PackageRejected(
            f"{APP_NAME} Hugging Face lockfile '{lockfile_path}' format is not valid: '{loc}: {msg}'",
            solution="Check the correct format and whether any keys are missing in the lockfile.",
        ) from e


def _fetch_model(model_entry: HuggingFaceModel, cache_manager: HFCacheManager) -> Component:
    """
    Fetch a single model or dataset from Hugging Face Hub.

    :param model_entry: Model entry from lockfile
    :param cache_manager: Cache manager for organizing files
    :return: SBOM component for the model
    """
    repo_type = model_entry.type
    repo_id = model_entry.repository
    revision = model_entry.revision

    # Get repository info from HF Hub API
    try:
        info: Union[ModelInfo, DatasetInfo]
        if repo_type == "model":
            info = model_info(
                repo_id=repo_id,
                revision=revision,
                files_metadata=True,
            )
        else:  # dataset
            info = dataset_info(
                repo_id=repo_id,
                revision=revision,
                files_metadata=True,
            )
    except HfHubHTTPError as e:
        if e.response.status_code == 404:
            raise PackageRejected(
                f"Repository '{repo_id}' not found on Hugging Face Hub at revision {revision}",
                solution=(
                    "Check that the repository name is correct and the revision exists. "
                    "You can verify on https://huggingface.co/"
                ),
            ) from e
        raise PackageRejected(
            f"Failed to fetch repository info for '{repo_id}': {e}",
            solution="Check your internet connection and that the repository is accessible.",
        ) from e

    # Get cache directory for this repository
    repo_cache_dir = cache_manager.get_repo_cache_dir(
        model_entry.namespace, model_entry.name, repo_type
    )
    repo_cache_dir.mkdir(parents=True, exist_ok=True)

    # Filter files based on include_patterns
    files_to_download = []
    if info.siblings:
        for sibling in info.siblings:
            if _should_include_file(sibling.rfilename, model_entry.include_patterns):
                files_to_download.append(sibling)

    if not files_to_download:
        log.warning(
            f"No files matched the include patterns for '{repo_id}'. "
            f"Patterns: {model_entry.include_patterns}"
        )

    log.info(f"Downloading {len(files_to_download)} files for '{repo_id}'")

    # Download each file
    with tempfile.TemporaryDirectory(prefix="hermeto-hf-") as temp_dir:
        temp_path = Path(temp_dir)

        for file_info in files_to_download:
            filename = file_info.rfilename
            log.debug(f"Downloading {filename}")

            # Download to temporary location
            downloaded_file = hf_hub_download(  # nosec B615
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                repo_type=repo_type,
                cache_dir=temp_path,
                local_dir=temp_path / "download",
                local_dir_use_symlinks=False,
            )

            # Add to our cache structure
            # HF Hub returns blob hash in lfs metadata, but we'll compute our own
            checksum_info = ChecksumInfo("sha256", file_info.lfs.sha256 if file_info.lfs else "")

            cache_manager.add_file_to_cache(
                repo_cache_dir=repo_cache_dir,
                revision=revision,
                file_path=filename,
                local_file=Path(downloaded_file),
                checksum_info=checksum_info,
            )

    # Create a ref for the revision
    cache_manager.create_ref(repo_cache_dir, "main", revision)

    # Generate SBOM component
    download_url = f"{DEFAULT_HF_ENDPOINT}/{repo_id}"
    return model_entry.get_sbom_component(download_url)


def _should_include_file(filename: str, include_patterns: Optional[list[str]]) -> bool:
    """
    Check if a file should be included based on patterns.

    :param filename: File path to check
    :param include_patterns: List of glob patterns, or None to include all
    :return: True if file should be included
    """
    if include_patterns is None:
        return True

    from pathlib import PurePath

    file_path = PurePath(filename)
    for pattern in include_patterns:
        # Use pathlib's match which supports ** globstar patterns
        if file_path.match(pattern):
            return True
        # Also try matching with pattern variations for ** edge cases
        # e.g., "**/*.json" should also match "config.json" at root
        if pattern.startswith("**/"):
            simple_pattern = pattern[3:]  # Remove "**/" prefix
            if file_path.match(simple_pattern):
                return True

    return False


def _generate_environment_variables() -> list[EnvironmentVariable]:
    """Generate environment variables for building with Hugging Face dependencies."""
    env_vars = {
        "HF_HOME": "${output_dir}/deps/huggingface",
        "HF_HUB_CACHE": "${output_dir}/deps/huggingface/hub",
        "HF_HUB_OFFLINE": "1",
        "HUGGINGFACE_HUB_CACHE": "${output_dir}/deps/huggingface/hub",
    }
    return [EnvironmentVariable(name=key, value=value) for key, value in env_vars.items()]
