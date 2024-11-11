import json

import pytest

from cachi2.core.errors import PackageRejected
from cachi2.core.package_managers.yarn_classic.main import _verify_repository
from cachi2.core.package_managers.yarn_classic.project import ConfigFile, PackageJson, Project
from cachi2.core.rooted_path import RootedPath

VALID_PACKAGE_JSON_FILE = """
{
  "name": "camelot",
  "packageManager": "yarn@3.6.1"
}
"""

PNP_PACKAGE_JSON_FILE = """
{
  "name": "camelot",
  "packageManager": "yarn@3.6.1",
  "installConfig": {
    "pnp": true
  }
}
"""

INVALID_JSON_FILE = "totally not json"


def _prepare_config_file(
    rooted_tmp_path: RootedPath, config_file_class: ConfigFile, filename: str, content: str
) -> ConfigFile:
    path = rooted_tmp_path.join_within_root(filename)

    with open(path, "w") as f:
        f.write(content)

    return config_file_class.from_file(path)


def _setup_pnp_installs(rooted_tmp_path: RootedPath, pnp_kind: str) -> None:
    if pnp_kind == "node":
        rooted_tmp_path.join_within_root("node_modules").path.mkdir()
    if pnp_kind == "pnp_cjs":
        rooted_tmp_path.join_within_root("foo.pnp.cjs").path.touch()


@pytest.mark.parametrize(
    "config_file_class, config_file_name, config_file_content, config_kind",
    [
        pytest.param(
            PackageJson, "package.json", VALID_PACKAGE_JSON_FILE, "package_json", id="package_json"
        ),
    ],
)
def test_config_file_attributes(
    rooted_tmp_path: RootedPath,
    config_file_class: ConfigFile,
    config_file_name: str,
    config_file_content: str,
    config_kind: str,
) -> None:
    found_config = _prepare_config_file(
        rooted_tmp_path,
        config_file_class,
        config_file_name,
        config_file_content,
    )
    assert found_config.path.root == rooted_tmp_path.root
    assert found_config.config_kind == config_kind


@pytest.mark.parametrize(
    "config_file_class, config_file_name, config_file_content, content_kind",
    [
        pytest.param(
            PackageJson, "package.json", VALID_PACKAGE_JSON_FILE, "json", id="package_json"
        ),
    ],
)
def test_find_and_open_config_file(
    rooted_tmp_path: RootedPath,
    config_file_class: ConfigFile,
    config_file_name: str,
    config_file_content: str,
    content_kind: str,
) -> None:
    found_config = _prepare_config_file(
        rooted_tmp_path,
        config_file_class,
        config_file_name,
        config_file_content,
    )

    if content_kind == "json":
        assert found_config.data == json.loads(config_file_content)


@pytest.mark.parametrize(
    "config_file_class, config_file_name, config_file_content",
    [
        pytest.param(
            PackageJson,
            "package.json",
            INVALID_JSON_FILE,
            id="invalid_package_json",
        ),
    ],
)
def test_from_file_bad(
    rooted_tmp_path: RootedPath,
    config_file_class: ConfigFile,
    config_file_name: str,
    config_file_content: str,
) -> None:
    with pytest.raises(PackageRejected):
        _prepare_config_file(
            rooted_tmp_path,
            config_file_class,
            config_file_name,
            config_file_content,
        )


@pytest.mark.parametrize(
    "config_file_class, config_file_name",
    [
        pytest.param(
            PackageJson,
            "package.json",
            id="missing_package_json",
        ),
    ],
)
def test_from_file_missing(
    rooted_tmp_path: RootedPath,
    config_file_class: ConfigFile,
    config_file_name: str,
) -> None:
    with pytest.raises(PackageRejected):
        path = rooted_tmp_path.join_within_root(config_file_name)
        config_file_class.from_file(path)


@pytest.mark.parametrize(
    "config_file_class, config_file_name, config_file_content, pnp_kind",
    [
        pytest.param(
            PackageJson,
            "package.json",
            PNP_PACKAGE_JSON_FILE,
            "install_config",
            id="installConfig",
        ),
        pytest.param(PackageJson, "package.json", VALID_PACKAGE_JSON_FILE, "node", id="node"),
        pytest.param(PackageJson, "package.json", VALID_PACKAGE_JSON_FILE, "pnp_cjs", id="pnp_cjs"),
    ],
)
def test_pnp_installs_true(
    rooted_tmp_path: RootedPath,
    config_file_class: ConfigFile,
    config_file_name: str,
    config_file_content: str,
    pnp_kind: str,
) -> None:
    _prepare_config_file(
        rooted_tmp_path,
        config_file_class,
        config_file_name,
        config_file_content,
    )

    project = Project.from_source_dir(rooted_tmp_path)

    _setup_pnp_installs(rooted_tmp_path, pnp_kind)
    with pytest.raises(PackageRejected):
        _verify_repository(project)


@pytest.mark.parametrize(
    "config_file_class, config_file_name, config_file_content",
    [
        pytest.param(
            PackageJson,
            "package.json",
            VALID_PACKAGE_JSON_FILE,
        ),
    ],
)
def test_pnp_installs_false(
    rooted_tmp_path: RootedPath,
    config_file_class: ConfigFile,
    config_file_name: str,
    config_file_content: str,
) -> None:
    _prepare_config_file(
        rooted_tmp_path,
        config_file_class,
        config_file_name,
        config_file_content,
    )

    project = Project.from_source_dir(rooted_tmp_path)

    _verify_repository(project)
