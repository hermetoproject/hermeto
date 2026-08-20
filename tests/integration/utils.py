# SPDX-License-Identifier: GPL-3.0-or-later
import functools
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import requests
import yaml

from hermeto import APP_NAME
from hermeto.core.errors import ExitError
from hermeto.core.scm import GitRepo
from hermeto.core.type_aliases import StrPath
from hermeto.core.utils import GIT_PRISTINE_ENV
from hermeto.interface.cli import DEFAULT_OUTPUT
from tests.integration.container_engine import get_container_engine
from tests.integration.proxy import (
    DEFAULT_LOCAL_NEXUS_PROXY_ENV,
    is_local_nexus_proxy_enabled,
    parse_proxy_env,
    validate_and_strip_proxy_refs,
)

# force IPv4 localhost as 'localhost' can resolve with IPv6 as well
TEST_SERVER_LOCALHOST = "127.0.0.1"

HERMETO_TEST_IMAGE_TAG = "localhost/hermeto-test:latest"


log = logging.getLogger(__name__)
container_engine = get_container_engine()


@dataclass
class SyntheticSubmoduleSpec:
    """Specification for a submodule to embed inside a synthetic parent repo."""

    source_dir: Path
    path: str


class SyntheticRepo:
    """A deterministic synthetic git repo created from scenario source files.

    All git metadata (author, date, commit message) is fixed so that identical
    source files always produce the same commit SHA.
    """

    _GIT_ENV = {
        **GIT_PRISTINE_ENV,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "protocol.file.allow",
        "GIT_CONFIG_VALUE_0": "always",
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "1970-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "1970-01-01T00:00:00+00:00",
    }

    def __init__(
        self,
        repo_path: Path,
        origin_url: str,
        submodules: Sequence[SyntheticSubmoduleSpec] = (),
    ) -> None:
        # Over time the test scenarios directories may accumulate some git untracked local-only
        # build artifacts, e.g. __pycache__, which, if unfiltered and then committed to the
        # synthetic repo would yield a different digest every time breaking the test suite
        # constantly.
        # Therefore, copy hermeto's root .gitignore into the synthetic repo as it already contains a
        # good set of excludes. We copy the .gitignore file to .git/info/exclude instead of plain
        # .gitignore because it would get committed automatically by the code below, we don't need
        # nor want to commit more than the test scenario data in the synthetic repo
        project_repo_root = GitRepo(Path(__file__), search_parent_directories=True).working_dir
        gitignore = Path(project_repo_root) / ".gitignore"
        if gitignore.is_file():
            exclude_file = repo_path / ".git" / "info" / "exclude"
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(gitignore, exclude_file)

        # main repo creation
        self.repo = GitRepo.init(repo_path, env=GIT_PRISTINE_ENV)
        with self.repo.git.custom_environment(**self._GIT_ENV):
            self.repo.git.add(".")
            self.repo.git.commit(m="test scenario")
        self.repo.create_remote("origin", origin_url)
        self.path = repo_path

        # adding submodules
        for sub in submodules:
            child_path = repo_path.parent / f"submodule-{sub.path}"
            shutil.copytree(sub.source_dir, child_path)

            # child repos use parent's origin_url rather than their own (URL is meaningless in tests)
            child = SyntheticRepo(child_path, origin_url)
            with self.repo.git.custom_environment(**self._GIT_ENV):
                self.repo.git.submodule("add", str(child.path), sub.path)

                # .gitmodules and the cloned submodule both record the local
                # tmp path which changes per run; replace with origin_url so
                # the parent commit is deterministic and hermeto can
                # canonicalize the submodule's origin
                sub_repo = GitRepo(repo_path / sub.path)
                sub_repo.remotes.origin.set_url(origin_url)

                self.repo.git.config(f"submodule.{sub.path}.url", origin_url, file=".gitmodules")
                self.repo.git.add(".")
                self.repo.git.commit(m=f"add submodule {sub.path}")


def _default_hermeto_env() -> dict[str, str]:
    """Return default Hermeto env vars for the test session, if any are enabled."""
    if is_local_nexus_proxy_enabled():
        return dict(DEFAULT_LOCAL_NEXUS_PROXY_ENV)
    return {}


def _resolve_hermeto_env(
    run_defaults: Mapping[str, str] | None = None,
    test_overrides: Mapping[str, str] | None = None,
    call_overrides: Mapping[str, str] | None = None,
    unset_hermeto_env: Collection[str] = (),
) -> dict[str, str]:
    """Resolve effective Hermeto env for one test invocation."""
    resolved = {
        **(run_defaults or {}),
        **(test_overrides or {}),
        **(call_overrides or {}),
    }
    return {k: v for k, v in resolved.items() if k not in unset_hermeto_env}


def _env_to_engine_flags(env: Mapping[str, str] | None) -> list[str]:
    """Convert env var dict to container engine ``-e`` flags."""
    if env is None:
        return []

    return [flag for name, value in env.items() for flag in ("-e", f"{name}={value}")]


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


CYCLONEDX_SCHEMA_URL = "https://raw.githubusercontent.com/CycloneDX/specification/refs/heads/master/schema/bom-1.6.schema.json"


@dataclass
class TestParameters:
    packages: tuple[dict[str, Any], ...]
    check_output: bool = True
    expected_error: ExitError = ExitError.ERR_OK
    expected_output: str = ""
    global_flags: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    hermeto_env: dict[str, str] = field(default_factory=dict)
    unset_hermeto_env: set[str] = field(default_factory=set)
    netrc_content: str | None = None
    containerfile: str = "Containerfile"


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
        net: str | None = None,
        entrypoint: str | None = None,
        podman_flags: Sequence[str] | None = None,
    ) -> tuple[str, int]:
        podman_flags = [] if podman_flags is None else list(podman_flags)
        podman_flags.extend(["-v", f"{tmp_path}:{tmp_path}:z"])

        for src, dest in mounts:
            podman_flags.extend(["-v", f"{src}:{dest}:z"])
        if net:
            podman_flags.append(f"--net={net}")

        return container_engine.run(self.repository, cmd, entrypoint, podman_flags)

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
        net: str | None = "host",
        entrypoint: str | None = None,
        podman_flags: Sequence[str] | None = None,
        netrc_content: str | None = None,
    ) -> tuple[str, int]:
        if netrc_content:
            with tempfile.TemporaryDirectory() as netrc_tmpdir:
                netrc_path = Path(netrc_tmpdir, ".netrc")
                netrc_path.write_text(netrc_content)
                return super().run_cmd_on_image(
                    cmd,
                    tmp_path,
                    [*mounts, (netrc_path, "/root/.netrc")],
                    net,
                    entrypoint,
                    podman_flags,
                )
        return super().run_cmd_on_image(cmd, tmp_path, mounts, net, entrypoint, podman_flags)


def build_image(context_dir: Path, tag: str) -> ContainerImage:
    return _build_image(flags=[], tag=tag, context_dir=context_dir)


def build_hermeto_test_image(base_image: str) -> None:
    """Build a derived hermeto image for integration tests."""
    cert_dir = Path(__file__).parents[1] / "certificates"
    containerfile = Path(__file__).parent / "Containerfile.test"
    _build_image(
        flags=["-f", str(containerfile), "--build-arg", f"HERMETO_BASE_IMAGE={base_image}"],
        tag=HERMETO_TEST_IMAGE_TAG,
        context_dir=cert_dir,
    )


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

    flags = [
        "-f",
        str(containerfile_path),
        "-v",
        f"{source_dir}:{source_dir_mount_point}:z",  # SELinux shared mount
        "-v",
        f"{output_dir}:{output_dir_mount_point}:Z",  # SELinux exclusive mount
        "--no-cache",
        "--network",
        "none",
    ]

    # extra build args
    for build_arg in ("CARGO_BUILD_JOBS",):
        if val := os.environ.get(build_arg):
            flags.extend(["--build-arg", f"{build_arg}={val}"])

    # this should be extended to support more archs when we have the means of testing it in our CI
    rpm_repos_path = f"{output_dir}/hermeto-output/deps/rpm/x86_64/repos.d"
    if Path(rpm_repos_path).exists():
        flags.extend(
            [
                "-v",
                f"{rpm_repos_path}:/etc/yum.repos.d:Z",
            ]
        )

    return _build_image(flags, tag=f"localhost/{test_case}")


def _build_image(flags: list[str], tag: str, context_dir: StrPath = ".") -> ContainerImage:
    (output, exit_code) = container_engine.build(context_dir, [*flags, "--tag", tag])
    if exit_code != 0:
        raise RuntimeError(f"Building image failed. Output:\n{output}")
    return ContainerImage(tag)


def _load_json_or_yaml(file: Path) -> dict[str, Any]:
    """Load JSON or YAML file and return dict."""
    with open(file) as f:
        return yaml.safe_load(f)


def _json_serialize(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _yaml_serialize(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data)


def _sort_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sort_obj(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return sorted((_sort_obj(v) for v in obj), key=str)
    return obj


def update_test_data_if_needed(path: Path, data: dict[str, Any]) -> None:
    if path.suffix == ".json":
        serialize = _json_serialize
    elif path.suffix == ".yaml":
        serialize = _yaml_serialize
    else:
        raise ValueError(f"Don't know how to serialize data to {path.name} :(")

    if os.getenv("HERMETO_TEST_GENERATE_DATA") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as file:
            file.write(serialize(data))


@functools.cache
def _fetch_cyclone_dx_schema() -> dict[str, Any]:
    response = requests.get(CYCLONEDX_SCHEMA_URL)
    response.raise_for_status()
    return response.json()


def create_synthetic_repo(
    tmp_path: Path,
    source_dir: Path,
    *,
    origin_url: str = "https://github.com/hermetoproject/hermeto.git",
    submodules: Sequence[SyntheticSubmoduleSpec] = (),
) -> Path:
    """Create a deterministic synthetic git repo from scenario source files."""
    synthetic_repo_path = tmp_path / "repo"
    shutil.copytree(source_dir, synthetic_repo_path)
    repo = SyntheticRepo(synthetic_repo_path, origin_url, submodules)
    return repo.path


def fetch_deps_and_check_output(
    tmp_path: Path,
    test_case: str,
    test_params: TestParameters,
    test_repo_dir: Path,
    test_data_dir: Path,
    hermeto_image: HermetoImage,
    mounts: Sequence[tuple[StrPath, StrPath]] = (),
    entrypoint: str | None = None,
    podman_flags: list[str] | None = None,
    hermeto_env_overrides: dict[str, str] | None = None,
    fetch_output_dirname: str = DEFAULT_OUTPUT,
) -> Path:
    """
    Fetch dependencies for source repo and check expected output.

    :param tmp_path: pytest fixture for temporary directory
    :param test_case: Test case name retrieved from pytest id
    :param test_params: Test case arguments
    :param test_repo_dir: Path to source repository
    :param test_data_dir: Relative path to expected output test data
    :param hermeto_image: ContainerImage instance with Hermeto image
    :param mounts: Additional volumes to be mounted to the image
    :param entrypoint: Entrypoint to be used for the image
    :param podman_flags: Additional flags to be passed to podman
    :param hermeto_env_overrides: Highest-precedence env var overrides for this call
    :param fetch_output_dirname: Name of the directory where the fetch output is stored
    :return: Path to the repository directory used (for passing to build_image_and_check_cmd)
    """
    actual_repo_dir = test_repo_dir

    output_dir = tmp_path.joinpath(fetch_output_dirname)
    cmd = [
        "fetch-deps",
        "--source",
        str(actual_repo_dir),
        "--output",
        str(output_dir),
    ]
    cmd = test_params.global_flags + cmd
    cmd += test_params.flags

    cmd.append(json.dumps(test_params.packages))

    merged_env = _resolve_hermeto_env(
        run_defaults=_default_hermeto_env(),
        test_overrides=test_params.hermeto_env,
        call_overrides=hermeto_env_overrides,
        unset_hermeto_env=test_params.unset_hermeto_env,
    )
    if merged_env:
        log.info("Injecting Hermeto env vars: %s", merged_env)

    (output, exit_code) = hermeto_image.run_cmd_on_image(
        cmd,
        tmp_path,
        [*mounts, (actual_repo_dir, actual_repo_dir)],
        entrypoint=entrypoint,
        podman_flags=(podman_flags or []) + _env_to_engine_flags(merged_env),
        netrc_content=test_params.netrc_content,
    )

    def _fmt(code: int) -> str:
        if code == 0:
            return "0"
        try:
            return f"{code} ({ExitError(code).name})"
        except ValueError:
            return f"{code} (unknown exit code)"

    assert exit_code == test_params.expected_error.value, (
        f"Fetching deps ended with unexpected exitcode: {_fmt(exit_code)}"
        f" != {_fmt(test_params.expected_error.value)}, output-cmd: {output}"
    )
    if test_params.expected_output:
        assert test_params.expected_output in str(output), (
            f"Expected msg {test_params.expected_output} was not found in cmd output: {output}"
        )

    if test_params.check_output:
        build_config = _load_json_or_yaml(output_dir.joinpath(".build-config.json"))
        sbom = _replace_timestamps(_load_json_or_yaml(output_dir.joinpath("bom.json")))

        if "project_files" in build_config:
            _replace_tmp_path_with_placeholder(build_config["project_files"], actual_repo_dir)

        # store .build_config as yaml for more readable test data
        expected_build_config_path = test_data_dir.joinpath(test_case, "out", ".build-config.yaml")
        expected_sbom_path = test_data_dir.joinpath(test_case, "out", "bom.json")

        # If any proxy backends are configured, validate and strip proxy refs from the SBOM
        # before comparing to test data.
        sbom_for_comparison = sbom
        backend_proxy_urls = parse_proxy_env(merged_env)
        if backend_proxy_urls:
            log.info("Validating and stripping proxy metadata from SBOM")
            sbom_for_comparison = validate_and_strip_proxy_refs(sbom, backend_proxy_urls)

        update_test_data_if_needed(expected_build_config_path, build_config)
        update_test_data_if_needed(expected_sbom_path, sbom_for_comparison)

        expected_build_config = _load_json_or_yaml(expected_build_config_path)
        expected_sbom = _replace_timestamps(_load_json_or_yaml(expected_sbom_path))

        log.info("Compare output files")
        assert build_config == expected_build_config
        assert _sort_obj(sbom_for_comparison) == _sort_obj(expected_sbom)

        log.info("Validate SBOM schema")
        schema = _fetch_cyclone_dx_schema()
        jsonschema.validate(instance=sbom, schema=schema)

    deps_content_file = Path(test_data_dir, test_case, "out", "fetch_deps_file_contents.yaml")
    if deps_content_file.exists():
        _validate_expected_dep_file_contents(deps_content_file, output_dir)

    return actual_repo_dir


def build_image_and_check_cmd(
    tmp_path: Path,
    test_repo_dir: Path,
    test_data_dir: Path,
    test_case: str,
    test_params: TestParameters,
    check_cmd: list,
    expected_cmd_output: str,
    hermeto_image: HermetoImage,
    hermeto_image_entrypoint: str | None = None,
    fetch_output_dirname: str = DEFAULT_OUTPUT,
    env_vars_filename: str = f"{APP_NAME}.env",
) -> None:
    """
    Build image and check that Hermeto provided sources properly.

    :param tmp_path: pytest fixture for temporary directory
    :param test_repo_dir: Path to source repository
    :param test_data_dir: Relative path to expected output test data
    :param test_case: Test case name retrieved from pytest id
    :param test_params: Test case arguments
    :param check_cmd: Command to be run on image to check provided sources
    :param expected_cmd_output: Expected output of check_cmd
    :param hermeto_image: ContainerImage instance with Hermeto image
    :param hermeto_image_entrypoint: Entrypoint to be used for the hermeto image
    :param fetch_output_dirname: Name of the directory where the fetch output is stored
    :param env_vars_filename: Name of the file where the environment variables are stored
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
    (output, exit_code) = hermeto_image.run_cmd_on_image(
        cmd,
        tmp_path,
        entrypoint=hermeto_image_entrypoint,
    )
    assert exit_code == 0, f"Env var file creation failed. output-cmd: {output}"

    log.info("Injecting project files")
    cmd = [
        "inject-files",
        str(output_dir),
        "--for-output-dir",
        f"/tmp/{fetch_output_dirname}",
    ]
    (output, exit_code) = hermeto_image.run_cmd_on_image(
        cmd,
        tmp_path,
        mounts=[(test_repo_dir, test_repo_dir)],
        entrypoint=hermeto_image_entrypoint,
    )
    assert exit_code == 0, f"Injecting project files failed. output-cmd: {output}"

    log.info("Build container image with all prerequisites retrieved in previous steps")
    containerfile_path = test_data_dir.joinpath(test_case, "in", test_params.containerfile)

    with build_image_for_test_case(
        source_dir=test_repo_dir,
        output_dir=tmp_path,
        containerfile_path=containerfile_path,
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
        # bundler config file is created in the output directory
        if "bundler" in item["abspath"]:
            item["abspath"] = "${test_case_tmp_path}/hermeto-output/bundler/config_override/config"
            continue
        # maven settings.xml file is created in the output directory
        elif "settings.xml" in item["abspath"]:
            item["abspath"] = "${test_case_tmp_path}/hermeto-output/settings.xml"
            continue

        # Walking up is necessary when one package manager triggers another one
        # (e.g. when dealing with Rust-based Python extensions).
        # Pathlib cannot be used since walk_up argument to relative_to
        # is available only in Python 3.12 or later.
        relative_path = os.path.relpath(item["abspath"], test_repo_dir)
        item["abspath"] = "${test_case_tmp_path}/" + str(relative_path)


def _replace_timestamps(json_obj: Any) -> Any:
    """
    Recursively replace all "timestamp" values with a fixed timestamp.
    This ensures deterministic test output in the SBOM.
    """
    if isinstance(json_obj, dict):
        return {
            key: "2025-01-01T00:00:00Z" if key == "timestamp" else _replace_timestamps(value)
            for key, value in json_obj.items()
        }

    if isinstance(json_obj, list):
        return [_replace_timestamps(item) for item in json_obj]

    return json_obj


def _validate_expected_dep_file_contents(dep_contents_file: Path, output_dir: Path) -> None:
    expected_deps_content = yaml.safe_load(dep_contents_file.read_text())

    for path, expected_content in expected_deps_content.items():
        log.info("Compare text content of deps/%s", path)
        dep_file = output_dir / "deps" / path
        assert dep_file.exists()
        assert dep_file.read_text() == expected_content
