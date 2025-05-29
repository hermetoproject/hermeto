import itertools
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from hermeto import APP_NAME
from hermeto.core.errors import PackageManagerError
from hermeto.core.models.input import Request
from hermeto.core.models.output import BuildConfig, EnvironmentVariable, RequestOutput
from hermeto.core.models.sbom import Component
from hermeto.core.package_managers.yarn_classic.main import (
    MIRROR_DIR,
    YARN_NETWORK_TIMEOUT_MILLISECONDS,
    _fetch_dependencies,
    _generate_build_environment_variables,
    _get_prefetch_environment_variables,
    _resolve_yarn_project,
    _run_yarn_install,
    _verify_corepack_yarn_version,
    _verify_no_offline_mirror_collisions,
    fetch_yarn_source,
)
from hermeto.core.package_managers.yarn_classic.project import Project
from hermeto.core.package_managers.yarn_classic.resolver import (
    FilePackage,
    LinkPackage,
    RegistryPackage,
    UrlPackage,
    YarnClassicPackage,
)
from hermeto.core.rooted_path import RootedPath


def _prepare_project(source_dir: RootedPath, package_json: dict[str, Any]) -> Project:
    package_json_path = source_dir.join_within_root("package.json")
    with open(package_json_path.path, "w") as f:
        json.dump(package_json, f)

    return Project.from_source_dir(source_dir)


@pytest.fixture(scope="module")
def yarn_classic_env_variables() -> list[EnvironmentVariable]:
    return [
        EnvironmentVariable(
            name="YARN_YARN_OFFLINE_MIRROR", value="${output_dir}/deps/yarn-classic"
        ),
        EnvironmentVariable(name="YARN_YARN_OFFLINE_MIRROR_PRUNING", value="false"),
    ]


def test_generate_build_environment_variables(
    yarn_classic_env_variables: list[EnvironmentVariable],
) -> None:
    result = _generate_build_environment_variables()
    assert result == yarn_classic_env_variables


@pytest.mark.parametrize(
    "input_request, package_components",
    (
        pytest.param(
            [{"type": "yarn", "path": "."}],
            [
                [
                    Component(
                        name="foo",
                        purl="pkg:npm/foo@1.0.0",
                        version="1.0.0",
                    ),
                    Component(name="bar", purl="pkg:npm/bar@2.0.0", version="2.0.0"),
                ],
            ],
            id="single_input_package",
        ),
        pytest.param(
            [{"type": "yarn", "path": "."}, {"type": "yarn", "path": "./path"}],
            [
                [
                    Component(
                        name="foo",
                        purl="pkg:npm/foo@1.0.0",
                        version="1.0.0",
                    ),
                ],
                [
                    Component(
                        name="bar",
                        purl="pkg:npm/bar@2.0.0",
                        version="2.0.0",
                    ),
                    Component(
                        name="baz",
                        purl="pkg:npm/baz@3.0.0",
                        version="3.0.0",
                    ),
                ],
            ],
            id="multiple_input_packages",
        ),
    ),
    indirect=["input_request"],
)
@mock.patch("hermeto.core.package_managers.yarn_classic.main._verify_repository")
@mock.patch("hermeto.core.package_managers.yarn_classic.main._resolve_yarn_project")
@mock.patch("hermeto.core.package_managers.yarn_classic.main.Project.from_source_dir")
def test_fetch_yarn_source(
    mock_create_project: mock.Mock,
    mock_resolve_yarn: mock.Mock,
    mock_verify_repository: mock.Mock,
    input_request: Request,
    package_components: list[Component],
    yarn_classic_env_variables: list[EnvironmentVariable],
) -> None:
    package_dirs = [
        input_request.source_dir.join_within_root(p.path) for p in input_request.packages
    ]
    projects = [_prepare_project(path, {}) for path in package_dirs]

    mock_create_project.side_effect = projects
    mock_resolve_yarn.side_effect = package_components

    output = fetch_yarn_source(input_request)

    mock_create_project.assert_has_calls([mock.call(path) for path in package_dirs])
    mock_resolve_yarn.assert_has_calls([mock.call(p, input_request.output_dir) for p in projects])
    mock_verify_repository.assert_has_calls([mock.call(p) for p in projects])

    expected_output = RequestOutput(
        components=list(itertools.chain.from_iterable(package_components)),
        build_config=BuildConfig(environment_variables=yarn_classic_env_variables),
    )
    assert output == expected_output
    assert input_request.output_dir.join_within_root(MIRROR_DIR).path.exists()


@mock.patch("hermeto.core.package_managers.yarn_classic.main.resolve_packages")
@mock.patch("hermeto.core.package_managers.yarn_classic.main._verify_corepack_yarn_version")
@mock.patch("hermeto.core.package_managers.yarn_classic.main._get_prefetch_environment_variables")
@mock.patch("hermeto.core.package_managers.yarn_classic.main._fetch_dependencies")
def test_resolve_yarn_project(
    mock_fetch_dependencies: mock.Mock,
    mock_prefetch_env_vars: mock.Mock,
    mock_verify_yarn_version: mock.Mock,
    mock_resolve_packages: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    project = _prepare_project(rooted_tmp_path, {})
    output_dir = rooted_tmp_path.join_within_root("output")

    _resolve_yarn_project(project, output_dir)

    mock_verify_yarn_version.assert_called_once_with(
        project.source_dir, mock_prefetch_env_vars.return_value
    )
    mock_fetch_dependencies.assert_called_once_with(project.source_dir, output_dir)
    mock_resolve_packages.assert_called_once_with(project, output_dir.join_within_root(MIRROR_DIR))


@pytest.mark.parametrize(
    "kwargs,expected_extra_flags",
    [
        ({}, []),
        (
            {"frozen_lockfile": True},
            ["--frozen-lockfile"],
        ),
        (
            {"skip_integrity": True},
            ["--skip-integrity-check"],
        ),
        (
            {"offline": True},
            ["--offline"],
        ),
        (
            {"frozen_lockfile": True, "skip_integrity": True, "offline": True},
            ["--frozen-lockfile", "--skip-integrity-check", "--offline"],
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.yarn_classic.main.run_yarn_cmd")
def test_run_yarn_install(
    mock_run_yarn_cmd: mock.Mock,
    kwargs: dict[str, bool],
    expected_extra_flags: list[str],
    rooted_tmp_path: RootedPath,
) -> None:
    env = {"foo": "bar"}
    base_cmd = [
        "install",
        "--disable-pnp",
        "--ignore-engines",
        "--no-default-rc",
        "--non-interactive",
    ]
    expected_cmd = base_cmd + expected_extra_flags

    _run_yarn_install(rooted_tmp_path, env, **kwargs)

    mock_run_yarn_cmd.assert_called_with(expected_cmd, rooted_tmp_path, env)
    assert mock_run_yarn_cmd.call_count == 1


@mock.patch("hermeto.core.package_managers.yarn_classic.main._run_yarn_install")
@mock.patch("hermeto.core.package_managers.yarn_classic.main._get_prefetch_environment_variables")
def test_fetch_dependencies(
    mock_prefetch_env: mock.Mock, mock_yarn_install: mock.Mock, tmp_path: Path
) -> None:
    source_dir = RootedPath(tmp_path / "source")
    output_dir = RootedPath(tmp_path / "output")

    first_install_env = {"foo": "bar"}
    mock_prefetch_env.return_value = first_install_env

    second_install_env = dict(first_install_env)
    second_install_env["YARN_YARN_OFFLINE_MIRROR"] = str(output_dir.join_within_root(MIRROR_DIR))
    second_install_env["YARN_YARN_OFFLINE_MIRROR_PRUNING"] = "false"

    expected_yarn_install_calls = [
        mock.call(source_dir, mock_prefetch_env.return_value, frozen_lockfile=True),
        mock.call(source_dir, second_install_env, skip_integrity=True, offline=True),
    ]

    _fetch_dependencies(source_dir, output_dir)

    mock_prefetch_env.assert_called_once()
    mock_yarn_install.assert_has_calls(expected_yarn_install_calls)
    assert mock_yarn_install.call_count == 2


def test_get_prefetch_environment_variables() -> None:
    expected_output = {
        "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
        "COREPACK_ENABLE_PROJECT_SPEC": "0",
        "YARN_IGNORE_PATH": "true",
        "YARN_IGNORE_SCRIPTS": "true",
        "YARN_NETWORK_TIMEOUT": f"{YARN_NETWORK_TIMEOUT_MILLISECONDS}",
    }

    output = _get_prefetch_environment_variables()

    assert output == expected_output


@pytest.mark.parametrize(
    "yarn_version_output",
    [
        pytest.param("1.22.0", id="valid_version"),
        pytest.param("1.22.0\n", id="valid_version_with_whitespace"),
    ],
)
@mock.patch("hermeto.core.package_managers.yarn.utils.run_yarn_cmd")
def test_verify_corepack_yarn_version(
    mock_run_yarn_cmd: mock.Mock, yarn_version_output: str, tmp_path: Path
) -> None:
    rooted_tmp_path = RootedPath(tmp_path)
    env = {"foo": "bar"}
    mock_run_yarn_cmd.return_value = yarn_version_output

    _verify_corepack_yarn_version(RootedPath(tmp_path), env)
    mock_run_yarn_cmd.assert_called_once_with(["--version"], rooted_tmp_path, env=env)


@pytest.mark.parametrize(
    "yarn_version_output",
    [
        pytest.param("1.21.0", id="version_too_low"),
        pytest.param("2.0.0", id="version_too_high"),
    ],
)
@mock.patch("hermeto.core.package_managers.yarn.utils.run_yarn_cmd")
def test_verify_corepack_yarn_version_disallowed_version(
    mock_run_yarn_cmd: mock.Mock, yarn_version_output: str, tmp_path: Path
) -> None:
    mock_run_yarn_cmd.return_value = yarn_version_output
    error_message = (
        f"{APP_NAME} expected corepack to install yarn >=1.22.0,<2.0.0, but "
        f"instead found yarn@{yarn_version_output}"
    )

    with pytest.raises(PackageManagerError, match=error_message):
        _verify_corepack_yarn_version(RootedPath(tmp_path), env={"foo": "bar"})


@mock.patch("hermeto.core.package_managers.yarn.utils.run_yarn_cmd")
def test_verify_corepack_yarn_version_invalid_version(
    mock_run_yarn_cmd: mock.Mock, tmp_path: Path
) -> None:
    mock_run_yarn_cmd.return_value = "foobar"
    error_message = "The command `yarn --version` did not return a valid semver."

    with pytest.raises(PackageManagerError, match=error_message):
        _verify_corepack_yarn_version(RootedPath(tmp_path), env={"foo": "bar"})


@pytest.mark.parametrize(
    "packages",
    [
        pytest.param(
            [
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/same/-/same-1.0.0.tgz",
                ),
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/same/-/same-1.0.0.tgz",
                ),
            ],
            id="same_registry_packages",
        ),
        pytest.param(
            [
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/@colors/colors/-/colors-1.6.0.tgz",
                ),
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/@colors/colors/-/colors-1.6.0.tgz",
                ),
            ],
            id="same_scoped_registry_packages",
        ),
        pytest.param(
            [
                LinkPackage(name="foo", version="1.0.0", path=RootedPath("/path/to/foo")),
                FilePackage(name="bar", version="1.0.0", path=RootedPath("/path/to/bar")),
            ],
            id="skipped_packages",
        ),
    ],
)
def test_verify_offline_mirror_collisions_pass(packages: Iterable[YarnClassicPackage]) -> None:
    _verify_no_offline_mirror_collisions(packages)


@pytest.mark.parametrize(
    "packages",
    [
        pytest.param(
            [
                RegistryPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://registry.yarnpkg.com/same/-/same-1.0.0.tgz",
                ),
                UrlPackage(
                    name="bar",
                    version="1.0.0",
                    url="https://mirror.example.com/same-1.0.0.tgz",
                ),
            ],
            id="registry_and_url_package_conflict",
        ),
        pytest.param(
            [
                UrlPackage(
                    name="foo",
                    version="1.0.0",
                    url="https://mirror.example.com/same-1.0.0.tgz",
                ),
                UrlPackage(
                    name="bar",
                    version="1.0.0",
                    url="https://mirror.example.com/same-1.0.0.tgz",
                ),
            ],
            id="url_and_url_package_conflict",
        ),
    ],
)
def test_verify_offline_mirror_collisions_fail(packages: Iterable[YarnClassicPackage]) -> None:
    with pytest.raises(PackageManagerError):
        _verify_no_offline_mirror_collisions(packages)
