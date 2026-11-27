# SPDX-License-Identifier: GPL-3.0-only
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hermeto.core.errors import ExitError
from hermeto.core.package_managers.gomod.main import ModuleVersionResolver
from hermeto.core.rooted_path import RootedPath
from tests.integration import utils

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@pytest.mark.parametrize(
    "test_params",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_with_deps",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_without_deps",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_correct_vendor_passes_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": "integration-tests", "type": "gomod"},),
            ),
            id="gomod_correct_vendor_in_submodule_passes_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_REJECTED,
                expected_output=(
                    "PackageRejected: The content of the vendor directory is not "
                    "consistent with go.mod. Please check the logs for more details"
                ),
            ),
            id="gomod_wrong_vendor_fails_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": "integration-tests", "type": "gomod"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_REJECTED,
                expected_output=(
                    "PackageRejected: The content of the vendor directory is not "
                    "consistent with go.mod. Please check the logs for more details"
                ),
            ),
            id="gomod_wrong_vendor_in_submodule_fails_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                global_flags=["--mode=permissive"],
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_wrong_vendor_passes_vendor_check_in_permissive_mode",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
                check_output=False,
                expected_error=ExitError.ERR_PACKAGE_REJECTED,
                expected_output=(
                    "PackageRejected: The content of the vendor directory is not "
                    "consistent with go.mod. Please check the logs for more details"
                ),
            ),
            id="gomod_empty_vendor_fails_vendor_check",
        ),
        pytest.param(
            utils.TestParameters(
                global_flags=["--mode=permissive"],
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_empty_vendor_passes_vendor_check_in_permissive_mode",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_local_deps",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            id="gomod_generate_imported",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": ".", "type": "gomod"},
                    {"path": "spam-module", "type": "gomod"},
                    {"path": "eggs-module", "type": "gomod"},
                ),
            ),
            id="gomod_missing_checksums",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": "./workspace_modules/hello", "type": "gomod"},),
            ),
            id="gomod_workspaces",
        ),
    ],
)
def test_gomod_packages(
    test_params: utils.TestParameters,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Test fetched dependencies for gomod."""
    test_case = request.node.callspec.id
    repo_dir = utils.create_synthetic_repo(tmp_path, SCENARIOS_DIR / test_case)

    utils.fetch_deps_and_check_output(
        tmp_path, test_case, test_params, repo_dir, SCENARIOS_DIR, hermeto_image
    )


@pytest.mark.parametrize(
    "test_params,check_cmd,expected_cmd_output",
    [
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            ["retrodep", "--help"],
            ["retrodep: help requested"],
            id="gomod_e2e_1_18",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": ".", "type": "gomod"},),
            ),
            ["retrodep", "--help"],
            ["retrodep: help requested"],
            id="gomod_e2e_1_21",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": ".", "type": "gomod"},
                    {"path": "spam-module", "type": "gomod"},
                    {"path": "eggs-module", "type": "gomod"},
                ),
            ),
            [],
            [""],
            id="gomod_e2e_multiple_modules",
        ),
        pytest.param(
            utils.TestParameters(
                packages=({"path": "hi/hiii", "type": "gomod"},),
            ),
            [],
            [""],
            id="gomod_e2e_1_22_workspace_vendoring",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": "vendored-module", "type": "gomod"},
                    {"path": "non-vendored-module", "type": "gomod"},
                ),
            ),
            [],
            [""],
            id="gomod_e2e_vendor_nonvendor_module_mix_ordering_1",
        ),
        pytest.param(
            utils.TestParameters(
                packages=(
                    {"path": "non-vendored-module", "type": "gomod"},
                    {"path": "vendored-module", "type": "gomod"},
                ),
            ),
            [],
            [""],
            id="gomod_e2e_vendor_nonvendor_module_mix_ordering_2",
        ),
    ],
)
def test_e2e_gomod(
    test_params: utils.TestParameters,
    check_cmd: list[str],
    expected_cmd_output: str,
    hermeto_image: utils.HermetoImage,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """End to end test for gomod."""
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


def _run_git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` with an isolated config and a fixed identity; return stripped stdout."""
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    # protocol.file.allow is required to use local paths as submodule/remote URLs
    proc = subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],  # noqa: S607
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stderr}")
    return proc.stdout.strip()


def _submodule_has_commit(repo: Path, submodule: str, commit: str) -> bool:
    """Return whether the git dir of ``submodule`` in ``repo`` already contains ``commit``."""
    result = subprocess.run(
        ["git", "cat-file", "-e", commit],  # noqa: S607
        cwd=repo / ".git" / "modules" / submodule,
        capture_output=True,
    )
    return result.returncode == 0


def test_fetch_tags_with_symlinked_submodule_path(tmp_path: Path) -> None:
    # A tag references a submodule whose path is a symlink in the checked-out tree; git's default
    # submodule recursion aborts such a fetch (CVE-2022-39253), so from_repo_path must disable it.
    sub = tmp_path / "sub"
    sub.mkdir()
    _run_git(sub, "init", "-q")
    (sub / "f").write_text("v1")
    _run_git(sub, "add", "-A")
    _run_git(sub, "commit", "-qm", "c1")
    sub_old_commit = _run_git(sub, "rev-parse", "HEAD")
    (sub / "f").write_text("v2")
    _run_git(sub, "add", "-A")
    _run_git(sub, "commit", "-qm", "c2")

    main = tmp_path / "main"
    main.mkdir()
    _run_git(main, "init", "-q")

    # tag v1.0: submodule at a/sub/mongocli, pinned to the older commit (needs a parent commit so
    # the gitlink shows up as a change when git inspects the fetched tag)
    _run_git(main, "checkout", "-q", "--orphan", "oldline")
    _run_git(main, "commit", "-q", "--allow-empty", "-m", "base")
    _run_git(main, "submodule", "add", "-q", "../sub", "a/sub/mongocli")
    _run_git(main / "a" / "sub" / "mongocli", "checkout", "-q", sub_old_commit)
    _run_git(main, "add", "-A")
    _run_git(main, "commit", "-qm", "old layout")
    _run_git(main, "tag", "v1.0")

    # HEAD, divergent from v1.0: submodule moved to shared/mongocli and a/sub is now a symlink to it
    _run_git(main, "rm", "-q", "a/sub/mongocli")
    shutil.rmtree(main / "a", ignore_errors=True)
    shutil.rmtree(main / ".git" / "modules" / "a", ignore_errors=True)
    _run_git(main, "checkout", "-q", "--orphan", "newline")
    _run_git(main, "rm", "-rqf", ".")
    _run_git(main, "submodule", "add", "-q", "../sub", "shared/mongocli")
    (main / "a").mkdir()
    (main / "a" / "sub").symlink_to("../shared")
    _run_git(main, "add", "-A")
    _run_git(main, "commit", "-qm", "new layout")
    head_commit = _run_git(main, "rev-parse", "HEAD")

    # shallow checkout + shallow submodule so the older submodule commit is absent and recursion
    # has to fetch it
    work = tmp_path / "work"
    work.mkdir()
    _run_git(work, "init", "-q")
    _run_git(work, "remote", "add", "origin", main.as_uri())
    _run_git(work, "fetch", "-q", "--depth=1", "--no-tags", "origin", head_commit)
    _run_git(work, "checkout", "-q", head_commit)
    _run_git(work, "submodule", "update", "--init", "--depth", "1", "shared/mongocli")

    ModuleVersionResolver.from_repo_path(RootedPath(work))
    assert "v1.0" in _run_git(work, "tag").split()


def test_fetch_tags_does_not_recurse_into_submodules(tmp_path: Path) -> None:
    # Default fetch recurses and pulls a submodule commit pinned by a tag; from_repo_path skips
    # that, yet both end up with the same tags -- recursion never affects which tags are fetched.
    sub = tmp_path / "sub"
    sub.mkdir()
    _run_git(sub, "init", "-q")
    (sub / "lib.txt").write_text("v1")
    _run_git(sub, "add", "-A")
    _run_git(sub, "commit", "-qm", "c1")

    superproject = tmp_path / "super"
    superproject.mkdir()
    _run_git(superproject, "init", "-q")
    _run_git(superproject, "submodule", "add", "-q", "../sub", "mod")
    _run_git(superproject, "add", "-A")
    _run_git(superproject, "commit", "-qm", "add mod")
    _run_git(superproject, "tag", "v1.0.0")

    def clone_work(name: str) -> Path:
        work = tmp_path / name
        _run_git(
            tmp_path,
            "clone",
            "-q",
            "--no-tags",
            "--recurse-submodules",
            superproject.as_uri(),
            str(work),
        )
        return work

    work_recurse = clone_work("work_recurse")
    work_no_recurse = clone_work("work_no_recurse")

    # add a submodule commit and a tag pinning it, now absent from the already-made clones
    (sub / "lib.txt").write_text("v2")
    _run_git(sub, "add", "-A")
    _run_git(sub, "commit", "-qm", "c2")
    sub_new_commit = _run_git(sub, "rev-parse", "HEAD")
    _run_git(superproject / "mod", "fetch", "-q", "origin")
    _run_git(superproject / "mod", "checkout", "-q", sub_new_commit)
    _run_git(superproject, "add", "-A")
    _run_git(superproject, "commit", "-qm", "bump mod")
    _run_git(superproject, "tag", "v2.0.0")

    _run_git(
        work_recurse,
        "fetch",
        "--recurse-submodules=on-demand",
        "--force",
        "--",
        "origin",
        "+refs/tags/*:refs/tags/*",
    )
    ModuleVersionResolver.from_repo_path(RootedPath(work_no_recurse))

    assert sorted(_run_git(work_recurse, "tag").split()) == ["v1.0.0", "v2.0.0"]
    assert sorted(_run_git(work_no_recurse, "tag").split()) == ["v1.0.0", "v2.0.0"]
    # recursion pulled the submodule commit; the no-recurse fetch left it absent
    assert _submodule_has_commit(work_recurse, "mod", sub_new_commit) is True
    assert _submodule_has_commit(work_no_recurse, "mod", sub_new_commit) is False
