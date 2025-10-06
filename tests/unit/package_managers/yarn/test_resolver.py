import json
import re
import zipfile
from pathlib import Path
from typing import Any, NamedTuple, Optional, Union
from unittest import mock
from urllib.parse import quote

import pytest
from semver import Version

from hermeto import APP_NAME
from hermeto.core.errors import PackageRejected, UnsupportedFeature
from hermeto.core.models.sbom import Component, Patch, PatchDiff, Pedigree
from hermeto.core.package_managers.yarn.locators import (
    Locator,
    NpmLocator,
    PatchLocator,
    WorkspaceLocator,
    parse_locator,
)
from hermeto.core.package_managers.yarn.project import PackageJson, Project, YarnRc
from hermeto.core.package_managers.yarn.resolver import (
    Package,
    _ComponentResolver,
    create_components,
    resolve_packages,
)
from hermeto.core.rooted_path import RootedPath
from hermeto.core.scm import RepoID

MOCK_REPO_ID = RepoID("https://github.com/org/project.git", "fffffff")
MOCK_REPO_VCS_URL = quote("git+https://github.com/org/project.git@fffffff", safe="://")


def mock_yarn_info_output(yarn_info_outputs: list[dict[str, Any]]) -> str:
    yarn_info_string_output = "\n".join(
        json.dumps(obj, separators=(",", ":")) for obj in yarn_info_outputs
    )
    return yarn_info_string_output + "\n"


# re-generate using hack/mock-unittest-data/yarn.py
YARN_INFO_OUTPUTS = [
    {
        "value": "@isaacs/cliui@npm:8.0.2",
        "children": {
            "Version": "8.0.2",
            "Cache": {
                "Checksum": "8/4a473b9b32a7d4d3cfb7a614226e555091ff0c5a29a1734c28c72a182c2f6699b26fc6b5c2131dfd841e86b185aea714c72201d7c98c2fba5f17709333a67aeb",
                "Path": "{repo_dir}/.yarn/cache/@isaacs-cliui-npm-8.0.2-f4364666d5-4a473b9b32.zip",
                "Size": 10582,
            },
        },
    },
    {
        "value": "ansi-regex-link@link:external-packages/ansi-regex::locator=berryscary%40workspace%3A.",
        "children": {"Version": "0.0.0-use.local", "Cache": {"Checksum": None, "Path": None}},
    },
    {
        "value": "berryscary@workspace:.",
        "children": {
            "Instances": 1,
            "Version": "0.0.0-use.local",
            "Cache": {"Checksum": None, "Path": None},
            "Exported Binaries": ["berryscary"],
        },
    },
    {
        "value": "c2-wo-deps-2@https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz",
        "children": {
            "Version": "2.0.0",
            "Cache": {
                "Checksum": "8/b194fd1f4a79472a332fec936818d1713a222157e845a8d466a239fdc950130a7ad9b77c212d69d2947c07bce0c911446496ff47dec5a73b4368f0a9c9432b1d",
                "Path": "{repo_dir}/.yarn/cache/c2-wo-deps-2-https-4261b189d8-b194fd1f4a.zip",
                "Size": 1925,
            },
        },
    },
    {
        "value": "fsevents@patch:fsevents@npm%3A2.3.2#./my-patches/fsevents.patch::version=2.3.2&hash=cf0bf0&locator=berryscary%40workspace%3A.",
        "children": {
            "Version": "2.3.2",
            "Cache": {
                "Checksum": "8/f73215b04b52395389a612af4d30f7f412752cdfba1580c9e32c7ec259e448b57b464a4d0474427d6142f5ed9a6260fc1841d61834caf44706d77874fba6f17f",
                "Path": "{repo_dir}/.yarn/cache/fsevents-patch-9d1204d729-f73215b04b.zip",
                "Size": 22847,
            },
        },
    },
    {
        "value": "fsevents@patch:fsevents@patch%3Afsevents@npm%253A2.3.2%23./my-patches/fsevents.patch%3A%3Aversion=2.3.2&hash=cf0bf0&locator=berryscary%2540workspace%253A.#~builtin<compat/fsevents>::version=2.3.2&hash=df0bf1",
        "children": {
            "Version": "2.3.2",
            "Cache": {
                "Checksum": None,
                "Path": "{repo_dir}/.yarn/cache/fsevents-patch-e4409ad759-8.zip",
            },
        },
    },
    {
        "value": "old-man-from-scene-24@workspace:packages/old-man-from-scene-24",
        "children": {"Version": "0.0.0-use.local", "Cache": {"Checksum": None, "Path": None}},
    },
    {
        "value": "once-portal@portal:external-packages/once::locator=berryscary%40workspace%3A.",
        "children": {"Version": "0.0.0-use.local", "Cache": {"Checksum": None, "Path": None}},
    },
    {
        "value": "strip-ansi-tarball@file:../../external-packages/strip-ansi-4.0.0.tgz::locator=the-answer%40workspace%3Apackages%2Fthe-answer",
        "children": {
            "Version": "4.0.0",
            "Cache": {
                "Checksum": "8/d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
                "Path": "{repo_dir}/.yarn/cache/strip-ansi-tarball-file-489a50cded-d67629c877.zip",
                "Size": 2419,
            },
        },
    },
    {
        "value": "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
        "children": {
            "Version": "4.0.0",
            "Cache": {
                "Checksum": "8/d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
                "Path": "{repo_dir}/.yarn/cache/strip-ansi-tarball-file-3176cc06fb-d67629c877.zip",
                "Size": 2419,
            },
        },
    },
]


EXPECT_PACKAGES = [
    Package(
        raw_locator="@isaacs/cliui@npm:8.0.2",
        version="8.0.2",
        checksum="4a473b9b32a7d4d3cfb7a614226e555091ff0c5a29a1734c28c72a182c2f6699b26fc6b5c2131dfd841e86b185aea714c72201d7c98c2fba5f17709333a67aeb",
        cache_path="{repo_dir}/.yarn/cache/@isaacs-cliui-npm-8.0.2-f4364666d5-4a473b9b32.zip",
    ),
    Package(
        raw_locator="ansi-regex-link@link:external-packages/ansi-regex::locator=berryscary%40workspace%3A.",
        version=None,
        checksum=None,
        cache_path=None,
    ),
    Package(raw_locator="berryscary@workspace:.", version=None, checksum=None, cache_path=None),
    Package(
        raw_locator="c2-wo-deps-2@https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz",
        version="2.0.0",
        checksum="b194fd1f4a79472a332fec936818d1713a222157e845a8d466a239fdc950130a7ad9b77c212d69d2947c07bce0c911446496ff47dec5a73b4368f0a9c9432b1d",
        cache_path="{repo_dir}/.yarn/cache/c2-wo-deps-2-https-4261b189d8-b194fd1f4a.zip",
    ),
    Package(
        raw_locator="fsevents@patch:fsevents@npm%3A2.3.2#./my-patches/fsevents.patch::version=2.3.2&hash=cf0bf0&locator=berryscary%40workspace%3A.",
        version="2.3.2",
        checksum="f73215b04b52395389a612af4d30f7f412752cdfba1580c9e32c7ec259e448b57b464a4d0474427d6142f5ed9a6260fc1841d61834caf44706d77874fba6f17f",
        cache_path="{repo_dir}/.yarn/cache/fsevents-patch-9d1204d729-f73215b04b.zip",
    ),
    Package(
        raw_locator="fsevents@patch:fsevents@patch%3Afsevents@npm%253A2.3.2%23./my-patches/fsevents.patch%3A%3Aversion=2.3.2&hash=cf0bf0&locator=berryscary%2540workspace%253A.#~builtin<compat/fsevents>::version=2.3.2&hash=df0bf1",
        version="2.3.2",
        checksum=None,
        cache_path="{repo_dir}/.yarn/cache/fsevents-patch-e4409ad759-8.zip",
    ),
    Package(
        raw_locator="old-man-from-scene-24@workspace:packages/old-man-from-scene-24",
        version=None,
        checksum=None,
        cache_path=None,
    ),
    Package(
        raw_locator="once-portal@portal:external-packages/once::locator=berryscary%40workspace%3A.",
        version=None,
        checksum=None,
        cache_path=None,
    ),
    Package(
        raw_locator="strip-ansi-tarball@file:../../external-packages/strip-ansi-4.0.0.tgz::locator=the-answer%40workspace%3Apackages%2Fthe-answer",
        version="4.0.0",
        checksum="d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
        cache_path="{repo_dir}/.yarn/cache/strip-ansi-tarball-file-489a50cded-d67629c877.zip",
    ),
    Package(
        raw_locator="strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
        version="4.0.0",
        checksum="d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
        cache_path="{repo_dir}/.yarn/cache/strip-ansi-tarball-file-3176cc06fb-d67629c877.zip",
    ),
]


@mock.patch("hermeto.core.package_managers.yarn.resolver.run_yarn_cmd")
def test_resolve_packages(mock_run_yarn_cmd: mock.Mock, rooted_tmp_path: RootedPath) -> None:
    yarn_info_output = mock_yarn_info_output(YARN_INFO_OUTPUTS)
    mock_run_yarn_cmd.return_value = yarn_info_output
    packages = resolve_packages(rooted_tmp_path)
    assert packages == EXPECT_PACKAGES

    for package in packages:
        assert package.parsed_locator == parse_locator(package.raw_locator)


@mock.patch("hermeto.core.package_managers.yarn.resolver.run_yarn_cmd")
def test_validate_unsupported_locators(
    mock_run_yarn_cmd: mock.Mock, rooted_tmp_path: RootedPath, caplog: pytest.LogCaptureFixture
) -> None:
    unsupported_outputs = [
        {
            "value": "ccto-wo-deps@git@github.com:cachito-testing/cachito-npm-without-deps.git#commit=2f0ce1d7b1f8b35572d919428b965285a69583f6",
            "children": {
                "Version": "1.0.0",
                "Cache": {
                    "Checksum": "8/3ed9ea417c75a1999925159e67cf04bf2d522967692a55321559ef2b353fa690167b7bc40e989e4ee35e36d095f007f2d0c53faeb55f14d07ec3ece34faba206",
                    "Path": "{repo_dir}/.yarn/cache/ccto-wo-deps-git@github.com-e0fce8c89c-3ed9ea417c.zip",
                    "Size": 638,
                },
            },
        },
        {
            "value": "ccto-wo-deps@patch:ccto-wo-deps@git@github.com%3Acachito-testing/cachito-npm-without-deps.git%23commit=2f0ce1d7b1f8b35572d919428b965285a69583f6#./.yarn/patches/ccto-wo-deps-git@github.com-e0fce8c89c.patch::version=1.0.0&hash=51a91f&locator=berryscary%40workspace%3A.",
            "children": {
                "Version": "1.0.0",
                "Cache": {
                    "Checksum": "8/98355f046f66b70b4ae4aec87fb20c83eb635a7138b5bb25dcbfa567ae4fcc4240ff1178de2f985776ab6cea1f55af8e085d798f5077b8a8b5bb5cb5278293d4",
                    "Path": "{repo_dir}/.yarn/cache/ccto-wo-deps-patch-c3567b709f-98355f046f.zip",
                    "Size": 647,
                },
            },
        },
        {
            "value": "holy-hand-grenade@exec:./generate-holy-hand-grenade.js#./generate-holy-hand-grenade.js::hash=3b5cbd&locator=berryscary%40workspace%3A.",
            "children": {
                "Version": "1.0.0",
                "Cache": {
                    "Checksum": "8/6053ad5dc79d8fedfdc528e1bf75e3f4a1a4558a8184f55589e1e54ab8819f5111ffc1812333906cfcfa05fdd3e81d9b65191d1a093066f3a3f479a61c626be9",
                    "Path": "{repo_dir}/.yarn/cache/holy-hand-grenade-exec-e88e9eb6dd-6053ad5dc7.zip",
                    "Size": 883,
                },
            },
        },
    ]
    yarn_info_output = mock_yarn_info_output(unsupported_outputs)
    mock_run_yarn_cmd.return_value = yarn_info_output

    with pytest.raises(
        UnsupportedFeature, match="Found 3 unsupported dependencies, more details in the logs."
    ):
        resolve_packages(rooted_tmp_path)

    assert caplog.messages == [
        f"{APP_NAME} does not support Git or Exec dependencies for Yarn Berry: ccto-wo-deps@git@github.com:cachito-testing/cachito-npm-without-deps.git#commit=2f0ce1d7b1f8b35572d919428b965285a69583f6",
        f"{APP_NAME} does not support Git or Exec dependencies for Yarn Berry: ccto-wo-deps@git@github.com:cachito-testing/cachito-npm-without-deps.git#commit=2f0ce1d7b1f8b35572d919428b965285a69583f6",
        f"{APP_NAME} does not support Git or Exec dependencies for Yarn Berry: holy-hand-grenade@exec:./generate-holy-hand-grenade.js#./generate-holy-hand-grenade.js::hash=3b5cbd&locator=berryscary%40workspace%3A.",
    ]


class MockedPackage(NamedTuple):
    package: Package
    is_hardlink: bool
    packjson_path: Optional[str] = None
    packjson_content: Optional[str] = None

    def resolve_cache_path(self, root_dir: RootedPath) -> "MockedPackage":
        cache_path = self.package.cache_path
        if cache_path:
            cache_path = root_dir.join_within_root(cache_path).path.as_posix()
        package = Package(
            raw_locator=self.package.raw_locator,
            version=self.package.version,
            checksum=self.package.checksum,
            cache_path=cache_path,
        )
        return MockedPackage(package, self.is_hardlink, self.packjson_path, self.packjson_content)


def mock_package_json(
    mocked_package: MockedPackage,
    project_dir: RootedPath,
) -> None:
    package, is_hardlink, packjson_path, packjson_content = mocked_package

    if is_hardlink:
        if not package.cache_path:
            assert not (packjson_path or packjson_content), (
                f"cache_path=None, is_hardlink=True => can't mock package.json: {package.raw_locator}"
            )
            return

        zipfile_path = Path(package.cache_path)
        zipfile_path.parent.mkdir(exist_ok=True, parents=True)

        # create zip file if the package has a cache_path
        with zipfile.ZipFile(zipfile_path, "w") as zf:
            # write package.json if it should exist
            if packjson_path and packjson_content:
                zf.writestr(packjson_path, packjson_content)

    elif packjson_path and packjson_content:
        # write package.json (if it should exist) directly to project dir
        path = project_dir.join_within_root(packjson_path).path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(packjson_content)


def mock_project(project_dir: RootedPath) -> Project:
    return Project(
        project_dir,
        YarnRc(project_dir.join_within_root(".yarnrc.yml"), {}),
        PackageJson(project_dir.join_within_root("package.json"), {}),
    )


@mock.patch("hermeto.core.package_managers.yarn.resolver.get_repo_id")
@pytest.mark.parametrize(
    "mocked_package, expect_component, expect_logs",
    [
        # Scoped npm package
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="@isaacs/cliui@npm:8.0.2",
                    version="8.0.2",
                    checksum="4a473b9b32a7d4d3cfb7a614226e555091ff0c5a29a1734c28c72a182c2f6699b26fc6b5c2131dfd841e86b185aea714c72201d7c98c2fba5f17709333a67aeb",
                    # we don't need the cache archive to resolve an npm dependency
                    cache_path=None,
                ),
                is_hardlink=True,
            ),
            Component(
                name="@isaacs/cliui",
                version="8.0.2",
                purl=f"pkg:npm/{quote('@isaacs')}/cliui@8.0.2",
            ),
            [],
            id="scoped_npm_package",
        ),
        # Unscoped npm package
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="abbrev@npm:1.1.1",
                    version="1.1.1",
                    checksum="a4a97ec07d7ea112c517036882b2ac22f3109b7b19077dc656316d07d308438aac28e4d9746dc4d84bf6b1e75b4a7b0a5f3cb30592419f128ca9a8cee3bcfa17",
                    cache_path=None,
                ),
                is_hardlink=True,
            ),
            Component(
                name="abbrev",
                version="1.1.1",
                purl="pkg:npm/abbrev@1.1.1",
            ),
            [],
            id="unscoped_npm_package",
        ),
        # Workspace
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="armaments@workspace:./book/armaments",
                    version=None,
                    checksum=None,
                    cache_path=None,
                ),
                is_hardlink=False,
                packjson_path="book/armaments/package.json",
                packjson_content=json.dumps({"name": "armaments", "version": "42.0.0"}),
            ),
            Component(
                name="armaments",
                version="42.0.0",
                purl=f"pkg:npm/armaments@42.0.0?vcs_url={MOCK_REPO_VCS_URL}#book/armaments",
            ),
            [
                "armaments@workspace:./book/armaments: reading package version from book/armaments/package.json"
            ],
            id="workspace_package",
        ),
        # Portal package
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="antioch@portal:holy-hand-grenade::locator=armaments%40workspace%3A./book/armaments",
                    version=None,
                    checksum=None,
                    cache_path=None,
                ),
                is_hardlink=False,
                # {path_to_workspace}/{path_to_portal}/package.json
                packjson_path="book/armaments/holy-hand-grenade/package.json",
                packjson_content=json.dumps(
                    {"name": "@antioch/holy-hand-grenade", "version": "1.2.5-threesir"}
                ),
            ),
            Component(
                name="@antioch/holy-hand-grenade",
                version="1.2.5-threesir",
                purl=f"pkg:npm/{quote('@antioch')}/holy-hand-grenade@1.2.5-threesir?vcs_url={MOCK_REPO_VCS_URL}#book/armaments/holy-hand-grenade",
            ),
            [
                (
                    "antioch@portal:holy-hand-grenade::locator=armaments%40workspace%3A./book/armaments: "
                    "reading package name and version from book/armaments/holy-hand-grenade/package.json"
                )
            ],
            id="portal_package",
        ),
        # Same as above, but as a Link package
        # Link packages don't need package.json, but if they have one, let's take it into account
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="antioch@link:holy-hand-grenade::locator=armaments%40workspace%3A./book/armaments",
                    version=None,
                    checksum=None,
                    cache_path=None,
                ),
                is_hardlink=False,
                packjson_path="book/armaments/holy-hand-grenade/package.json",
                packjson_content=json.dumps(
                    {"name": "@antioch/holy-hand-grenade", "version": "1.2.5-threesir"}
                ),
            ),
            Component(
                name="@antioch/holy-hand-grenade",
                version="1.2.5-threesir",
                purl=f"pkg:npm/{quote('@antioch')}/holy-hand-grenade@1.2.5-threesir?vcs_url={MOCK_REPO_VCS_URL}#book/armaments/holy-hand-grenade",
            ),
            [
                (
                    "antioch@link:holy-hand-grenade::locator=armaments%40workspace%3A./book/armaments: "
                    "reading package name and version from book/armaments/holy-hand-grenade/package.json"
                )
            ],
            id="link_package_that_happens_to_have_package_json",
        ),
        # A more typical Link package, with no package.json
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="antioch@link:holy-hand-grenade::locator=armaments%40workspace%3A./book/armaments",
                    version=None,
                    checksum=None,
                    cache_path=None,
                ),
                is_hardlink=False,
            ),
            Component(
                name="antioch",
                version=None,
                purl=f"pkg:npm/antioch?vcs_url={MOCK_REPO_VCS_URL}#book/armaments/holy-hand-grenade",
            ),
            [],
            id="link_package",
        ),
        # File package
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
                    version="4.0.0",
                    checksum="d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
                    cache_path="cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip",
                ),
                is_hardlink=True,
                packjson_path="node_modules/strip-ansi-tarball/package.json",
                packjson_content=json.dumps({"name": "strip-ansi"}),
            ),
            Component(
                name="strip-ansi",
                version="4.0.0",
                purl=f"pkg:npm/strip-ansi@4.0.0?vcs_url={MOCK_REPO_VCS_URL}#external-packages/strip-ansi-4.0.0.tgz",
            ),
            [
                (
                    "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.: "
                    "reading package name from cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip"
                ),
            ],
            id="file_package",
        ),
        # File package in parent directory
        # `purl` should be the same as above since the subpath is normalized
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="strip-ansi-tarball@file:../../external-packages/strip-ansi-4.0.0.tgz::locator=the-answer%40workspace%3Apackages%2Fthe-answer",
                    version="4.0.0",
                    checksum="d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
                    cache_path="cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip",
                ),
                is_hardlink=True,
                packjson_path="node_modules/strip-ansi-tarball/package.json",
                packjson_content=json.dumps({"name": "strip-ansi"}),
            ),
            Component(
                name="strip-ansi",
                version="4.0.0",
                purl=f"pkg:npm/strip-ansi@4.0.0?vcs_url={MOCK_REPO_VCS_URL}#external-packages/strip-ansi-4.0.0.tgz",
            ),
            [
                (
                    "strip-ansi-tarball@file:../../external-packages/strip-ansi-4.0.0.tgz::locator=the-answer%40workspace%3Apackages%2Fthe-answer: "
                    "reading package name from cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip"
                ),
            ],
            id="file_package_parent_dir",
        ),
        # Https package
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="@cachito/c2-wo-deps-2@https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz",
                    version="2.0.0",
                    checksum="b194fd1f4a79472a332fec936818d1713a222157e845a8d466a239fdc950130a7ad9b77c212d69d2947c07bce0c911446496ff47dec5a73b4368f0a9c9432b1d",
                    cache_path="cache/directory/c2-wo-deps-2-https-4261b189d8-b194fd1f4a.zip",
                ),
                is_hardlink=True,
                packjson_path="node_modules/@cachito/c2-wo-deps-2/package.json",
                packjson_content=json.dumps({"name": "bitbucket-cachi2-npm-without-deps-second"}),
            ),
            Component(
                name="bitbucket-cachi2-npm-without-deps-second",
                version="2.0.0",
                purl=(
                    "pkg:npm/bitbucket-cachi2-npm-without-deps-second@2.0.0"
                    "?checksum=sha512:b194fd1f4a79472a332fec936818d1713a222157e845a8d466a239fdc950130a7ad9b77c212d69d2947c07bce0c911446496ff47dec5a73b4368f0a9c9432b1d"
                    "&download_url=https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz"
                ),
            ),
            [
                (
                    "@cachito/c2-wo-deps-2@https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz: "
                    "reading package name from cache/directory/c2-wo-deps-2-https-4261b189d8-b194fd1f4a.zip"
                ),
            ],
            id="https_package",
        ),
    ],
)
def test_create_components_single_package(
    mock_get_repo_id: mock.Mock,
    mocked_package: MockedPackage,
    expect_component: Component,
    expect_logs: list[str],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_get_repo_id.return_value = MOCK_REPO_ID

    project_dir = RootedPath(tmp_path / "project")
    output_dir = RootedPath(tmp_path / "output")

    mocked_package = mocked_package.resolve_cache_path(output_dir)
    mock_package_json(mocked_package, project_dir)

    components = create_components([mocked_package.package], mock_project(project_dir), output_dir)

    assert len(components) == 1
    assert components[0] == expect_component
    assert caplog.messages == expect_logs


@mock.patch("hermeto.core.package_managers.yarn.resolver.get_repo_id")
@mock.patch("hermeto.core.package_managers.yarn.resolver.extract_yarn_version_from_env")
def test_create_components_patched_packages(
    mock_get_yarn_version: mock.Mock,
    mock_get_repo_id: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_get_yarn_version.return_value = Version(3, 0, 0)
    mock_get_repo_id.return_value = MOCK_REPO_ID
    project_dir = rooted_tmp_path

    mocked_packages = [
        MockedPackage(
            Package(
                raw_locator="fsevents@npm:2.3.2",
                version="2.3.2",
                checksum="97ade64e75091afee5265e6956cb72ba34db7819b4c3e94c431d4be2b19b8bb7a2d4116da417950c3425f17c8fe693d25e20212cac583ac1521ad066b77ae31f",
                cache_path=project_dir.join_within_root(
                    ".yarn/cache/fsevents-npm-2.3.2-a881d6ac9f-97ade64e75.zip"
                ).path.as_posix(),
            ),
            is_hardlink=True,
        ),
        MockedPackage(
            Package(
                raw_locator="fsevents@patch:fsevents@npm%3A2.3.2#./my-patches/fsevents.patch::version=2.3.2&hash=cf0bf0&locator=berryscary%40workspace%3A.",
                version="2.3.2",
                checksum="f73215b04b52395389a612af4d30f7f412752cdfba1580c9e32c7ec259e448b57b464a4d0474427d6142f5ed9a6260fc1841d61834caf44706d77874fba6f17f",
                cache_path=project_dir.join_within_root(
                    ".yarn/cache/fsevents-patch-9d1204d729-f73215b04b.zip"
                ).path.as_posix(),
            ),
            is_hardlink=True,
        ),
        MockedPackage(
            Package(
                # Note: this package patches the patched package above
                raw_locator="fsevents@patch:fsevents@patch%3Afsevents@npm%253A2.3.2%23./my-patches/fsevents.patch%3A%3Aversion=2.3.2&hash=cf0bf0&locator=berryscary%2540workspace%253A.#./my-patches/fsevents-2.patch::version=2.3.2&hash=df0bf1&locator=berryscary%40workspace%3A.",
                version="2.3.2",
                checksum=None,
                cache_path=project_dir.join_within_root(
                    ".yarn/cache/fsevents-patch-e4409ad759-8.zip"
                ).path.as_posix(),
            ),
            is_hardlink=True,
        ),
    ]

    components = create_components(
        [mocked_package.package for mocked_package in mocked_packages],
        mock_project(project_dir),
        output_dir=RootedPath("/unused"),
    )

    expect_components = [
        Component(
            name="fsevents",
            version="2.3.2",
            purl="pkg:npm/fsevents@2.3.2",
            pedigree=Pedigree(
                patches=[
                    Patch(
                        type="unofficial",
                        diff=PatchDiff(
                            url="git+https://github.com/org/project.git@fffffff#my-patches/fsevents.patch"
                        ),
                    ),
                    Patch(
                        type="unofficial",
                        diff=PatchDiff(
                            url="git+https://github.com/org/project.git@fffffff#my-patches/fsevents-2.patch"
                        ),
                    ),
                ],
            ),
        ),
    ]

    assert components == expect_components


@mock.patch("hermeto.core.package_managers.yarn.resolver.get_repo_id")
@mock.patch("hermeto.core.package_managers.yarn.resolver.extract_yarn_version_from_env")
def test_create_components_patched_packages_with_multiple_paths(
    mock_get_yarn_version: mock.Mock,
    mock_get_repo_id: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    mock_get_yarn_version.return_value = Version(3, 0, 0)
    mock_get_repo_id.return_value = MOCK_REPO_ID
    project_dir = rooted_tmp_path

    mocked_packages = [
        MockedPackage(
            Package(
                raw_locator="fsevents@npm:2.3.2",
                version="2.3.2",
                checksum="97ade64e75091afee5265e6956cb72ba34db7819b4c3e94c431d4be2b19b8bb7a2d4116da417950c3425f17c8fe693d25e20212cac583ac1521ad066b77ae31f",
                cache_path=project_dir.join_within_root(
                    ".yarn/cache/fsevents-npm-2.3.2-a881d6ac9f-97ade64e75.zip"
                ).path.as_posix(),
            ),
            is_hardlink=True,
        ),
        MockedPackage(
            Package(
                raw_locator="fsevents@patch:fsevents@npm%3A2.3.2#./my-patches/fsevents.patch&./my-patches/fsevents-2.patch::version=2.3.2&hash=cf0bf0&locator=berryscary%40workspace%3A.",
                version="2.3.2",
                checksum="f73215b04b52395389a612af4d30f7f412752cdfba1580c9e32c7ec259e448b57b464a4d0474427d6142f5ed9a6260fc1841d61834caf44706d77874fba6f17f",
                cache_path=project_dir.join_within_root(
                    ".yarn/cache/fsevents-patch-9d1204d729-f73215b04b.zip"
                ).path.as_posix(),
            ),
            is_hardlink=True,
        ),
    ]

    components = create_components(
        [mocked_package.package for mocked_package in mocked_packages],
        mock_project(project_dir),
        output_dir=RootedPath("/unused"),
    )

    expect_components = [
        Component(
            name="fsevents",
            version="2.3.2",
            purl="pkg:npm/fsevents@2.3.2",
            pedigree=Pedigree(
                patches=[
                    Patch(
                        type="unofficial",
                        diff=PatchDiff(
                            url="git+https://github.com/org/project.git@fffffff#my-patches/fsevents.patch"
                        ),
                    ),
                    Patch(
                        type="unofficial",
                        diff=PatchDiff(
                            url="git+https://github.com/org/project.git@fffffff#my-patches/fsevents-2.patch"
                        ),
                    ),
                ],
            ),
        ),
    ]

    assert components == expect_components


@pytest.mark.parametrize(
    "mocked_package, expect_err_msg",
    [
        # No package.json for a Workspace
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="armaments@workspace:./book/armaments",
                    version=None,
                    checksum=None,
                    cache_path=None,
                ),
                is_hardlink=False,
                packjson_path=None,
            ),
            (
                "Failed to resolve the name and version for armaments@workspace:./book/armaments: "
                "missing book/armaments/package.json"
            ),
            id="workspace_no_package_json",
        ),
        # Invalid package.json
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="armaments@workspace:./book/armaments",
                    version=None,
                    checksum=None,
                    cache_path=None,
                ),
                is_hardlink=False,
                packjson_path="book/armaments/package.json",
                packjson_content="{invalid JSON}",
            ),
            (
                "Failed to resolve the name and version for armaments@workspace:./book/armaments: "
                "book/armaments/package.json: invalid JSON "
                "(Expecting property name enclosed in double quotes: line 1 column 2 (char 1))"
            ),
            id="invalid_package_json",
        ),
        # No package.json for a Portal package
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="antioch@portal:holy-hand-grenade::locator=armaments%40workspace%3A./book/armaments",
                    version=None,
                    checksum=None,
                    cache_path=None,
                ),
                is_hardlink=False,
                packjson_path=None,
            ),
            (
                "Failed to resolve the name and version for "
                "antioch@portal:holy-hand-grenade::locator=armaments%40workspace%3A./book/armaments: "
                "missing book/armaments/holy-hand-grenade/package.json"
            ),
            id="portal_no_package_json",
        ),
        # No "name" in package.json for a Portal package
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="antioch@portal:holy-hand-grenade::locator=armaments%40workspace%3A./book/armaments",
                    version=None,
                    checksum=None,
                    cache_path=None,
                ),
                is_hardlink=False,
                packjson_path="book/armaments/holy-hand-grenade/package.json",
                packjson_content="{}",
            ),
            (
                "Failed to resolve the name and version for "
                "antioch@portal:holy-hand-grenade::locator=armaments%40workspace%3A./book/armaments: "
                "book/armaments/holy-hand-grenade/package.json: no 'name' attribute"
            ),
            id="portal_no_name_in_package_json",
        ),
        # No cache_path for a File package
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
                    version="4.0.0",
                    checksum="d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
                    cache_path=None,
                ),
                is_hardlink=True,
            ),
            (
                "Failed to resolve the name and version for "
                "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.: "
                "expected a zip archive in the cache but 'yarn info' says there is none"
            ),
            id="file_no_cache_path",
        ),
        # Missing package.json in cache archive
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
                    version="4.0.0",
                    checksum="d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
                    cache_path="cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip",
                ),
                is_hardlink=True,
            ),
            (
                "Failed to resolve the name and version for "
                "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.: "
                "cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip: no package.json"
            ),
            id="cache_archive_no_package_json",
        ),
        # Invalid package.json in cache archive
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
                    version="4.0.0",
                    checksum="d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
                    cache_path="cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip",
                ),
                is_hardlink=True,
                packjson_path="node_modules/strip-ansi-tarball/package.json",
                packjson_content="{invalid JSON}",
            ),
            (
                "Failed to resolve the name and version for "
                "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.: "
                "cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip::node_modules/strip-ansi-tarball/package.json: "
                "invalid JSON (Expecting property name enclosed in double quotes: line 1 column 2 (char 1))"
            ),
            id="cache_archive_invalid_package_json",
        ),
        # No "name" in package.json in cache archive
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
                    version="4.0.0",
                    checksum="d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
                    cache_path="cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip",
                ),
                is_hardlink=True,
                packjson_path="node_modules/strip-ansi-tarball/package.json",
                packjson_content="{}",
            ),
            (
                "Failed to resolve the name and version for "
                "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.: "
                "cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip::node_modules/strip-ansi-tarball/package.json: "
                "no 'name' attribute"
            ),
            id="cache_archive_no_name_in_package_json",
        ),
        # No cache_path for an Https package
        pytest.param(
            MockedPackage(
                Package(
                    raw_locator="@cachito/c2-wo-deps-2@https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz",
                    version="2.0.0",
                    checksum="b194fd1f4a79472a332fec936818d1713a222157e845a8d466a239fdc950130a7ad9b77c212d69d2947c07bce0c911446496ff47dec5a73b4368f0a9c9432b1d",
                    cache_path=None,
                ),
                is_hardlink=True,
            ),
            (
                "Failed to resolve the name and version for "
                "@cachito/c2-wo-deps-2@https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz: "
                "expected a zip archive in the cache but 'yarn info' says there is none"
            ),
            id="https_no_cache_path",
        ),
    ],
)
def test_create_components_failed_to_resolve(
    mocked_package: MockedPackage,
    expect_err_msg: str,
    rooted_tmp_path: RootedPath,
) -> None:
    project_dir = rooted_tmp_path
    mocked_package = mocked_package.resolve_cache_path(project_dir)
    mock_package_json(mocked_package, project_dir)

    with pytest.raises(PackageRejected, match=re.escape(expect_err_msg)):
        create_components(
            [mocked_package.package],
            mock_project(project_dir),
            output_dir=RootedPath("/unused"),
        )


def test_create_components_cache_path_reported_but_missing(rooted_tmp_path: RootedPath) -> None:
    package = Package(
        raw_locator="strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
        version="4.0.0",
        checksum="d67629c87783bc1138a64f6495439b40f568424a05e068c341b4fc330745e8ba6e7f93536549883054c1da58761f0ce6ab039a233014b38240304d3c45f85ac6",
        cache_path=rooted_tmp_path.join_within_root(
            "cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip"
        ).path.as_posix(),
    )

    expect_err_msg = (
        "Failed to resolve the name and version for "
        "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.: "
        "cache archive does not exist: cache/directory/strip-ansi-tarball-file-489a50cded-d67629c877.zip"
    )

    with pytest.raises(PackageRejected, match=re.escape(expect_err_msg)):
        create_components(
            [package],
            mock_project(rooted_tmp_path),
            output_dir=RootedPath("/unused"),
        )


@pytest.mark.parametrize(
    "patch_locators_spec, expected_urls",
    [
        pytest.param(
            [
                {
                    "package": ("fsevents", "1.0.0"),
                    "patches": ["./my-patches/fsevents.patch", "./my-patches/fsevents-2.patch"],
                    "locator": ("foo-project", "."),
                },
                {
                    "package": "PREVIOUS",  # base patch marker
                    "patches": ["./my-patches/fsevents-3.patch"],
                    "locator": ("foo-project", "."),
                },
                {
                    "package": "PREVIOUS",  # base patch marker
                    "patches": ["builtin<compat/fsevents>"],
                    "locator": None,
                },
            ],
            [
                "git+https://github.com/org/project.git@fffffff#my-patches/fsevents.patch",
                "git+https://github.com/org/project.git@fffffff#my-patches/fsevents-2.patch",
                "git+https://github.com/org/project.git@fffffff#my-patches/fsevents-3.patch",
                "git+https://github.com/yarnpkg/berry@%40yarnpkg/cli/3.0.0#packages/plugin-compat/sources/patches/fsevents.patch.ts",
            ],
            id="nested_patches_with_workspace",
        ),
        pytest.param(
            [
                {
                    "package": ("fsevents", "1.0.0"),
                    "patches": ["~/my-patches/fsevents.patch"],
                    "locator": None,
                },
            ],
            [
                "git+https://github.com/org/project.git@fffffff#my-patches/fsevents.patch",
            ],
            id="project_relative_patch_without_locator",
        ),
        pytest.param(
            [
                {
                    "package": ("fsevents", "1.0.0"),
                    "patches": ["{source_dir}/absolute-patches/fsevents.patch"],
                    "locator": None,
                },
            ],
            [
                "git+https://github.com/org/project.git@fffffff#absolute-patches/fsevents.patch",
            ],
            id="absolute_patch_without_locator",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.yarn.resolver.get_repo_id")
@mock.patch("hermeto.core.package_managers.yarn.resolver.extract_yarn_version_from_env")
def test_get_pedigree(
    mock_get_yarn_version: mock.Mock,
    mock_get_repo_id: mock.Mock,
    rooted_tmp_path: RootedPath,
    patch_locators_spec: list[dict[str, Any]],
    expected_urls: list[str],
) -> None:
    mock_get_yarn_version.return_value = Version(3, 0, 0)
    mock_get_repo_id.return_value = MOCK_REPO_ID

    source_dir = rooted_tmp_path.re_root("source")

    # Build patch locators from specs
    patch_locators: list[PatchLocator] = []
    base_package: Optional[NpmLocator] = None

    for spec in patch_locators_spec:
        if spec["package"] == "PREVIOUS":
            # Wrap previous PatchLocator as package
            package: Locator = patch_locators[-1]
        else:
            name, version = spec["package"]
            package = NpmLocator(None, name, version)
            if base_package is None:
                base_package = package

        patches: list[Union[str, Path]] = []
        for p in spec["patches"]:
            p_resolved = p.replace("{source_dir}", str(source_dir.join_within_root("").path))
            patches.append(
                Path(p_resolved) if not p_resolved.startswith("builtin<") else p_resolved
            )

        workspace_locator: Optional[WorkspaceLocator] = None
        if spec["locator"]:
            name, relpath = spec["locator"]
            workspace_locator = WorkspaceLocator(None, name, Path(relpath))

        patch_loc = PatchLocator(package, patches, workspace_locator)
        patch_locators.append(patch_loc)

    # Build expected pedigree
    assert base_package is not None
    expected_pedigree: dict[Locator, Pedigree] = {
        base_package: Pedigree(
            patches=[Patch(type="unofficial", diff=PatchDiff(url=url)) for url in expected_urls]
        )
    }

    mock_project = mock.Mock(source_dir=source_dir)
    resolver = _ComponentResolver(
        {}, patch_locators, mock_project, rooted_tmp_path.re_root("output")
    )

    actual_pedigree = resolver._get_pedigree_mapping(patch_locators)
    assert actual_pedigree == expected_pedigree


@pytest.mark.parametrize(
    "patch",
    [
        pytest.param(
            Path("foo.patch"),
            id="path_patch_without_workspace",
        ),
        pytest.param(
            "builtin<bogus/patch>",
            id="builtin_patch_from_unknown_plugin",
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.yarn.resolver.get_repo_id")
@mock.patch("hermeto.core.package_managers.yarn.resolver.extract_yarn_version_from_env")
def test_get_pedigree_with_unsupported_locators(
    mock_get_yarn_version: mock.Mock,
    mock_get_repo_id: mock.Mock,
    patch: Union[Path, str],
    rooted_tmp_path: RootedPath,
) -> None:
    mock_get_yarn_version.return_value = Version(3, 0, 0)
    mock_get_repo_id.return_value = MOCK_REPO_ID

    patch_locators = [PatchLocator(NpmLocator(None, "foo", "1.0.0"), [patch], None)]
    mock_project = mock.Mock(source_dir=rooted_tmp_path.re_root("source"))

    with pytest.raises(UnsupportedFeature):
        _ComponentResolver({}, patch_locators, mock_project, rooted_tmp_path.re_root("output"))
