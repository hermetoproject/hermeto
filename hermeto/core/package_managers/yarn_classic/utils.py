import re
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pyarn.lockfile import Package as PYarnPackage

from cachi2.core.package_managers.yarn_classic.project import PackageJson, YarnLock
from cachi2.core.package_managers.yarn_classic.workspaces import Workspace


def find_runtime_deps(
    main_package_json: PackageJson,
    yarn_lock: YarnLock,
    workspaces: list[Workspace],
) -> set[str]:
    """
    Identify all runtime dependencies in the root package and its workspaces.

    A dependency is classified as runtime if:
    - It is listed in `dependencies`, `peerDependencies`, or `optionalDependencies`
      of any `package.json` file.
    - It is a transitive dependency of another runtime dependency.

    A dependency is classified for development if:
    - It is listed in the `devDependencies` of any `package.json` file.
    - It is a transitive dependency of a dev dependency.

    Note: If a dependency is marked as runtime dependency somewhere
    and as a development dependency somewhere else, it is classified as runtime.
    """
    all_package_jsons = [main_package_json] + [ws.package_json for ws in workspaces]
    expanded_yarn_lock = _expand_yarn_lock_keys(yarn_lock)

    root_deps: list[PYarnPackage] = []
    for package_json in all_package_jsons:
        for dep_type in ["dependencies", "peerDependencies", "optionalDependencies"]:
            for name, version_specifier in package_json.data.get(dep_type, {}).items():
                key = f"{name}@{version_specifier}"
                data = expanded_yarn_lock.get(key)

                if not data:
                    # peerDependencies are not always present in the yarn.lock
                    continue

                root_dep = PYarnPackage.from_dict(key, data)
                root_deps.append(root_dep)

    all_dep_ids: set[str] = set()
    for root_dep in root_deps:
        transitive_dep_ids = _find_transitive_deps(root_dep, expanded_yarn_lock)
        all_dep_ids.update(transitive_dep_ids)

    return all_dep_ids


def _expand_yarn_lock_keys(yarn_lock: YarnLock) -> dict[str, dict[str, Any]]:
    """
    Expand compound keys in the yarn.lock dictionary into individual keys.

    In the original yarn.lock dictionary, a single key may represent multiple package names,
    separated by commas (e.g., "package-a@^1.0.0, package-b@^2.0.0"). These are referred to
    as compound keys, where multiple keys share the same value (N:1 mapping).

    This function splits such compound keys into individual keys, creating a new dictionary
    where each key maps directly to the same shared value as in the original dictionary.
    The result is a dictionary with only one-to-one (1:1) key-value mappings.

    Note: This function does not copy the values. The newly created individual keys will
    all reference the same original value object.
    """

    def split_multi_key(dep_id: str) -> list[str]:
        return dep_id.replace('"', "").split(", ")

    result = {}
    for dep_id in yarn_lock.data.keys():
        for key in split_multi_key(dep_id):
            result[key] = yarn_lock.data[dep_id]

    return result


def _find_transitive_deps(
    root_dep: PYarnPackage,
    expanded_yarn_lock: dict[str, dict[str, Any]],
) -> set[str]:
    """Perform a breadth-first search (BFS) algorithm to find all transitive dependencies of a given root dependency.

    The search is performed on the expanded yarn.lock dictionary, which contains individual keys for each package.
    Keys in the expanded yarn.lock dictionary contains version specifiers, not the resolved version of the package.

    If expanded_yarn_lock contains a key "foo@^1.0.0", the actual resolved version of "foo" may be for example "1.1.0".
    The result of this function is a set of strings in the format "package-name@-resolved-version" of all transitive dependencies.
    """
    bfs_queue: deque[PYarnPackage] = deque([root_dep])
    visited: set[str] = set()

    while bfs_queue:
        current = bfs_queue.popleft()
        dep_id = f"{current.name}@{current.version}"
        visited.add(dep_id)

        for name, version_specifier in current.dependencies.items():
            key = f"{name}@{version_specifier}"
            data = expanded_yarn_lock.get(key)

            new_dep = PYarnPackage.from_dict(key, data)
            new_dep_id = f"{new_dep.name}@{new_dep.version}"

            if new_dep_id not in visited:
                bfs_queue.append(new_dep)

    return visited


# https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/fetchers/tarball-fetcher.js#L21
RE_URL_NAME_MATCH = r"/(?:(@[^/]+)(?:\/|%2f))?[^/]+/(?:-|_attachments)/(?:@[^/]+\/)?([^/]+)$"


# https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/fetchers/tarball-fetcher.js#L65
def get_tarball_mirror_name(url: str) -> str:
    """Get the name of the tarball file that will be stored in the offline mirror."""
    parsed_url = urlparse(url)
    path = Path(parsed_url.path)

    match = re.search(RE_URL_NAME_MATCH, str(path))

    if match is not None:
        scope, tarball_basename = match.groups()
        package_filename = f"{scope}-{tarball_basename}" if scope else tarball_basename
    else:
        package_filename = path.name

    return package_filename


# https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/fetchers/git-fetcher.js#L40
def get_git_tarball_mirror_name(url: str) -> str:
    """Get the name of the tarball file that will be stored in the offline mirror for git packages."""
    parsed_url = urlparse(url)
    path = Path(parsed_url.path)

    package_filename = path.name
    hash = parsed_url.fragment

    if hash:
        package_filename = f"{package_filename}-{hash}"

    if package_filename.startswith(":"):
        package_filename = package_filename[1:]

    return package_filename
