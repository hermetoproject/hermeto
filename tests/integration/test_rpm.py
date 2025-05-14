import os
import re
from configparser import ConfigParser
from pathlib import Path
from typing import List

import pytest

from hermeto.interface.cli import DEFAULT_OUTPUT

from . import utils


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                branch="rpm/missing-checksum",
                packages=({"path": ".", "type": "rpm"},),
                flags=["--dev-package-managers"],
                check_output=True,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
            ),
            id="rpm_missing_checksum",
        ),
        pytest.param(
            utils.TestParameters(
                branch="rpm/unmatched-checksum",
                packages=({"path": ".", "type": "rpm"},),
                flags=["--dev-package-managers"],
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=2,
                expected_output="Unmatched checksum",
            ),
            id="rpm_unmatched_checksum",
        ),
        pytest.param(
            utils.TestParameters(
                branch="rpm/unexpected-size",
                packages=({"path": ".", "type": "rpm"},),
                flags=["--dev-package-managers"],
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=2,
                expected_output="Unexpected file size",
            ),
            id="rpm_unexpected_size",
        ),
        pytest.param(
            utils.TestParameters(
                branch="rpm/multiple-packages",
                packages=(
                    {"path": "this-project", "type": "rpm"},
                    {"path": "another-project", "type": "rpm"},
                ),
                flags=["--dev-package-managers"],
                check_output=True,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
            ),
            id="rpm_multiple_packages",
        ),
        pytest.param(
            utils.TestParameters(
                branch="rpm/multiple-archs",
                packages=({"path": ".", "type": "rpm"},),
                flags=["--dev-package-managers"],
                check_output=True,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
            ),
            id="rpm_multiple_archs",
        ),
        pytest.param(
            utils.TestParameters(
                branch="rpm/dnf-tls-client-auth",
                packages=(
                    {
                        "path": ".",
                        "type": "rpm",
                        "options": {
                            "ssl": {
                                "client_cert": "/certificates/client.crt",
                                "client_key": "/certificates/client.key",
                                "ca_bundle": "/certificates/CA.crt",
                                "ssl_verify": 0,
                            },
                        },
                    },
                ),
                flags=["--dev-package-managers"],
                check_output=True,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
            ),
            id="rpm_dnf_tls_client_auth",
            marks=pytest.mark.skipif(
                os.getenv("HERMETO_TEST_LOCAL_DNF_SERVER") != "true",
                reason="HERMETO_TEST_LOCAL_DNF_SERVER!=true",
            ),
        ),
        pytest.param(
            utils.TestParameters(
                branch="rpm/multiple-packages-summary",
                packages=(
                    {"path": "this-project", "type": "rpm", "include_summary_in_sbom": "true"},
                    {"path": "another-project", "type": "rpm"},
                ),
                flags=["--dev-package-managers"],
                check_output=True,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
            ),
            id="rpm_multiple_packages_summary",
        ),
    ],
)
def test_rpm_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    top_level_test_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    Test fetched dependencies for RPMs.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id

    utils.fetch_deps_and_check_output(
        tmp_path,
        test_case,
        test_params,
        test_repo_dir,
        test_data_dir,
        hermeto_image,
        mounts=[(top_level_test_dir / "dnfserver/certificates", "/certificates")],
    )


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                branch="rpm/repo-file",
                packages=({"path": ".", "type": "rpm"},),
                flags=["--dev-package-managers"],
                check_output=False,
                check_deps_checksums=False,
                check_vendor_checksums=False,
                expected_exit_code=0,
            ),
            id="rpm_repo_file",
        ),
    ],
)
def test_repo_files(
    test_params: utils.TestParameters,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Test if the contents of the generated .repo file are correct."""
    test_case = request.node.callspec.id
    output_dir = tmp_path.joinpath(DEFAULT_OUTPUT)

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )

    # call inject-files to create the .repo file
    cmd = [
        "inject-files",
        str(output_dir),
        "--for-output-dir",
        f"/tmp/{DEFAULT_OUTPUT}",
    ]
    (output, exit_code) = hermeto_image.run_cmd_on_image(cmd, tmp_path)
    assert exit_code == 0, f"Injecting project files failed. output-cmd: {output}"

    # load .repo file contents
    def read_and_normalize_repofile(path: Path) -> str:
        with open(path) as file:
            # whenever an RPM lacks a repoid in the lockfile, Hermeto will resort to a randomly
            # generated internal repoid, which needs to be replaced by a constant string so it can
            # be tested consistently.
            return re.sub(r"hermeto-[a-f0-9]{6}", "hermeto-aaa000", file.read())

    repo_file_content = read_and_normalize_repofile(
        output_dir.joinpath("deps/rpm/x86_64/repos.d/hermeto.repo")
    )

    # update test data if needed
    expected_repo_file_path = test_data_dir.joinpath(test_case, "hermeto.repo")

    if os.getenv("HERMETO_GENERATE_TEST_DATA") == "true":
        expected_repo_file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(expected_repo_file_path, "w") as file:
            file.write(repo_file_content)

    actual = ConfigParser()
    expected = ConfigParser()

    actual.read_string(repo_file_content)
    with open(expected_repo_file_path) as f:
        expected.read_file(f)

    # check if .repo file content matches the expected test data
    assert actual == expected


@pytest.mark.parametrize(
    "test_params, check_cmd, expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                branch="rpm/repo-metadata-compression-type",
                packages=(
                    {
                        "type": "rpm",
                        "options": {
                            "dnf": {"ubi-7": {"gpgcheck": 0}},
                        },
                    },
                ),
                flags=["--dev-package-managers"],
                check_vendor_checksums=False,
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            ["vim", "--version"],
            ["Vi IMproved 7.4"],
            id="rpm_repo_metadata_compression_type",
        ),
        # Test case that checks fetching RPM files, generating repos and repofiles, building an
        # image that requires the RPM files to be installed and running the image to check if the
        # RPMs were properly installed
        pytest.param(
            utils.TestParameters(
                branch="rpm/e2e",
                packages=(
                    {
                        "type": "rpm",
                    },
                ),
                flags=["--dev-package-managers"],
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            ["vim", "--version"],
            ["VIM - Vi IMproved 9.0"],
            id="rpm_e2e",
        ),
        # Test case that checks fetching RPM and module metadata files, generating repos and repofiles,
        # building an image that requires the RPM files to be installed and running the image to check
        # if the RPMs (including modular packages) were properly installed.
        pytest.param(
            utils.TestParameters(
                branch="rpm/e2e-modularity",
                packages=(
                    {
                        "type": "rpm",
                    },
                ),
                flags=["--dev-package-managers"],
                expected_exit_code=0,
                expected_output="All dependencies fetched successfully",
            ),
            ["ab", "-V"],
            ["This is ApacheBench, Version 2.3"],
            id="rpm_e2e_modularity",
        ),
    ],
)
def test_e2e_rpm(
    test_params: utils.TestParameters,
    check_cmd: List[str],
    expected_cmd_output: str,
    hermeto_image: utils.ContainerImage,
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    request: pytest.FixtureRequest,
) -> None:
    """
    End to end test for rpms.

    :param test_params: Test case arguments
    :param tmp_path: Temp directory for pytest
    """
    test_case = request.node.callspec.id

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, test_repo_dir, test_data_dir, hermeto_image
    )

    utils.build_image_and_check_cmd(
        tmp_path,
        test_repo_dir,
        test_data_dir,
        test_case,
        check_cmd,
        expected_cmd_output,
        hermeto_image,
    )
