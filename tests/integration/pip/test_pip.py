# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path

import pytest

from hermeto import APP_NAME
from hermeto.core.errors import ExitError
from tests.integration import utils

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


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
    "test_params,check_cmd,expected_cmd_output",
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
            id="pip_e2e_wheels",
        ),
        # The test relies on rpm and thus requires rpms.lock.yaml defined.
        # The lock file could be generated by https://github.com/konflux-ci/rpm-lockfile-prototype
        # The necessary repo definition for it could be found in UBI image
        # (/etc/yum.repos.d/ubi.repo) and extracted to a local directory.
        pytest.param(
            utils.TestParameters(
                packages=(({"type": "pip"}, {"type": "rpm"})),
                check_output=True,
            ),
            ["python3", "/app/main.py"],
            [],
            id="pip_e2e_rust_extensions",
        ),
    ],
)
def test_e2e_pip(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
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
