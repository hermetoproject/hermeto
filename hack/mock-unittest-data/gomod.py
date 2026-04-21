#!/usr/bin/env python3
import json
import subprocess
from functools import partial
from itertools import chain
from os import environ
from pathlib import Path
from shutil import copyfile
from sys import argv
from tempfile import TemporaryDirectory

from yarn import print_banner


INTEGRATION_TESTS_DIR = "integration-tests"


def mkdir_with_parents(dirname):
    return Path(dirname).mkdir(exist_ok=True, parents=True)


def run(command_string, **kwargs):
    cmd = command_string.split()
    return subprocess.run(cmd, check=True, text=True, **kwargs)


def generate_auxiliary_dirs(mocked_data_dir):
    mkdir_with_parents(f"{mocked_data_dir}/non-vendored")
    mkdir_with_parents(f"{mocked_data_dir}/vendored")
    mkdir_with_parents(f"{mocked_data_dir}/workspaces")


def write_to_file(where, what, mocked_data_dir, **subprocess_kwargs):
    print(f"generating {mocked_data_dir}/{where}")
    with open(f"{mocked_data_dir}/{where}", "w") as f:
        run(what, stdout=f, **subprocess_kwargs)


def replace_paths_with_placeholder(integration_tests_dir, gomodcache, mocked_data_dir):
    subdirs_of_interest = ("non-vendored", "vendored", "workspaces")
    fs_objects_to_check = [(mocked_data_dir/x).glob("**/*") for x in subdirs_of_interest]
    files_to_update = [o for o in chain.from_iterable(fs_objects_to_check) if o.is_file()]
    for fpath in files_to_update:
        data = fpath.read_text()
        data = data.replace(str(gomodcache), "{gomodcache_dir}")
        data = data.replace(str(integration_tests_dir), "{repo_dir}")
        fpath.write_text(data)


def update_workspaces(redirect_to_file, integration_tests_dir, mocked_data_dir):
    with open(f"{mocked_data_dir}/workspaces/go_work.json") as f:
        d_paths = [v['DiskPath'] for v in json.loads(f.read())['Use']]
        paths = [Path(f"{integration_tests_dir}")/p.removeprefix("./") for p in d_paths if p != '.']

    for p in paths:
        print(f"preparing per-workspace directory {p}")
        rel_path = p.relative_to(integration_tests_dir)
        workspace_mock_path = f"{mocked_data_dir}/workspaces/{rel_path}"
        mkdir_with_parents(workspace_mock_path)
        if (gosum:=Path(p)/"go.sum").exists():
            print(f"generating {workspace_mock_path}/go.sum")
            copyfile(gosum, Path(f"{workspace_mock_path}/go.sum"))
        redirect_to_file(
            where=f"workspaces/{rel_path}/go_list_deps_threedot.json",
            what="go list -deps -json=ImportPath,Module,Standard,Deps ./...",
            cwd=p
        )


def warn_about_potential_manual_intervention(mocked_data_dir):
    non_vendored_diff = run(f"git diff -- {mocked_data_dir}/non-vendored", capture_output=True).stdout
    vendored_diff = run(f"git diff -- {mocked_data_dir}/vendored", capture_output=True).stdout
    if vendored_diff or non_vendored_diff:
        nonvendor = f"{mocked_data_dir}/expected-results/resolve_gomod.json"
        vendor = f"{mocked_data_dir}/expected-results/resolve_gomod_vendored.json"
        banner = f"""The mock data changed => the expected unit test results may change.
            The following files may need to be adjusted manually:
            {nonvendor if non_vendored_diff else ""}
            {vendor if vendored_diff else ""}
        """
        print_banner(banner)


def main() -> None:
    print_banner("Generating mock data for gomod unit tests")
    mocked_data_dir = Path(argv[1] if len(argv) > 1 else "tests/unit/data/gomod-mocks")
    generate_auxiliary_dirs(mocked_data_dir)
    with TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        integration_tests_dir = tmpdir / INTEGRATION_TESTS_DIR
        gomodcache = tmpdir / "hermeto-mock-gomodcache"

        subprocess_kwargs = {
            "env": environ | {"GOMODCACHE": str(gomodcache)},
            "cwd": integration_tests_dir
        }

        run(f"git clone https://github.com/hermetoproject/integration-tests {integration_tests_dir}")
        run("git switch gomod/go-1.22-workspaces", **subprocess_kwargs)
        redirect_to_file = partial(
            write_to_file, mocked_data_dir=mocked_data_dir, **subprocess_kwargs
        )
        redirect_to_file(where="workspaces/go_list_modules.json", what="go list -m -json")
        redirect_to_file(where="workspaces/go_mod_download.json", what="go mod download -json")
        redirect_to_file(
            where="workspaces/go_list_deps_all.json",
            what="go list -deps -json=ImportPath,Module,Standard,Deps all"
        )
        redirect_to_file(
            where="workspaces/go_list_deps_threedot.json",
            what="go list -deps -json=ImportPath,Module,Standard,Deps ./..."
        )
        redirect_to_file(where="workspaces/go_work.json", what="go work edit -json")
        if (go_work_sum := integration_tests_dir / "go.work.sum").exists():
            print(f"generating {mocked_data_dir}/workspaces/go.sum")
            copyfile(go_work_sum, Path(f"{mocked_data_dir}/workspaces/go.sum"))
        update_workspaces(
            redirect_to_file=redirect_to_file,
            integration_tests_dir=integration_tests_dir,
            mocked_data_dir=mocked_data_dir
        )
        run("git restore .", **subprocess_kwargs)
        run("git switch gomod/with-deps", **subprocess_kwargs)
        redirect_to_file = partial(
            write_to_file, mocked_data_dir=mocked_data_dir, **subprocess_kwargs
        )
        redirect_to_file(where="non-vendored/go_list_modules.json", what="go list -m -json")
        redirect_to_file(where="non-vendored/go_mod_download.json", what="go mod download -json")
        redirect_to_file(
            where="non-vendored/go_list_deps_all.json",
            what="go list -deps -json=ImportPath,Module,Standard,Deps all"
        )
        redirect_to_file(
            where="non-vendored/go_list_deps_threedot.json",
            what="go list -deps -json=ImportPath,Module,Standard,Deps ./..."
        )
        print(f"generating {mocked_data_dir}/non-vendored/go.sum")
        copyfile(integration_tests_dir/"go.sum", Path(f"{mocked_data_dir}/non-vendored/go.sum"))
        print(f"generating {mocked_data_dir}/vendored/modules.txt")
        run("go mod vendor", **subprocess_kwargs)
        run("go mod tidy", **subprocess_kwargs)
        copyfile(
            integration_tests_dir/"vendor/modules.txt",
            Path(f"{mocked_data_dir}/vendored/modules.txt")
        )
        redirect_to_file(
            where="vendored/go_list_deps_all.json",
            what="go list -deps -json=ImportPath,Module,Standard,Deps all"
        )
        redirect_to_file(
            where="vendored/go_list_deps_threedot.json",
            what="go list -deps -json=ImportPath,Module,Standard,Deps ./..."
        )
        print(f"generating {mocked_data_dir}/vendored/go.sum")
        copyfile(integration_tests_dir/"go.sum", Path(f"{mocked_data_dir}/vendored/go.sum"))
        replace_paths_with_placeholder(integration_tests_dir, gomodcache, mocked_data_dir)
        warn_about_potential_manual_intervention(mocked_data_dir)


if __name__ == "__main__":
    main()
