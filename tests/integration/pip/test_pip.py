# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
import requests
from packaging.utils import canonicalize_name, parse_sdist_filename

from hermeto import APP_NAME
from hermeto.core.errors import ExitError
from tests.integration import utils
from tests.nexusserver.configure import (
    DEFAULT_NEXUS_ADMIN_PASSWORD,
    DEFAULT_NEXUS_HOST,
    DEFAULT_NEXUS_PORT,
)

log = logging.getLogger(__name__)

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _build_and_publish_sdist(
    tmp_path: Path,
    source_dir: Path,
    build_command: Sequence[str],
) -> Path:
    """Build an sdist from source, publish it to local Nexus, and verify it."""

    # Build from a clean copy outside the main Git repository.
    build_source_dir = tmp_path / "sdist-source"
    shutil.copytree(
        source_dir,
        build_source_dir,
        ignore=shutil.ignore_patterns(".git", "dist", "target", "__pycache__"),
    )

    dist_dir = tmp_path / "sdist-dist"
    dist_dir.mkdir()

    # The command uses {dist_dir} to specify where the sdist should be written.
    command = [arg.replace("{dist_dir}", str(dist_dir)) for arg in build_command]
    if not any("{dist_dir}" in arg for arg in build_command):
        raise ValueError("build_command must contain a {dist_dir} placeholder")

    subprocess.run(command, cwd=build_source_dir, check=True)
    sdists = sorted([*dist_dir.glob("*.tar.gz"), *dist_dir.glob("*.zip")])
    if len(sdists) != 1:
        raise RuntimeError(f"Expected exactly one sdist, found: {sdists}")

    sdist = sdists[0]
    digest = hashlib.sha256(sdist.read_bytes()).hexdigest()
    package_name, _ = parse_sdist_filename(sdist.name)
    package_name = canonicalize_name(package_name)

    # Publish directly to the PyPI hosted repository.
    base_url = f"http://{DEFAULT_NEXUS_HOST}:{DEFAULT_NEXUS_PORT}"
    with sdist.open("rb") as archive:
        response = requests.post(
            f"{base_url}/service/rest/v1/components",
            params={"repository": "pypi-hosted"},
            auth=("admin", DEFAULT_NEXUS_ADMIN_PASSWORD),
            files={"pypi.asset": (sdist.name, archive)},
            timeout=60,
        )
    response.raise_for_status()

    # Confirm the generated artifact is visible through the PyPI group.
    index_url = f"{base_url}/repository/pypi-group/simple/{package_name}/"
    response = requests.get(index_url, timeout=30)
    response.raise_for_status()
    assert sdist.name in response.text, f"{sdist.name} is missing from the PyPI group index"
    assert f"sha256={digest}" in response.text, (
        "The checksum in the Nexus index differs from the generated sdist"
    )

    return sdist


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip"},),
            ),
            id="pip_without_deps",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip"},),
            ),
            id="pip_missing_hashes",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": "first", "type": "pip"},
                    {"path": "second", "type": "pip"},
                ),
            ),
            id="pip_multiple_packages",
        ),
        # Test case checks that an attempt to fetch a local file will result in failure.
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip"},),
                check_output=False,
                expected_error=ExitError.ERR_UNSUPPORTED_FEATURE,
                expected_output=(
                    "UnsupportedFeature: Direct references with 'file' scheme are not supported, "
                    "'file:///tmp/packages.zip'\n  "
                    f"If you need {APP_NAME} to support this feature, please contact the maintainers."
                ),
            ),
            id="pip_local_path",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": ".", "type": "pip"},
                    {"path": "subpath1/subpath2", "type": "pip"},
                ),
            ),
            id="pip_no_metadata",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip"},),
            ),
            id="pip_yanked",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip", "binary": {}},),
            ),
            id="pip_no_wheels",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_REJECTED,
                expected_output="Error: PackageRejected: No distributions found",
            ),
            id="pip_no_sdists",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip", "binary": {}},),
                netrc_content="machine 127.0.0.1 login hermeto-user password hermeto-pass",
            ),
            id="pip_custom_index",
            marks=pytest.mark.skipif(
                os.getenv("HERMETO_TEST_LOCAL_NEXUS") != "1",
                reason="HERMETO_TEST_LOCAL_NEXUS!=1",
            ),
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip"},),
                global_flags=["--mode", "permissive"],
                check_output=False,
            ),
            id="pip_rust_extension_lock_and_config_mismatch_permissive",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip"},),
                global_flags=["--mode", "strict"],
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_WITH_CORRUPT_LOCKFILE_REJECTED,
                expected_output="PackageWithCorruptLockfileRejected",
            ),
            id="pip_rust_extension_lock_and_config_mismatch_strict",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "pip"},),
                check_output=False,
            ),
            id="pip_rust_dependency_unusual_cargo_toml_location",
        ),
    ],
)
def test_pip_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test fetched dependencies for pip.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id
    repo_dir = utils.create_synthetic_repo(tmp_path, SCENARIOS_DIR / test_case)

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )


def test_pip_binary_exclusions(
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
) -> None:
    test_case = "pip_binary_exclusions"
    repo_dir = utils.create_synthetic_repo(tmp_path, SCENARIOS_DIR / test_case)
    test_params = utils.TestParameters(
        packages=({"type": "pip", "binary": {"packages": "!six,!iniconfig"}},),
        check_output=False,
    )

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )

    deps_dir = tmp_path / utils.DEFAULT_OUTPUT / "deps" / "pip"
    artifacts = set(artifact.name for artifact in deps_dir.iterdir())
    artifact_types = set()
    for artifact in artifacts:
        extension = ".whl" if artifact.endswith(".whl") else ".tar.gz"
        artifact_types.add((artifact.split("-", maxsplit=1)[0], extension))

    assert artifact_types == {
        ("six", ".tar.gz"),
        ("iniconfig", ".tar.gz"),
        ("editables", ".whl"),
        ("editables", ".tar.gz"),
    }


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output,sdist_source,sdist_build_command",
    [
        # Test case checks fetching pip dependencies, generating environment vars file,
        # building image with all prepared prerequisites and testing if pip packages are present
        # in built image
        pytest.param(
            utils.TestParameters(
                packages=(
                    {
                        "type": "pip",
                        "requirements_files": ["requirements.txt"],
                        "requirements_build_files": ["requirements-build.txt"],
                    },
                ),
            ),
            ["python3", "/app/src/test_package/main.py"],
            ["1.0.0"],
            None,
            None,
            id="pip_e2e",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {
                        "type": "pip",
                        "requirements_files": ["requirements.txt"],
                        "requirements_build_files": [],
                        "binary": {"py_version": 312, "platform": "^(any|manylinux.*)$"},
                    },
                ),
            ),
            ["python3", "/app/package/main.py"],
            ["Hello, world!"],
            None,
            None,
            id="pip_e2e_wheels",
        ),
        # The test relies on rpm and thus requires rpms.lock.yaml defined.
        # The lock file could be generated by https://github.com/konflux-ci/rpm-lockfile-prototype
        # The necessary repo definition for it could be found in UBI image
        # (/etc/yum.repos.d/ubi.repo) and extracted to a local directory.
        pytest.param(
            utils.TestParameters(
                packages=(
                    (
                        {
                            "type": "pip",
                            "binary": {
                                "packages": "maturin",
                                "py_impl": "py",
                            },
                        },
                        {"type": "rpm"},
                    )
                ),
                check_output=True,
                hermeto_env={
                    "HERMETO_PIP__PROXY_URL": (
                        "https://127.0.0.1:8443/repository/pypi-group/simple/"
                    ),
                },
            ),
            ["python3", "/app/main.py"],
            [],
            "fixtures/hello",
            ["maturin", "sdist", "--out", "{dist_dir}"],
            id="pip_e2e_rust_extensions",
        ),
    ],
)
def test_e2e_pip(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    sdist_source: str | None,
    sdist_build_command: list[str] | None,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    End to end test for pip.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id
    if sdist_source is not None and sdist_build_command is not None:
        _build_and_publish_sdist(
            tmp_path=tmp_path,
            source_dir=SCENARIOS_DIR / test_case / sdist_source,
            build_command=sdist_build_command,
        )
    repo_dir = utils.create_synthetic_repo(tmp_path, SCENARIOS_DIR / test_case)

    actual_repo_dir = utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )

    utils.build_image_and_check_cmd(
        tmp_path,
        actual_repo_dir,
        SCENARIOS_DIR,
        test_case,
        test_params,
        check_cmd,
        expected_cmd_output,
        hermeto_image,
    )
