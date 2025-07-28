# SPDX-License-Identifier: GPL-3.0-or-later
import functools
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tarfile import ExtractError, TarFile
from typing import Any, Optional

import jsonschema
import requests
import yaml
from git import Repo

from hermeto import APP_NAME
from hermeto.core import resolver
from hermeto.interface.cli import DEFAULT_OUTPUT
from tests.integration.container_engine import StrPath, get_container_engine

# force IPv4 localhost as 'localhost' can resolve with IPv6 as well
TEST_SERVER_LOCALHOST = "127.0.0.1"

# Individual files could be added to the set as well.
PATHS_TO_CODE = frozenset(
    (
        Path("hermeto"),
        Path("tests/integration"),
        Path("Dockerfile"),
        Path("Containerfile"),
        Path("requirements.txt"),
        Path("requirements-extras.txt"),
        Path("pyproject.toml"),
    )
)

# package managers that are not exposed to the user but are used internally
EXTRA_PMS = ["yarn_classic"]
SUPPORTED_PMS: frozenset[str] = frozenset(
    list(resolver._package_managers) + list(resolver._dev_package_managers) + EXTRA_PMS
)


log = logging.getLogger(__name__)
container_engine = get_container_engine()

# use the '|' style for multiline strings
# https://github.com/yaml/pyyaml/issues/240
yaml.representer.SafeRepresenter.add_representer(
    str,
    lambda dumper, data: dumper.represent_scalar(
        "tag:yaml.org,2002:str",
        data,
        style="|" if data.count("\n") > 0 else None,
    ),
)


CYCLONEDX_SCHEMA_URL = (
    "https://raw.githubusercontent.com/CycloneDX/specification/1.4/schema/bom-1.4.schema.json"
)


@dataclass
class TestParameters:
    branch: str
    packages: tuple[dict[str, Any], ...]
    check_output: bool = True
    check_deps_checksums: bool = True
    expected_exit_code: int = 0
    expected_output: str = ""
    global_flags: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


class ContainerImage:
    def __init__(self, repository: str):
        """Initialize ContainerImage object with associated repository."""
        self.repository = repository

    def __enter__(self) -> "ContainerImage":
        return self

    def pull_image(self) -> None:
        output, exit_code = container_engine.pull(self.repository)
        if exit_code != 0:
            raise RuntimeError(f"Pulling {self.repository} failed. Output:{output}")
        log.info("Pulled image: %s.", self.repository)

    def run_cmd_on_image(
        self,
        cmd: list[str],
        tmp_path: Path,
        mounts: Sequence[tuple[StrPath, StrPath]] = (),
        net: Optional[str] = None,
        podman_flags: Optional[list[str]] = None,
    ) -> tuple[str, int]:
        if podman_flags is None:
            podman_flags = []

        podman_flags.extend(["-v", f"{tmp_path}:{tmp_path}:z"])

        for src, dest in mounts:
            podman_flags.extend(["-v", f"{src}:{dest}:z"])
        if net:
            podman_flags.append(f"--net={net}")

        return container_engine.run(self.repository, cmd, podman_flags)

    def __exit__(self, exc_type: Any, exc_value: Any, exc_traceback: Any) -> None:
        output, exit_code = container_engine.rmi(self.repository)
        if exit_code != 0:
            raise RuntimeError(f"Image deletion failed. Output:{output}")


class HermetoImage(ContainerImage):
    def run_cmd_on_image(
        self,
        cmd: list[str],
        tmp_path: Path,
        mounts: Sequence[tuple[StrPath, StrPath]] = (),
        net: Optional[str] = "host",
        podman_flags: Optional[list[str]] = None,
    ) -> tuple[str, int]:
        netrc_content = os.getenv("HERMETO_TEST_NETRC_CONTENT")
        if netrc_content:
            with tempfile.TemporaryDirectory() as netrc_tmpdir:
                netrc_path = Path(netrc_tmpdir, ".netrc")
                netrc_path.write_text(netrc_content)
                return super().run_cmd_on_image(
                    cmd, tmp_path, [*mounts, (netrc_path, "/root/.netrc")], net, podman_flags
                )
        return super().run_cmd_on_image(cmd, tmp_path, mounts, net, podman_flags)


def build_image(context_dir: Path, tag: str) -> ContainerImage:
    return _build_image(["build", str(context_dir)], tag=tag)


def build_image_for_test_case(
    source_dir: Path,
    output_dir: Path,
    containerfile_path: Path,
    test_case: str,
) -> ContainerImage:
    # mounts the source code of the test case
    source_dir_mount_point = "/src"
    # mounts the output of the fetch-deps command and hermeto.env
    output_dir_mount_point = "/tmp"

    cmd = [
        "build",
        "-f",
        str(containerfile_path),
        "-v",
        f"{source_dir}:{source_dir_mount_point}:Z",
        "-v",
        f"{output_dir}:{output_dir_mount_point}:Z",
        "--no-cache",
        "--network",
        "none",
    ]

    # this should be extended to support more archs when we have the means of testing it in our CI
    rpm_repos_path = f"{output_dir}/hermeto-output/deps/rpm/x86_64/repos.d"
    if Path(rpm_repos_path).exists():
        cmd.extend(
            [
                "-v",
                f"{rpm_repos_path}:/etc/yum.repos.d:Z",
            ]
        )

    return _build_image(cmd, tag=f"localhost/{test_case}")


def _build_image(podman_cmd: list[str], *, tag: str) -> ContainerImage:
    (output, exit_code) = container_engine.build(podman_cmd, tag)
    if exit_code != 0:
        raise RuntimeError(f"Building image failed. Output:\n{output}")
    return ContainerImage(tag)


def _calculate_files_checksums_in_dir(root_dir: Path) -> dict:
    """
    Calculate files sha256sum in provided directory.

    Method lists all files in provided directory and calculates their checksums.
    :param root_dir: path to root directory
    :return: Dictionary with relative paths to files in dir and their checksums
    :rtype: Dict
    """
    files_checksums = {}

    for dir_, _, files in os.walk(root_dir):
        rel_dir = Path(dir_).relative_to(root_dir)
        for file_name in files:
            rel_file = rel_dir.joinpath(file_name).as_posix()
            if "-gitcommit-" in file_name:
                files_checksums[rel_file] = _get_git_commit_from_tarball(
                    root_dir.joinpath(rel_file)
                )
            elif "/sumdb/sum.golang.org/lookup/" in rel_file:
                files_checksums[rel_file] = "unstable"
            elif "/sumdb/sum.golang.org/tile/" in rel_file:
                # drop altogether - even the filenames are unstable, not just the checksums
                pass
            else:
                files_checksums[rel_file] = _calculate_sha256sum(root_dir.joinpath(rel_file))
    return files_checksums


def _get_git_commit_from_tarball(tarball: Path) -> str:
    with TarFile.open(tarball, "r:gz") as tarfile:
        extract_path = str(tarball).replace(".tar.gz", "").replace(".tgz", "")
        _safe_extract(tarfile, extract_path)

    repo = Repo(path=f"{extract_path}/app")
    commit = f"gitcommit:{repo.commit().hexsha}"

    shutil.rmtree(extract_path)

    return commit


def _calculate_sha256sum(file: Path) -> str:
    """
    Calculate sha256sum of file.

    :param file: path to file
    :return: file's sha256sum
    :rtype: str
    """
    sha256_hash = hashlib.sha256()
    with open(file, "rb") as f:
        # Read and update hash string value in blocks of 4K
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return f"sha256:{sha256_hash.hexdigest()}"


def _load_json_or_yaml(file: Path) -> dict[str, Any]:
    """Load JSON or YAML file and return dict."""
    with open(file) as f:
        return yaml.safe_load(f)


def _safe_extract(tar: TarFile, path: str = ".", *, numeric_owner: bool = False) -> None:
    """
    CVE-2007-4559 replacement for extract() or extractall().

    By using extract() or extractall() on a tarfile object without sanitizing input,
    a maliciously crafted .tar file could perform a directory path traversal attack.
    The patch essentially checks to see if all tarfile members will be
    extracted safely and throws an exception otherwise.

    :param tarfile tar: the tarfile to be extracted.
    :param str path: specifies a different directory to extract to.
    :param numeric_owner: if True, only the numbers for user/group names are used and not the names.
    :raise ExtractError: if there is a Traversal Path Attempt in the Tar File.
    """
    abs_path = Path(path).resolve()
    for member in tar.getmembers():
        member_path = Path(path).joinpath(member.name)
        abs_member_path = member_path.resolve()

        if not abs_member_path.is_relative_to(abs_path):
            raise ExtractError("Attempted Path Traversal in Tar File")

    # This 'if' block is to deal with deprectaion warning for unfiltered tar
    # extraction in 3.12.
    if sys.version_info >= (3, 12):
        tar.extractall(path, numeric_owner=numeric_owner, filter="fully_trusted")
    else:
        tar.extractall(path, numeric_owner=numeric_owner)


def _json_serialize(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _yaml_serialize(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data)


def update_test_data_if_needed(path: Path, data: dict[str, Any]) -> None:
    if path.suffix == ".json":
        serialize = _json_serialize
    elif path.suffix == ".yaml":
        serialize = _yaml_serialize
    else:
        raise ValueError(f"Don't know how to serialize data to {path.name} :(")

    if os.getenv("HERMETO_GENERATE_TEST_DATA") == "true":
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as file:
            file.write(serialize(data))


@functools.cache
def _fetch_cyclone_dx_schema() -> dict[str, Any]:
    response = requests.get(CYCLONEDX_SCHEMA_URL)
    response.raise_for_status()
    return response.json()


def fetch_deps_and_check_output(
    tmp_path: Path,
    test_case: str,
    test_params: TestParameters,
    test_repo_dir: Path,
    test_data_dir: Path,
    hermeto_image: ContainerImage,
    mounts: Sequence[tuple[StrPath, StrPath]] = (),
    podman_flags: Optional[list[str]] = None,
    fetch_output_dirname: str = DEFAULT_OUTPUT,
) -> None:
    """
    Fetch dependencies for source repo and check expected output.

    :param tmp_path: pytest fixture for temporary directory
    :param test_case: Test case name retrieved from pytest id
    :param test_params: Test case arguments
    :param test_repo_dir: Path to source repository
    :param test_data_dir: Relative path to expected output test data
    :param hermeto_image: ContainerImage instance with Hermeto image
    :param mounts: Additional volumes to be mounted to the image
    :return: None
    """
    repo = Repo(test_repo_dir)
    repo.git.reset("--hard")
    # remove untracked files and directories from the working tree
    # git will refuse to modify untracked nested git repositories unless a second -f is given
    repo.git.clean("-ffdx")
    repo.git.checkout(test_params.branch)

    output_dir = tmp_path.joinpath(fetch_output_dirname)
    cmd = [
        "fetch-deps",
        "--source",
        str(test_repo_dir),
        "--output",
        str(output_dir),
    ]
    cmd = test_params.global_flags + cmd
    cmd += test_params.flags

    cmd.append(json.dumps(test_params.packages))

    (output, exit_code) = hermeto_image.run_cmd_on_image(
        cmd,
        tmp_path,
        [*mounts, (test_repo_dir, test_repo_dir)],
        podman_flags=podman_flags,
    )
    assert exit_code == test_params.expected_exit_code, (
        f"Fetching deps ended with unexpected exitcode: {exit_code} != "
        f"{test_params.expected_exit_code}, output-cmd: {output}"
    )
    assert test_params.expected_output in str(
        output
    ), f"Expected msg {test_params.expected_output} was not found in cmd output: {output}"

    if test_params.check_output:
        build_config = _load_json_or_yaml(output_dir.joinpath(".build-config.json"))
        sbom = _load_json_or_yaml(output_dir.joinpath("bom.json"))

        if "project_files" in build_config:
            _replace_tmp_path_with_placeholder(build_config["project_files"], test_repo_dir)

        # store .build_config as yaml for more readable test data
        expected_build_config_path = test_data_dir.joinpath(test_case, ".build-config.yaml")
        expected_sbom_path = test_data_dir.joinpath(test_case, "bom.json")

        update_test_data_if_needed(expected_build_config_path, build_config)
        update_test_data_if_needed(expected_sbom_path, sbom)

        expected_build_config = _load_json_or_yaml(expected_build_config_path)
        expected_sbom = _load_json_or_yaml(expected_sbom_path)

        log.info("Compare output files")
        assert build_config == expected_build_config
        assert sbom == expected_sbom

        log.info("Validate SBOM schema")
        schema = _fetch_cyclone_dx_schema()
        jsonschema.validate(instance=sbom, schema=schema)

    deps_content_file = Path(test_data_dir, test_case, "fetch_deps_file_contents.yaml")
    if deps_content_file.exists():
        _validate_expected_dep_file_contents(deps_content_file, output_dir)

    if test_params.check_deps_checksums:
        files_checksums = _calculate_files_checksums_in_dir(output_dir.joinpath("deps"))
        expected_files_checksums_path = test_data_dir.joinpath(
            test_data_dir, test_case, "fetch_deps_sha256sums.json"
        )
        update_test_data_if_needed(expected_files_checksums_path, files_checksums)
        expected_files_checksums = _load_json_or_yaml(expected_files_checksums_path)

        log.info("Compare checksums of fetched deps files")
        assert files_checksums == expected_files_checksums


def build_image_and_check_cmd(
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    test_case: str,
    check_cmd: list,
    expected_cmd_output: str,
    hermeto_image: ContainerImage,
    podman_flags: Optional[list[str]] = None,
    fetch_output_dirname: str = DEFAULT_OUTPUT,
    env_vars_filename: str = f"{APP_NAME}.env",
) -> None:
    """
    Build image and check that Hermeto provided sources properly.

    :param tmp_path: pytest fixture for temporary directory
    :param test_repo_dir: Path to source repository
    :param test_data_dir: Relative path to expected output test data
    :param test_case: Test case name retrieved from pytest id
    :param check_cmd: Command to be run on image to check provided sources
    :param expected_cmd_output: Expected output of check_cmd
    :param hermeto_image: ContainerImage instance with Hermeto image
    :return: None
    """
    output_dir = tmp_path.joinpath(fetch_output_dirname)

    log.info(f"Creating {env_vars_filename} file")
    env_vars_file = tmp_path.joinpath(env_vars_filename)
    cmd = [
        "generate-env",
        str(output_dir),
        "--output",
        str(env_vars_file),
        "--for-output-dir",
        f"/tmp/{fetch_output_dirname}",
    ]
    (output, exit_code) = hermeto_image.run_cmd_on_image(cmd, tmp_path, podman_flags=podman_flags)
    assert exit_code == 0, f"Env var file creation failed. output-cmd: {output}"

    log.info("Injecting project files")
    cmd = [
        "inject-files",
        str(output_dir),
        "--for-output-dir",
        f"/tmp/{fetch_output_dirname}",
    ]
    (output, exit_code) = hermeto_image.run_cmd_on_image(
        cmd, tmp_path, [(test_repo_dir, test_repo_dir)]
    )
    assert exit_code == 0, f"Injecting project files failed. output-cmd: {output}"

    log.info("Build container image with all prerequisites retrieved in previous steps")
    container_folder = test_data_dir.joinpath(test_case, "container")

    with build_image_for_test_case(
        source_dir=test_repo_dir,
        output_dir=tmp_path,
        containerfile_path=container_folder.joinpath("Containerfile"),
        test_case=test_case,
    ) as test_image:

        log.info(f"Run command {check_cmd} on built image {test_image.repository}")
        (output, exit_code) = test_image.run_cmd_on_image(check_cmd, tmp_path)

        assert exit_code == 0, f"{check_cmd} command failed, Output: {output}"
        for expected_output in expected_cmd_output:
            assert expected_output in output, f"{expected_output} is missing in {output}"


def _replace_tmp_path_with_placeholder(
    project_files: list[dict[str, str]], test_repo_dir: Path
) -> None:
    for item in project_files:
        if "bundler" in item["abspath"]:
            # special case for bundler, as it is not a real project file
            item["abspath"] = "${test_case_tmp_path}/hermeto-output/bundler/config_override/config"
            continue

        # Walking up is necessary when one package manager triggers another one
        # (e.g. when dealing with Rust-based Python extensions).
        # Pathlib cannot be used since walk_up argument to relative_to
        # is available only in Python 3.12 or later.
        relative_path = os.path.relpath(item["abspath"], test_repo_dir)
        item["abspath"] = "${test_case_tmp_path}/" + str(relative_path)


def _validate_expected_dep_file_contents(dep_contents_file: Path, output_dir: Path) -> None:
    expected_deps_content = yaml.safe_load(dep_contents_file.read_text())

    for path, expected_content in expected_deps_content.items():
        log.info("Compare text content of deps/%s", path)
        dep_file = output_dir / "deps" / path
        assert dep_file.exists()
        assert dep_file.read_text() == expected_content


def retrieve_changed_files_from_git() -> tuple[Path, ...]:
    repo = Repo(".", search_parent_directories=True)
    # >>> type(repo.branches)
    # <class 'git.util.IterableList'>
    # Despite the fact stated above mypy does not believe one can use 'in' with
    # repo.branches because branches is an alias to heads and heads are decorated
    # with @property:
    #  Unsupported right operand type for in ("Callable[[], IterableList[Head]]")
    main = "main" if "main" in repo.branches else "origin/main"  # type: ignore
    try:
        files = repo.git.diff("--name-only", f"{main}..HEAD").split("\n")
    # Widest net possible in order not to interfere with testing for no good reason.
    except Exception as e:
        # If there is no main and no origin/main then someone is probably doing
        # something unusual. Implicitly falling back to retesting everything.
        msg = (
            "Detection of changed files unexpectedly failed. This either indicates "
            "that both 'main' and 'origin/main' branches are missing in a repo or "
            "a more fundamental failure. The tool will attempt to recover from this "
            f"by not filtering any tests out. This is the exception that was encountered: {e}"
        )
        log.warning(msg)
        files = PATHS_TO_CODE
    modified_files = [Path(f) for f in files]
    return tuple(modified_files)


def name_of(path: Path) -> str:
    return path.stem


def tested_object_name(path: Path) -> str:
    return path.stem.lstrip("test_")


def affects_pm(change: Path) -> bool:
    """Check if a pm is affected.

    >>> affects_pm(Path('hermeto/core/config.py'))
    False
    >>> affects_pm(Path('requirements.txt'))
    False
    >>> affects_pm(Path('tests/integration/test_gomod.py'))
    True
    >>> affects_pm(Path('tests/integration/utils.py'))
    False
    >>> affects_pm(Path('hermeto/core/package_managers/rpm/main.py'))
    True
    >>> affects_pm(Path('hermeto/core/package_managers/general.py'))
    False
    >>> affects_pm(Path('hermeto/core/package_managers/gomod.py'))
    True
    """

    def name_belongs_to_a_pm(change: Path) -> bool:
        return name_of(change) in SUPPORTED_PMS or change.parent.stem in SUPPORTED_PMS

    def affects_pm_directly(change: Path) -> bool:
        return "package_managers" in change.parts and name_belongs_to_a_pm(change)

    def affects_pm_tests(change: Path) -> bool:
        return tested_object_name(change) in SUPPORTED_PMS

    return affects_pm_directly(change) or affects_pm_tests(change)


def pm_name(pm_change: Path) -> str:
    """Extract package manager name from a known package manager-related change.

    >>> pm_name(Path('tests/integration/test_gomod.py'))
    'gomod'
    >>> pm_name(Path('hermeto/core/package_managers/rpm/main.py'))
    'rpm'
    >>> pm_name(Path('hermeto/core/package_managers/pip.py'))
    'pip'
    """
    if (name := name_of(pm_change)) in SUPPORTED_PMS:
        return name
    elif (name := tested_object_name(pm_change)) in SUPPORTED_PMS:
        return name
    else:
        return pm_change.parent.stem


def affected_package_managers(pm_changes: tuple[Path, ...]) -> set[str]:
    return set(pm_name(c) for c in pm_changes)


def is_testable_code(c: Path) -> bool:
    """Check if any actual code was affected by any of the changes.

    Does this by checking if a change is in watched subtree.

    >>> is_testable_code(Path('hermeto/core/config.py'))
    True
    >>> is_testable_code(Path('tests/integration/test_gomod.py'))
    True
    >>> is_testable_code(Path('tests/integration/utils.py'))
    True
    >>> is_testable_code(Path('tests/integration/conftest.py'))
    True
    >>> is_testable_code(Path('README.md'))
    False
    >>> is_testable_code(Path('requirements.txt'))
    True
    >>> is_testable_code(Path('pyproject.toml'))
    True
    >>> is_testable_code(Path('requirements-extras.txt'))
    True
    >>> is_testable_code(Path('Dockerfile'))
    True
    >>> is_testable_code(Path('Containerfile'))
    True
    """
    return any(c.is_relative_to(p) for p in PATHS_TO_CODE)


def select_testable_changes(changes: tuple[Path, ...]) -> tuple[Path, ...]:
    """Weed out changes that cannot be tested.

    If a change is outside of paths to testable code it is dropped as if
    it never happened. Any file or module outside of watched directories
    will be rejected.
    """
    return tuple(c for c in changes if is_testable_code(c))


def just_some_package_managers_were_affected_by(changes: tuple[Path, ...]) -> bool:
    """Check that just package managers were affected.

    If any code outside of package managers subtree was affected or if a module
    shared by package managers was affected will return False.

    >>> just_some_package_managers_were_affected_by((Path('tests/integration/test_pip.py'),))
    True
    >>> c = Path('hermeto/core/package_managers/pip.py'), Path('tests/integration/test_pip.py')
    >>> just_some_package_managers_were_affected_by(c)
    True
    >>> c = (Path('hermeto/core/package_managers/rpm/main.py'),)
    >>> just_some_package_managers_were_affected_by(c)
    True
    >>> c = Path('hermeto/core/package_managers/gomod.py'), Path('tests/integration/test_pip.py')
    >>> just_some_package_managers_were_affected_by(c)
    True
    >>> c = Path('hermeto/core/package_managers/general.py'), Path('tests/integration/test_pip.py')
    >>> just_some_package_managers_were_affected_by(c)
    False
    >>> c = (Path('hermeto/core/package_managers/general.py'),
    ...      Path('hermeto/core/package_managers/pip.py'))
    >>> just_some_package_managers_were_affected_by(c)
    False
    >>> c = Path('tests/integration/utils.py'), Path('tests/integration/test_pip.py')
    >>> just_some_package_managers_were_affected_by(c)
    False
    >>> c = Path('hermeto/core/package_managers/pip.py'), Path('tests/integration/utils.py')
    >>> just_some_package_managers_were_affected_by(c)
    False
    >>> just_some_package_managers_were_affected_by((Path('hermeto/core/utils.py'),))
    False
    >>> c = (Path('hermeto/core/package_managers/general.py'),)
    >>> just_some_package_managers_were_affected_by(c)
    False
    """
    return all(affects_pm(c) for c in changes)


def must_test_all() -> bool:
    return os.getenv("HERMETO_RUN_ALL_INTEGRATION_TESTS", "false").lower() == "true"


def determine_integration_tests_to_skip() -> Any:
    """Check which tests to run basing on which files were changed in a commit."""
    if must_test_all():
        return set()
    changes = select_testable_changes(retrieve_changed_files_from_git())
    if len(changes) == 0:
        return SUPPORTED_PMS
    elif just_some_package_managers_were_affected_by(changes):
        return SUPPORTED_PMS - affected_package_managers(changes)
    return set()
