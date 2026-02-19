# SPDX-License-Identifier: GPL-3.0-only
from unittest import mock

from hermeto.core.package_managers.yarn_classic.project import PackageJson
from hermeto.core.package_managers.yarn_classic.utils import find_runtime_deps
from hermeto.core.package_managers.yarn_classic.workspaces import Workspace
from hermeto.core.rooted_path import RootedPath

PACKAGE_JSON = """
{
  "name": "main",
  "dependencies": {
    "main-dep1": "^1.2.0"
  },
  "optionalDependencies": {
    "optional-dep1": "^2.3.0"
  },
  "peerDependencies": {
    "peer-dep1": "^3.4.0"
  },
  "devDependencies": {
    "dev-dep1": "^4.5.0"
  }
}
"""


@mock.patch("hermeto.core.package_managers.yarn_classic.project.YarnLock")
def test_find_runtime_deps(
    mock_yarn_lock: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    package_json_path = rooted_tmp_path.join_within_root("package.json")
    package_json_path.path.write_text(PACKAGE_JSON)
    package_json = PackageJson.from_file(package_json_path)

    mock_yarn_lock_instance = mock_yarn_lock.return_value
    mock_yarn_lock_instance.data = {
        # dependencies
        "main-dep1@^1.2.0": {
            "version": "1.2.0",
            "dependencies": {"sub-dep1": "^1.3.0"},
        },
        # optional dependencies
        "optional-dep1@^2.3.0": {
            "version": "2.3.0",
            "dependencies": {"compound-multi-dep1": "^4.5.0", "compound-multi-dep2": "^4.6.0"},
        },
        "compound-multi-dep1@^4.5.0, compound-multi-dep2@^4.6.0": {
            "version": "5.7.0",
            "dependencies": {},
        },
        # peer dependencies
        "peer-dep1@^3.4.0": {"version": "3.4.0", "dependencies": {}},
        # transitive dependencies
        "sub-dep1@^1.3.0": {"version": "1.3.0", "dependencies": {"sub-dep11": "^1.4.0"}},
        "sub-dep11@^1.4.0": {"version": "1.4.0", "dependencies": {}},
        # dev dependencies
        "dev-dep1@^4.5.0": {"version": "4.5.0", "dependencies": {}},
    }

    result = find_runtime_deps(package_json, mock_yarn_lock_instance, [])
    expected_result = {
        "main-dep1@1.2.0",
        "optional-dep1@2.3.0",
        "compound-multi-dep1@5.7.0",
        "compound-multi-dep2@5.7.0",
        "peer-dep1@3.4.0",
        "sub-dep1@1.3.0",
        "sub-dep11@1.4.0",
        # no dev dependencies
    }

    assert result == expected_result


MAIN_PACKAGE_JSON = """
{
  "name": "main",
  "workspaces": ["packages/*"],
  "dependencies": {
    "foo": "^2.1.0"
  },
  "devDependencies": {
    "bar": "^3.2.0"
  }
}
"""

WORKSPACE_PACKAGE_JSON = """
{
  "name": "workspace",
  "dependencies": {
    "bar": "^3.2.0"
  }
}
"""


@mock.patch("hermeto.core.package_managers.yarn_classic.project.YarnLock")
def test_find_runtime_deps_with_workspace(
    mock_yarn_lock: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    package_json_path = rooted_tmp_path.join_within_root("package.json")
    package_json_path.path.write_text(MAIN_PACKAGE_JSON)
    package_json = PackageJson.from_file(package_json_path)

    workspace_dir = rooted_tmp_path.join_within_root("packages/workspace")
    workspace_dir.path.mkdir(parents=True)

    workspace_package_json = workspace_dir.join_within_root("package.json")
    workspace_package_json.path.write_text(WORKSPACE_PACKAGE_JSON)

    w = Workspace(
        path=workspace_dir.path,
        package_json=PackageJson.from_file(workspace_package_json),
    )

    mock_yarn_lock_instance = mock_yarn_lock.return_value
    mock_yarn_lock_instance.data = {
        # dependencies from main package.json
        "foo@^2.1.0": {"version": "2.1.0", "dependencies": {}},
        # dependencies from workspace package.json
        "bar@^3.2.0": {"version": "3.2.0", "dependencies": {}},
    }

    result = find_runtime_deps(package_json, mock_yarn_lock_instance, [w])
    expected_result = {"foo@2.1.0", "bar@3.2.0"}

    assert result == expected_result
