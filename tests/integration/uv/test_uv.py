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


def test_e2e_uv_binary_filter(
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
) -> None:
    """A filtered Linux wheel set must install in a network-disabled container."""
    test_case = "uv_binary_filter"
    test_params = utils.TestParameters(
        packages=(
            {
                "path": ".",
                "type": "x-uv",
                "binary": {"os": "linux", "arch": ":all:", "py_version": 312},
            },
        ),
        check_output=True,
    )
    repo_dir = utils.create_synthetic_repo(tmp_path, SCENARIOS_DIR / test_case)

    actual_repo_dir = utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )

    fetched_filenames = {
        path.name for path in (tmp_path / "hermeto-output" / "deps" / "uv").iterdir()
    }
    assert fetched_filenames == {
        "anyio-4.16.0-py3-none-any.whl",
        "anyio-4.16.0.tar.gz",
        "cffi-2.1.1-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        "cffi-2.1.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "cffi-2.1.1-cp312-cp312-musllinux_1_2_aarch64.whl",
        "cffi-2.1.1-cp312-cp312-musllinux_1_2_x86_64.whl",
        "cffi-2.1.1.tar.gz",
        "charset_normalizer-3.5.2-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl",
        "charset_normalizer-3.5.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
        "charset_normalizer-3.5.2-cp312-cp312-musllinux_1_2_aarch64.whl",
        "charset_normalizer-3.5.2-cp312-cp312-musllinux_1_2_x86_64.whl",
        "charset_normalizer-3.5.2-py3-none-any.whl",
        "charset_normalizer-3.5.2.tar.gz",
        "idna-3.20-py3-none-any.whl",
        "idna-3.20.tar.gz",
        "pycparser-3.12-py3-none-any.whl",
        "pycparser-3.12.tar.gz",
        "typing_extensions-4.16.0-py3-none-any.whl",
        "typing_extensions-4.16.0.tar.gz",
    }

    utils.build_image_and_check_cmd(
        tmp_path,
        actual_repo_dir,
        SCENARIOS_DIR,
        test_case,
        test_params,
        ["/app/.venv/bin/python", "/app/main.py"],
        "binary-filtered-dependencies-installed",
        hermeto_image,
    )
