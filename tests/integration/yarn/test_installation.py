# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path

import pytest

from tests.integration import utils

INTEGRATION_DIR = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "scenario, package_manager, version",
    [
        pytest.param(
            "yarn_classic/scenarios/yarn_classic_lifecycle_scripts",
            None,
            "1.22.22",
            id="classic-default",
        ),
        pytest.param(
            "yarn_classic/scenarios/yarn_classic_lifecycle_scripts",
            "yarn@1.22.19",
            "1.22.19",
            id="classic-pinned",
        ),
        pytest.param(
            "yarn/scenarios/yarn_correct_version_is_installed",
            None,
            "3.6.1",
            id="berry-v3",
        ),
        pytest.param(
            "yarn/scenarios/yarn_installation_v4",
            None,
            "4.6.0",
            id="berry-v4",
        ),
    ],
)
def test_fetch_without_yarn_shims_and_reuse_cached_release(
    scenario: str,
    package_manager: str | None,
    version: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
) -> None:
    repo_dir = utils.create_synthetic_repo(tmp_path, INTEGRATION_DIR / scenario)
    if package_manager:
        package_json = repo_dir / "package.json"
        data = json.loads(package_json.read_text())
        data["packageManager"] = package_manager
        package_json.write_text(json.dumps(data) + "\n")

    output, exit_code = hermeto_image.run_cmd_on_image(
        ["-c", "command -v node && ! command -v corepack && ! command -v yarn"],
        tmp_path,
        entrypoint="/bin/sh",
    )
    assert exit_code == 0, output

    cache_dir = tmp_path / "cache with spaces"
    test_params = utils.TestParameters(
        packages=({"path": ".", "type": "yarn"},),
        check_output=False,
        expected_output=f"Processing the request using yarn@{version}",
        hermeto_env={"XDG_CACHE_HOME": str(cache_dir)},
    )
    utils.fetch_deps_and_check_output(
        tmp_path, "yarn-runtime", test_params, repo_dir, INTEGRATION_DIR, hermeto_image
    )

    # These projects have no dependencies; an offline retry must reuse the downloaded Yarn.
    output, exit_code = hermeto_image.run_cmd_on_image(
        [
            "fetch-deps",
            "--source",
            str(repo_dir),
            "--output",
            str(tmp_path / "offline-output"),
            json.dumps(test_params.packages),
        ],
        tmp_path,
        net="none",
        podman_flags=["--env", f"XDG_CACHE_HOME={cache_dir}"],
    )
    assert exit_code == 0, output
    assert test_params.expected_output in output
    assert "All dependencies fetched successfully" in output
