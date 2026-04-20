# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import logging
import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from hermeto.core.checksum import must_match_any_checksum
from hermeto.core.config import get_config
from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound, PackageRejected
from hermeto.core.models.input import Request
from hermeto.core.models.output import RequestOutput
from hermeto.core.models.sbom import Component, create_backend_annotation
from hermeto.core.package_managers.general import async_download_files
from hermeto.core.package_managers.generic.models import GenericLockfile, GenericLockfileAdapter
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)
DEFAULT_LOCKFILE_NAME = "artifacts.lock.yaml"
DEFAULT_DEPS_DIR = "deps/generic"

Url = str
AuthHeaders = dict[str, str]


def fetch_generic_source(request: Request) -> RequestOutput:
    """
    Resolve and fetch generic dependencies for a given request.

    :param request: the request to process
    """
    components = []
    for package in request.generic_packages:
        lockfile_path = _resolve_lockfile_path(
            request.source_dir,
            package.path,
            package.lockfile,
        )
        components.extend(_resolve_generic_lockfile(lockfile_path, request.output_dir))
    annotations = []
    if backend_annotation := create_backend_annotation(components, "generic"):
        annotations.append(backend_annotation)
    return RequestOutput.from_obj_list(components=components, annotations=annotations)


def _resolve_lockfile_path(
    source_dir: RootedPath,
    package_path: Path,
    lockfile_path: Path | None,
) -> Path:
    """
    Return the lockfile path for a package.
    """
    if lockfile_path and lockfile_path.is_absolute():
        return lockfile_path

    path = source_dir.join_within_root(package_path)
    lockfile_name = lockfile_path or DEFAULT_LOCKFILE_NAME
    lockfile = path.join_within_root(lockfile_name).path

    if not lockfile.is_relative_to(path.path):
        raise PackageRejected(
            f"Supplied generic lockfile path '{lockfile_name}' must be inside the package "
            f"path '{package_path}'.",
            solution="Use a lockfile path located within the package path.",
        )

    return lockfile


def _resolve_generic_lockfile(lockfile_path: Path, output_dir: RootedPath) -> list[Component]:
    """
    Resolve the generic lockfile and pre-fetch the dependencies.

    :param lockfile_path: absolute path to the lockfile
    :param output_dir: the output directory to store the dependencies
    """
    if not lockfile_path.exists():
        raise LockfileNotFound(files=lockfile_path)

    # output_dir is now the root and cannot be escaped
    output_dir = output_dir.re_root(DEFAULT_DEPS_DIR)

    log.info(f"Reading generic lockfile: {lockfile_path}")
    lockfile = _load_lockfile(lockfile_path, output_dir)
    to_download: dict[Url, str | os.PathLike[str]] = {}
    auth_headers: dict[Url, AuthHeaders] = {}

    for artifact in lockfile.artifacts:
        # create the parent directory for the artifact
        Path.mkdir(Path(artifact.filename).parent, parents=True, exist_ok=True)
        url = str(artifact.download_url)
        to_download[url] = artifact.filename
        if headers := artifact.get_headers():
            auth_headers[url] = headers

    asyncio.run(
        async_download_files(
            to_download,
            get_config().runtime.concurrency_limit,
            headers=auth_headers or None,
        )
    )

    # verify checksums
    for artifact in lockfile.artifacts:
        must_match_any_checksum(artifact.filename, [artifact.formatted_checksum])
    return [artifact.get_sbom_component() for artifact in lockfile.artifacts]


def _load_lockfile(lockfile_path: Path, output_dir: RootedPath) -> GenericLockfile:
    """
    Load the generic lockfile from the given path.

    :param lockfile_path: the path to the lockfile
    :param output_dir: path to output directory
    """
    with open(lockfile_path) as f:
        try:
            lockfile_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise InvalidLockfileFormat(
                lockfile_path=lockfile_path,
                err_details=str(e),
                solution="Check correct 'yaml' syntax in the lockfile.",
            )

        if lockfile_data is None:
            raise InvalidLockfileFormat(
                lockfile_path=lockfile_path,
                err_details="Lockfile is empty",
                solution=(
                    "Ensure the lockfile contains valid YAML with "
                    "'metadata' and 'artifacts' sections."
                ),
            )

        try:
            lockfile = GenericLockfileAdapter.validate_python(
                lockfile_data, context={"output_dir": output_dir}
            )
        except ValidationError as e:
            err = e.errors()[0]
            if err["type"] == "union_tag_not_found":
                err_details = "Missing required 'metadata.version' field"
            elif err["type"] == "union_tag_invalid":
                err_details = err["msg"]
            else:
                err_details = f"'{err['loc']}: {err['msg']}'"
            raise InvalidLockfileFormat(
                lockfile_path=lockfile_path,
                err_details=err_details,
                solution=(
                    "Check the correct format and whether any keys are missing in the lockfile."
                ),
            )
    return lockfile
