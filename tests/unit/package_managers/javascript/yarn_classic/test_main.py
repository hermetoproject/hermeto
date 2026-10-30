# SPDX-License-Identifier: GPL-3.0-only
import json
from collections.abc import Iterable
from pathlib import Path
from unittest import mock

import pytest

from hermeto.core.errors import PackageManagerError, PackageRejected, UnexpectedFormat
from hermeto.core.package_managers.javascript.yarn_classic.main import (
    _fetch_dependencies,
    _install_and_verify_yarn,
    _resolve_classic_version,
    _verify_no_offline_mirror_collisions,
)
from hermeto.core.package_managers.javascript.yarn_classic.project import Project
from hermeto.core.package_managers.javascript.yarn_classic.resolver import (
    FilePackage,
    LinkPackage,
    RegistryPackage,
    UrlPackage,
    YarnClassicPackage,
)
from hermeto.core.rooted_path import RootedPath


@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_cmd")
def test_fetch_dependencies_uses_selected_yarn_for_both_installs(
    mock_run_cmd: mock.Mock, rooted_tmp_path: RootedPath
) -> None:
    yarn_bin = Path("/cache/yarn/1.22.22/bin/yarn.js")

    _fetch_dependencies(
        rooted_tmp_path, rooted_tmp_path.join_within_root("output"), yarn_bin=yarn_bin
    )

    assert mock_run_cmd.call_count == 2
    for call in mock_run_cmd.call_args_list:
        assert call.kwargs["cmd"][:3] == ["node", str(yarn_bin), "install"]
    assert "--frozen-lockfile" in mock_run_cmd.call_args_list[0].kwargs["cmd"]
    assert "--offline" in mock_run_cmd.call_args_list[1].kwargs["cmd"]


@pytest.mark.parametrize("package_manager", ["npm@10.0.0", "yarn@^1.22.0", ""])
@mock.patch("hermeto.core.package_managers.javascript.yarn_classic.main.install_yarn")
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_cmd")
def test_invalid_classic_spec_is_rejected_before_installation(
    mock_run_cmd: mock.Mock,
    mock_install: mock.Mock,
    rooted_tmp_path: RootedPath,
    package_manager: str,
) -> None:
    project = mock.Mock(
        source_dir=rooted_tmp_path, package_json={"packageManager": package_manager}
    )

    with pytest.raises(UnexpectedFormat):
        _install_and_verify_yarn(project)

    mock_install.assert_not_called()
    mock_run_cmd.assert_not_called()


@pytest.mark.parametrize(
    "package_manager",
    [
        pytest.param(None, id="null"),
        pytest.param(False, id="boolean"),
        pytest.param(0, id="zero"),
        pytest.param(123, id="integer"),
        pytest.param([], id="empty-array"),
        pytest.param({}, id="empty-object"),
        pytest.param({"version": "1.22.22"}, id="object"),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn_classic.main.install_yarn")
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_cmd")
def test_invalid_classic_package_manager_type_is_rejected_before_installation(
    mock_run_cmd: mock.Mock,
    mock_install: mock.Mock,
    rooted_tmp_path: RootedPath,
    package_manager: object,
) -> None:
    rooted_tmp_path.join_within_root("package.json").path.write_text(
        json.dumps({"packageManager": package_manager}) + "\n"
    )
    project = Project.from_source_dir(rooted_tmp_path)
    mock_install.return_value = Path("/cache/yarn/1.22.22/bin/yarn.js")
    mock_run_cmd.return_value = "1.22.22\n"

    with pytest.raises(UnexpectedFormat):
        _install_and_verify_yarn(project)

    mock_install.assert_not_called()
    mock_run_cmd.assert_not_called()


@pytest.mark.parametrize(
    "package_manager, version",
    [(None, "1.22.22"), ("yarn@1.22.19", "1.22.19")],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_cmd")
@mock.patch("hermeto.core.package_managers.javascript.yarn_classic.main.install_yarn")
def test_classic_version_check_uses_selected_executable(
    mock_install: mock.Mock,
    mock_run_cmd: mock.Mock,
    rooted_tmp_path: RootedPath,
    package_manager: str | None,
    version: str,
) -> None:
    project = mock.Mock(
        source_dir=rooted_tmp_path,
        package_json={} if package_manager is None else {"packageManager": package_manager},
    )
    yarn_bin = rooted_tmp_path.path / "cache with spaces" / "bin" / "yarn.js"
    mock_install.return_value = yarn_bin
    mock_run_cmd.return_value = version + "\n"

    assert _install_and_verify_yarn(project) == yarn_bin

    assert str(mock_install.call_args.args[0]) == version
    mock_run_cmd.assert_called_once_with(
        cmd=["node", str(yarn_bin), "--version"],
        params={"cwd": rooted_tmp_path, "env": mock.ANY},
    )
    env = mock_run_cmd.call_args.kwargs["params"]["env"]
    assert env["YARN_IGNORE_PATH"] == "true"
    assert env["YARN_IGNORE_SCRIPTS"] == "true"


def test_resolve_classic_version_preserves_checksum() -> None:
    spec = "yarn@1.22.22+sha1." + "a" * 40

    version = _resolve_classic_version(spec)

    assert str(version) == spec.removeprefix("yarn@")


@pytest.mark.parametrize(
    "yarn_version_output",
    [
        pytest.param("1.21.0", id="disallowed_version_too_low"),
        pytest.param("2.0.0", id="disallowed_version_too_high"),
        pytest.param("foobar", id="invalid_version"),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn_classic.main.install_yarn")
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_yarn_cmd")
def test_install_and_verify_yarn_fail(
    mock_run_yarn_cmd: mock.Mock,
    mock_install_yarn: mock.Mock,
    yarn_version_output: str,
    tmp_path: Path,
) -> None:
    mock_install_yarn.return_value = Path("/fake/yarn.js")
    mock_run_yarn_cmd.return_value = yarn_version_output

    project = mock.Mock()
    project.source_dir = RootedPath(tmp_path)
    project.package_json = {}

    with pytest.raises(PackageManagerError):
        _install_and_verify_yarn(project)


@pytest.mark.parametrize("version", ["1.21.0", "1.22.0-rc.1", "2.0.0-rc.1", "2.0.0", "3.6.2"])
@mock.patch("hermeto.core.package_managers.javascript.yarn_classic.main.install_yarn")
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_yarn_cmd")
def test_unsupported_classic_version_is_rejected_before_installation(
    mock_run_yarn_cmd: mock.Mock,
    mock_install_yarn: mock.Mock,
    rooted_tmp_path: RootedPath,
    version: str,
) -> None:
    project = mock.Mock(
        source_dir=rooted_tmp_path, package_json={"packageManager": f"yarn@{version}"}
    )
    mock_install_yarn.return_value = Path("/fake/yarn.js")
    mock_run_yarn_cmd.return_value = version

    with pytest.raises(PackageRejected):
        _install_and_verify_yarn(project)

    mock_install_yarn.assert_not_called()
    mock_run_yarn_cmd.assert_not_called()


@pytest.mark.parametrize(
    "package_manager, reported_version",
    [
        (None, "1.22.19"),
        ("yarn@1.22.19", "1.22.22"),
        ("yarn@1.22.22-rc.1", "1.22.22"),
        ("yarn@1.22.22+sha1." + "a" * 40, "1.22.19"),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn_classic.main.install_yarn")
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_yarn_cmd")
def test_classic_version_must_match_selected_release(
    mock_run_yarn_cmd: mock.Mock,
    mock_install_yarn: mock.Mock,
    rooted_tmp_path: RootedPath,
    package_manager: str | None,
    reported_version: str,
) -> None:
    project = mock.Mock(
        source_dir=rooted_tmp_path,
        package_json={} if package_manager is None else {"packageManager": package_manager},
    )
    mock_install_yarn.return_value = Path("/fake/yarn.js")
    mock_run_yarn_cmd.return_value = reported_version

    with pytest.raises(PackageManagerError):
        _install_and_verify_yarn(project)


@pytest.mark.parametrize(
    "package_manager, reported_version",
    [
        (None, "1.22.22"),
        ("yarn@1.22.0", "1.22.0"),
        ("yarn@1.22.19", "1.22.19"),
        ("yarn@1.22.22-rc.1", "1.22.22-rc.1"),
        ("yarn@1.22.22+sha1." + "a" * 40, "1.22.22"),
    ],
)
@mock.patch("hermeto.core.package_managers.javascript.yarn_classic.main.install_yarn")
@mock.patch("hermeto.core.package_managers.javascript.yarn.utils.run_yarn_cmd")
def test_classic_selected_release_is_accepted(
    mock_run_yarn_cmd: mock.Mock,
    mock_install_yarn: mock.Mock,
    rooted_tmp_path: RootedPath,
    package_manager: str | None,
    reported_version: str,
) -> None:
    project = mock.Mock(
        source_dir=rooted_tmp_path,
        package_json={} if package_manager is None else {"packageManager": package_manager},
    )
    yarn_bin = Path("/fake/yarn.js")
    mock_install_yarn.return_value = yarn_bin
    mock_run_yarn_cmd.return_value = reported_version

    assert _install_and_verify_yarn(project) == yarn_bin

    expected_version = (package_manager or "yarn@1.22.22").removeprefix("yarn@")
    mock_install_yarn.assert_called_once()
    assert str(mock_install_yarn.call_args.args[0]) == expected_version


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
