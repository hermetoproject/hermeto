# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from hermeto import APP_NAME
from tests.integration import utils

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "x-uv"},),
            ),
            id="uv_sdist_only_sources",
        ),
    ],
)
def test_uv_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    test_case = request.node.callspec.id
    repo_dir = utils.create_synthetic_repo(tmp_path, SCENARIOS_DIR / test_case)

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        # Fetch the dependencies, build an image with no network access and check that
        # `uv sync` installed everything from the prefetched artifacts alone.
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "x-uv"},),
                # pip's code downloads requirements-build.txt for uv and would apply pip's proxy
                # settings to these uv build dependencies, so run without them.
                unset_hermeto_env={
                    f"{APP_NAME.upper()}_PIP__PROXY_URL",
                    f"{APP_NAME.upper()}_PIP__PROXY_LOGIN",
                    f"{APP_NAME.upper()}_PIP__PROXY_PASSWORD",
                },
            ),
            ["/app/.venv/bin/python", "/app/uv_e2e/main.py"],
            ["idna==3.10", "itsdangerous==2.2.0", "packaging==25.0", "uv-e2e==1.0.0"],
            id="uv_e2e",
        ),
    ],
)
def test_e2e_uv(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
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


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "x-uv"},),
                unset_hermeto_env={
                    f"{APP_NAME.upper()}_PIP__PROXY_URL",
                    f"{APP_NAME.upper()}_PIP__PROXY_LOGIN",
                    f"{APP_NAME.upper()}_PIP__PROXY_PASSWORD",
                },
            ),
            ["/app/.venv/bin/python", "/app/main.py"],
            ["nested package works"],
            id="uv_package_inside_subdirectory",
        ),
    ],
)
def test_e2e_uv_package_inside_subdirectory(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Fetch and install a uv package located in a Git repository subdirectory."""
    test_case = request.node.callspec.id
    repo_dir = utils.create_synthetic_repo(tmp_path, SCENARIOS_DIR / test_case)
    # Hermeto clones the dependency in a separate container, where the synthetic
    # repository's local Git URL rewrite is unavailable. Map its canonical test URL to
    # the temporary local origin so the fixture remains network-independent.
    # Extract this into a utility if more tests need a Git URL rewrite
    # in the Hermeto container.
    nested_repo_url = "https://git.example.invalid/hermeto-tests/nested-example.git"
    nested_repo_origin = tmp_path / "origins" / "submodules" / "vendor" / "nested-repo"
    git_config = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": f"url.{nested_repo_origin.resolve().as_uri()}.insteadOf",
        "GIT_CONFIG_VALUE_0": nested_repo_url,
        "GIT_CONFIG_KEY_1": "protocol.file.allow",
        "GIT_CONFIG_VALUE_1": "always",
    }
    actual_repo_dir = utils.fetch_deps_and_check_output(
        tmp_path,
        test_case,
        test_params,
        repo_dir,
        SCENARIOS_DIR,
        hermeto_image,
        hermeto_env_overrides=git_config,
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
