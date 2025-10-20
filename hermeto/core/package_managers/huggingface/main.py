"""Main logic for fetching Hugging Face models and datasets."""

import logging
from pathlib import Path

import yaml
from huggingface_hub import snapshot_download
from huggingface_hub.utils import HfHubHTTPError
from pydantic import ValidationError

from hermeto import APP_NAME
from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import EnvironmentVariable, RequestOutput
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.huggingface.models import HuggingFaceLockfile, HuggingFaceModel
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)

DEFAULT_LOCKFILE_NAME = "huggingface.lock.yaml"
DEFAULT_DEPS_DIR = "deps/huggingface/hub"
DEFAULT_HF_ENDPOINT = "https://huggingface.co"

# File patterns that execute arbitrary code when LOADED by user's application
# (not during Hermeto's fetch - Hermeto only downloads files without deserialization)
UNSAFE_FILE_PATTERNS = [
    "*.bin",  # PyTorch pickle format - executes code during model loading
    "*.pt",  # PyTorch pickle format - executes code during model loading
    "*.pkl",  # Python pickle format - executes code during deserialization
    "*.pickle",  # Python pickle format - executes code during deserialization
    "modeling_*.py",  # Custom model code - imported by transformers library
    "*.pth",  # PyTorch checkpoint format - executes code during loading
]


def fetch_huggingface_source(request: Request) -> RequestOutput:
    """
    Resolve and fetch Hugging Face dependencies for a given request.

    :param request: the request to process
    :return: Request output with fetched components
    """
    components = []
    for package in request.huggingface_packages:
        path = request.source_dir.join_within_root(package.path)
        if package.lockfile:
            if not package.lockfile.is_absolute():
                raise PackageRejected(
                    f"Hugging Face lockfile path '{package.lockfile}' is not absolute",
                    solution="Provide an absolute path to the lockfile",
                )
            lockfile = package.lockfile
        else:
            lockfile = path.join_within_root(DEFAULT_LOCKFILE_NAME).path

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

    components = []

    for model_entry in lockfile.models:
        log.info(
            f"Fetching {model_entry.type} '{model_entry.repository}' at revision {model_entry.revision}"
        )
        # Check for unsafe file patterns and warn user
        _check_unsafe_patterns(model_entry)
        component = _fetch_model(model_entry, cache_root.path)
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


def _fetch_model(model_entry: HuggingFaceModel, cache_root: Path) -> Component:
    """
    Fetch a single model or dataset from Hugging Face Hub.

    :param model_entry: Model entry from lockfile
    :param cache_root: Root directory for HuggingFace cache
    :return: SBOM component for the model
    """
    repo_type = model_entry.type
    repo_id = model_entry.repository
    revision = model_entry.revision

    # Download entire snapshot using HuggingFace's native cache management
    try:
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            repo_type=repo_type,
            cache_dir=cache_root,
            allow_patterns=model_entry.include_patterns,
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
            f"Failed to fetch repository '{repo_id}': {e}",
            solution="Check your internet connection and that the repository is accessible.",
        ) from e

    # Generate SBOM component
    download_url = f"{DEFAULT_HF_ENDPOINT}/{repo_id}"
    return model_entry.get_sbom_component(download_url)


def _check_unsafe_patterns(model_entry: HuggingFaceModel) -> None:
    """
    Check if model entry allows downloading unsafe file formats and log warnings.

    Note: The risk is NOT during Hermeto's fetch (which only downloads files via HTTP),
    but during model loading by the user's application (pickle deserialization).

    :param model_entry: Model entry from lockfile
    """
    if model_entry.include_patterns is None:
        # No patterns means everything is downloaded, including unsafe files
        log.warning(
            f"Security warning: Model '{model_entry.repository}' has no include_patterns specified. "
            f"This will download ALL files including potentially unsafe formats (*.bin, *.pt, *.pkl) "
            f"that execute arbitrary code when YOUR application loads them (not during Hermeto's fetch). "
            f"Consider restricting to safe formats like *.safetensors"
        )
        return

    # Check if any unsafe patterns are explicitly included
    unsafe_patterns_found = []
    for pattern in model_entry.include_patterns:
        for unsafe_pattern in UNSAFE_FILE_PATTERNS:
            # Simple pattern matching - check if they're the same or if the pattern could match
            if pattern == unsafe_pattern or unsafe_pattern in pattern:
                unsafe_patterns_found.append(pattern)
                break

    if unsafe_patterns_found:
        log.warning(
            f"Security warning: Model '{model_entry.repository}' includes potentially unsafe patterns: "
            f"{unsafe_patterns_found}. These file formats use pickle serialization which executes "
            f"arbitrary code when YOUR application loads the model (not during Hermeto's fetch). "
            f"Consider using SafeTensors format (*.safetensors) instead."
        )


def _generate_environment_variables() -> list[EnvironmentVariable]:
    """Generate environment variables for building with Hugging Face dependencies."""
    env_vars = {
        "HF_HOME": "${output_dir}/deps/huggingface",
        "HF_HUB_CACHE": "${output_dir}/deps/huggingface/hub",
        "HF_DATASETS_CACHE": "${output_dir}/deps/huggingface/datasets",
        "HF_HUB_OFFLINE": "1",
        "HUGGINGFACE_HUB_CACHE": "${output_dir}/deps/huggingface/hub",
    }
    return [EnvironmentVariable(name=key, value=value) for key, value in env_vars.items()]
