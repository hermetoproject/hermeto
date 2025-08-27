#!/usr/bin/env python3
import itertools
import json
import pprint
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Callable, Optional
from collections.abc import Iterator


def print_banner(content: str) -> None:
    print("-" * 80)
    print("\n".join(textwrap.wrap(content, 80)))
    print("-" * 80)


def clone_repo(git_executable: str, tmpdir: Path) -> Path:
    repo_dir = tmpdir / "cachi2-yarn-berry"
    subprocess.run(
        [
            git_executable,
            "clone",
            "https://github.com/cachito-testing/cachi2-yarn-berry",
            "--depth=1",
            "--single-branch",
            "--branch=zero-installs",
            repo_dir,
        ],
        check=True,
    )
    return repo_dir


def run_yarninfo(yarn_executable: str, repo_dir: Path) -> str:
    proc = subprocess.run(
        [yarn_executable, "info", "--all", "--recursive", "--json", "--cache"],
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        proc.check_returncode()
    except subprocess.CalledProcessError:
        # yarn prints errors to stdout
        print(proc.stdout, file=sys.stderr)
        raise
    return proc.stdout


def process_yarninfo(yarninfo_output: str, repo_dir: Path) -> list[dict[str, Any]]:
    yarn_packages = map(json.loads, yarninfo_output.splitlines())
    # drop dependencies that use the unsupported git protocol
    yarn_packages_no_git_protocol = [pkg for pkg in yarn_packages if "commit=" not in pkg["value"]]

    def has_path_but_no_checksum(pkg: dict[str, Any]) -> bool:
        cache = pkg["children"]["Cache"]
        return cache["Checksum"] is None and cache["Path"] is not None

    pkgchain = itertools.chain(
        # take 1 or 2 examples of each supported protocol
        _filter_pkgs_by_pattern("@npm:", yarn_packages_no_git_protocol, 1),
        _filter_pkgs_by_pattern("@workspace:", yarn_packages_no_git_protocol, 2),
        _filter_pkgs_by_pattern("@patch:", yarn_packages_no_git_protocol, 2),
        _filter_pkgs_by_pattern("@file:", yarn_packages_no_git_protocol, 2),
        _filter_pkgs_by_pattern("@portal:", yarn_packages_no_git_protocol, 1),
        _filter_pkgs_by_pattern("@link:", yarn_packages_no_git_protocol, 1),
        _filter_pkgs_by_pattern("@https:.*tar.gz", yarn_packages_no_git_protocol, 1),
        # make sure to include the curious case where checksum is null but path isn't
        _filter_pkgs(has_path_but_no_checksum, yarn_packages_no_git_protocol),
    )

    # sort packages and make them unique by "value" (the locator)
    pkgdict = {pkg["value"]: pkg for pkg in pkgchain}
    selected_packages = [pkg for _, pkg in sorted(pkgdict.items())]

    for pkg in selected_packages:
        # drop large unused Dependencies attribute
        pkg["children"].pop("Dependencies", None)
        # replace the repo directory path in Cache.Path with a placeholder
        if cache_path := pkg["children"]["Cache"]["Path"]:
            path_with_placeholder = Path("{repo_dir}", Path(cache_path).relative_to(repo_dir))
            pkg["children"]["Cache"]["Path"] = path_with_placeholder.as_posix()

    return selected_packages


def _filter_pkgs(
    predicate: Callable[[dict[str, Any]], bool],
    pkgs: list[dict[str, Any]],
    max_items: Optional[int] = None,
) -> Iterator[dict[str, Any]]:
    filtered = filter(predicate, pkgs)
    if max_items:
        return itertools.islice(filtered, max_items)
    else:
        return filtered


def _filter_pkgs_by_pattern(
    pattern: str,
    pkgs: list[dict[str, Any]],
    max_items: Optional[int] = None,
) -> Iterator[dict[str, Any]]:
    # check if the locator contains the regex pattern
    def matches_pattern(pkg: dict[str, Any]) -> bool:
        return re.search(pattern, pkg["value"]) is not None

    return _filter_pkgs(matches_pattern, pkgs, max_items)


def need_command(name: str) -> str:
    cmd_path = shutil.which(name)
    if not cmd_path:
        raise ValueError(f"Command not found in PATH: {name}")
    return cmd_path


def main() -> None:
    print_banner("Generating mock data for yarn unit tests")

    git_executable = need_command("git")
    yarn_executable = need_command("yarn")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = clone_repo(git_executable, Path(tmpdir))
        yarninfo_output = run_yarninfo(yarn_executable, repo_dir)

    selected_packages = process_yarninfo(yarninfo_output, repo_dir)

    print_banner(
        "You can copy the following to tests/unit/package_managers/yarn/test_resolver.py "
        "(you will need to re-format it with 'ruff')"
    )

    pprint.pprint(selected_packages, sort_dicts=False)


if __name__ == "__main__":
    main()
