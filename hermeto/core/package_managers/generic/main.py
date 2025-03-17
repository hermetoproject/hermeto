import asyncio
import logging
import os
from pathlib import Path
from typing import Union

import yaml
from pydantic import ValidationError

from hermeto import APP_NAME
from hermeto.core.checksum import must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import RequestOutput
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.package_managers.generic.models import GenericLockfileV1
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)
DEFAULT_LOCKFILE_NAME = "artifacts.lock.yaml"
DEFAULT_DEPS_DIR = "deps/generic"


def fetch_generic_source(request: Request) -> RequestOutput:
    """
    Resolve and fetch generic dependencies for a given request.

    :param request: the request to process
    """
    components = []
    for package in request.generic_packages:
        path = request.source_dir.join_within_root(package.path)
        lockfile = package.lockfile or path.join_within_root(DEFAULT_LOCKFILE_NAME).path
        if not lockfile.is_absolute():
            raise PackageRejected(
                f"Supplied generic lockfile path '{lockfile}' is not absolute, refusing to continue.",
                solution="Make sure the supplied path to the generic lockfile is absolute.",
            )
        components.extend(_resolve_generic_lockfile(lockfile, request.output_dir))
    return RequestOutput.from_obj_list(components=components)


def _resolve_generic_lockfile(lockfile_path: Path, output_dir: RootedPath) -> list[Component]:
    """
    Resolve the generic lockfile and pre-fetch the dependencies.

    :param lockfile_path: absolute path to the lockfile
    :param output_dir: the output directory to store the dependencies
    """
    if not lockfile_path.exists():
        raise PackageRejected(
            f"{APP_NAME} generic lockfile '{lockfile_path}' does not exist, refusing to continue.",
            solution=(
                f"Make sure your repository has {APP_NAME} generic lockfile '{DEFAULT_LOCKFILE_NAME}' "
                f"checked in to the repository, or the supplied lockfile path is correct."
            ),
        )

    # output_dir is now the root and cannot be escaped
    output_dir = output_dir.re_root(DEFAULT_DEPS_DIR)

    log.info(f"Reading generic lockfile: {lockfile_path}")
    lockfile = _load_lockfile(lockfile_path, output_dir)
    to_download: dict[str, Union[str, os.PathLike[str]]] = {}

    for artifact in lockfile.artifacts:
        # create the parent directory for the artifact
        Path.mkdir(Path(artifact.filename).parent, parents=True, exist_ok=True)
        to_download[str(artifact.download_url)] = artifact.filename

    asyncio.run(async_download_files(to_download, get_config().concurrency_limit))

    # verify checksums
    for artifact in lockfile.artifacts:
        must_match_any_checksum(artifact.filename, [artifact.formatted_checksum])
    return [artifact.get_sbom_component() for artifact in lockfile.artifacts]


def _load_lockfile(lockfile_path: Path, output_dir: RootedPath) -> GenericLockfileV1:
    """
    Load the generic lockfile from the given path.

    :param lockfile_path: the path to the lockfile
    :param output_dir: path to output directory
    """
    with open(lockfile_path, "r") as f:
        try:
            lockfile_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise PackageRejected(
                f"{APP_NAME} lockfile '{lockfile_path}' yaml format is not correct: {e}",
                solution="Check correct 'yaml' syntax in the lockfile.",
            )

        try:
            lockfile = GenericLockfileV1.model_validate(
                lockfile_data, context={"output_dir": output_dir}
            )
        except ValidationError as e:
            loc = e.errors()[0]["loc"]
            msg = e.errors()[0]["msg"]
            raise PackageRejected(
                f"{APP_NAME} lockfile '{lockfile_path}' format is not valid: '{loc}: {msg}'",
                solution=(
                    "Check the correct format and whether any keys are missing in the lockfile."
                ),
            )
    return lockfile
