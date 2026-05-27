# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import git
import pytest

from hermeto.core.constants import Mode
from hermeto.core.errors import (
    FetchError,
    NotAGitRepo,
    UnexpectedFormat,
)
from hermeto.core.models.input import Request
from hermeto.core.models.sbom import (
    PROXY_COMMENT,
    PROXY_REF_TYPE,
    Component,
    ExternalReference,
)
from hermeto.core.package_managers.gomod.go import (
    Go,
    GoWork,
)
from hermeto.core.package_managers.gomod.main import (
    Module,
    ModuleDict,
    ModuleID,
    ModuleVersionResolver,
    Package,
    ParsedModule,
    ParsedOrigin,
    ParsedPackage,
    StandardPackage,
    _create_main_module_from_parsed_data,
    _create_modules_from_parsed_data,
    _create_packages_from_parsed_data,
    _deduplicate_resolved_modules,
    _get_proxy_for_module,
    _get_repository_name,
    _parse_go_sum,
    _parse_packages,
    _parse_vendor,
    _process_modules_json_stream,
    _resolve_gomod,
    _validate_local_replacements,
    _vendor_changed,
)
from hermeto.core.rooted_path import PathOutsideRoot, RootedPath
from hermeto.core.scm import GitRepo, RepoID
from hermeto.core.utils import GIT_PRISTINE_ENV
from tests.common_utils import GIT_REF, write_file_tree
from tests.unit.package_managers.gomod.helpers import get_mocked_data

GO_CMD_PATH = "/usr/bin/go"


@pytest.fixture(scope="module", autouse=True)
def mock_which_go() -> Iterator[None]:
    """Make shutil.which return GO_CMD_PATH for all the tests in this file.

    Whenever we execute a command, we use shutil.which to look for it first. To ensure
    that these tests don't depend on the state of the developer's machine, the returned
    go path must be mocked.
    """
    with mock.patch("shutil.which") as mock_which:
        mock_which.return_value = GO_CMD_PATH
        yield


@pytest.fixture(autouse=True)
def mock_go_release() -> Iterator[mock.MagicMock]:
    with mock.patch("hermeto.core.package_managers.gomod.go.Go._get_release") as _mock:
        # Using a side_effect instead of return_value because return_value always takes precedence
        # and we would not be able to override this easily.
        _mock.side_effect = lambda: "go1.21.0"
        yield _mock


@pytest.fixture
def gomod_input_packages() -> list[dict[str, str]]:
    return [{"type": "gomod"}]


@pytest.fixture
def gomod_request(tmp_path: Path, gomod_input_packages: list[dict[str, str]]) -> Request:
    # Create folder in the specified path, otherwise Request validation would fail
    for package in gomod_input_packages:
        if "path" in package:
            (tmp_path / package["path"]).mkdir(exist_ok=True)

    return Request(
        source_dir=tmp_path,
        output_dir=tmp_path / "output",
        packages=gomod_input_packages,
    )


@pytest.mark.parametrize(
    "symlinked_file",
    [
        "go.mod",
        "go.sum",
        "vendor/modules.txt",
        "some-package/foo.go",
        "vendor/github.com/foo/bar/main.go",
    ],
)
def test_resolve_gomod_suspicious_symlinks(symlinked_file: str, gomod_request: Request) -> None:
    tmp_path = gomod_request.source_dir.path
    tmp_path.joinpath(symlinked_file).parent.mkdir(parents=True, exist_ok=True)
    tmp_path.joinpath(symlinked_file).symlink_to("/foo")
    version_resolver = mock.Mock(spec=ModuleVersionResolver)
    go_work = mock.Mock(spec=GoWork)

    app_dir = gomod_request.source_dir

    with pytest.raises(PathOutsideRoot):
        _resolve_gomod(app_dir, gomod_request, tmp_path, version_resolver, Go(), go_work)


@pytest.mark.parametrize(
    "go_sum_content, expect_modules",
    [
        (None, set()),
        ("", set()),
        (
            textwrap.dedent(
                """
                github.com/creack/pty v1.1.18 h1:n56/Zwd5o6whRC5PMGretI4IdRLlmBXYNjScPaBgsbY=

                github.com/davecgh/go-spew v1.1.0/go.mod h1:J7Y8YcW2NihsgmVo/mv3lAwl/skON4iLHjSsI+c5H38=

                github.com/davecgh/go-spew v1.1.1 h1:vj9j/u1bqnvCEfJOwUhtlOARqs3+rkHYY13jYWTU97c=
                github.com/davecgh/go-spew v1.1.1/go.mod h1:J7Y8YcW2NihsgmVo/mv3lAwl/skON4iLHjSsI+c5H38=

                github.com/moby/term v0.0.0-20221205130635-1aeaba878587 h1:HfkjXDfhgVaN5rmueG8cL8KKeFNecRCXFhaJ2qZ5SKA=
                github.com/moby/term v0.0.0-20221205130635-1aeaba878587/go.mod h1:8FzsFHVUBGZdbDsJw/ot+X+d5HLUbvklYLJ9uGfcI3Y=
                """
            ),
            {
                ("github.com/creack/pty", "v1.1.18"),  # has the .zip checksum => include it
                # ("github.com/davecgh/go-spew", "v1.1.0"),  # only the .mod checksum => exclude it
                ("github.com/davecgh/go-spew", "v1.1.1"),
                ("github.com/moby/term", "v0.0.0-20221205130635-1aeaba878587"),
            },
        ),
    ],
)
def test_parse_go_sum(
    go_sum_content: str | None,
    expect_modules: set[ModuleID],
    rooted_tmp_path: RootedPath,
) -> None:
    go_sum_file = rooted_tmp_path.join_within_root("go.sum")

    if go_sum_content is not None:
        go_sum_file.path.write_text(go_sum_content)

    parsed_modules = _parse_go_sum(go_sum_file)
    assert frozenset(expect_modules) == parsed_modules


def test_parse_broken_go_sum(rooted_tmp_path: RootedPath, caplog: pytest.LogCaptureFixture) -> None:
    go_sum_content = textwrap.dedent(
        """\
        github.com/creack/pty v1.1.18 h1:n56/Zwd5o6whRC5PMGretI4IdRLlmBXYNjScPaBgsbY=
        github.com/davecgh/go-spew v1.1.0/go.mod
        github.com/davecgh/go-spew v1.1.1 h1:vj9j/u1bqnvCEfJOwUhtlOARqs3+rkHYY13jYWTU97c=
        github.com/davecgh/go-spew v1.1.1/go.mod h1:J7Y8YcW2NihsgmVo/mv3lAwl/skON4iLHjSsI+c5H38=
        github.com/moby/term v0.0.0-20221205130635-1aeaba878587 h1:HfkjXDfhgVaN5rmueG8cL8KKeFNecRCXFhaJ2qZ5SKA=
        github.com/moby/term v0.0.0-20221205130635-1aeaba878587/go.mod h1:8FzsFHVUBGZdbDsJw/ot+X+d5HLUbvklYLJ9uGfcI3Y=
        """
    )
    expect_modules = frozenset([("github.com/creack/pty", "v1.1.18")])

    submodule = rooted_tmp_path.join_within_root("submodule")
    submodule.path.mkdir()
    go_sum_file = submodule.join_within_root("go.sum")
    go_sum_file.path.write_text(go_sum_content)

    assert _parse_go_sum(go_sum_file) == expect_modules
    assert caplog.messages == [
        "submodule/go.sum:2: malformed line, skipping the rest of the file: 'github.com/davecgh/go-spew v1.1.0/go.mod'",
    ]


@pytest.mark.parametrize(
    "project_path, stream, expected_modules",
    (
        pytest.param(
            "/home/my-projects/simple-project",
            textwrap.dedent(
                """
                {
                    "Path": "github.com/my-org/simple-project",
                    "Main": true,
                    "Dir": "/home/my-projects/simple-project",
                    "GoMod": "/home/my-projects/simple-project/go.mod",
                    "GoVersion": "1.19"
                }
                """
            ),
            (
                {
                    "Path": "github.com/my-org/simple-project",
                    "Main": True,
                    "Dir": "/home/my-projects/simple-project",
                    "GoMod": "/home/my-projects/simple-project/go.mod",
                    "GoVersion": "1.19",
                },
                [],
            ),
            id="no_workspaces",
        ),
        pytest.param(
            "/home/my-projects/project-with-workspaces",
            textwrap.dedent(
                """
                {
                    "Path": "github.com/my-org/project-with-workspaces",
                    "Main": true,
                    "Dir": "/home/my-projects/project-with-workspaces",
                    "GoMod": "/home/my-projects/project-with-workspaces/go.mod",
                    "GoVersion": "1.19"
                }
                {
                    "Path": "github.com/my-org/work",
                    "Main": true,
                    "Dir": "/home/my-projects/project-with-workspaces/work",
                    "GoMod": "/home/my-projects/project-with-workspaces/work/go.mod"
                }
                {
                    "Path": "github.com/my-org/space",
                    "Main": true,
                    "Dir": "/home/my-projects/project-with-workspaces/space",
                    "GoMod": "/home/my-projects/project-with-workspaces/space/go.mod"
                }
                """
            ),
            (
                {
                    "Path": "github.com/my-org/project-with-workspaces",
                    "Main": True,
                    "Dir": "/home/my-projects/project-with-workspaces",
                    "GoMod": "/home/my-projects/project-with-workspaces/go.mod",
                    "GoVersion": "1.19",
                },
                [
                    {
                        "Path": "github.com/my-org/work",
                        "Main": True,
                        "Dir": "/home/my-projects/project-with-workspaces/work",
                        "GoMod": "/home/my-projects/project-with-workspaces/work/go.mod",
                    },
                    {
                        "Path": "github.com/my-org/space",
                        "Main": True,
                        "Dir": "/home/my-projects/project-with-workspaces/space",
                        "GoMod": "/home/my-projects/project-with-workspaces/space/go.mod",
                    },
                ],
            ),
            id="with_workspaces",
        ),
    ),
)
def test_process_modules_json_stream(
    project_path: str,
    stream: str,
    expected_modules: tuple[ModuleDict, list[ModuleDict]],
) -> None:
    app_dir = RootedPath(project_path)
    result = _process_modules_json_stream(app_dir, stream)

    assert result == expected_modules


@pytest.mark.parametrize("has_workspaces", (False, True))
@mock.patch("hermeto.core.package_managers.gomod.main.ModuleVersionResolver")
def test_create_modules_from_parsed_data(
    mock_version_resolver: mock.Mock,
    has_workspaces: bool,
    rooted_tmp_path: RootedPath,
) -> None:
    main_module_dir = rooted_tmp_path.join_within_root("target-module")
    mock_version_resolver.get_golang_version.return_value = "v1.5.0"

    go_work = None

    main_module = Module(
        name="github.com/my-org/my-repo/target-module",
        version="v1.5.0",
        original_name="github.com/my-org/my-repo/target-module",
        real_path="github.com/my-org/my-repo/target-module",
        main=True,
    )

    parsed_modules = [
        # simple module
        ParsedModule(
            path="golang.org/a/standard-module",
            version="v0.0.0-20190311183353-d8887717615a",
        ),
        # replaced module
        ParsedModule(
            path="github.com/a-neat-org/useful-module",
            version="v1.0.0",
            replace=ParsedModule(
                path="github.com/another-org/useful-module",
                version="v2.0.0",
            ),
        ),
        # locally replaced module, child folder
        ParsedModule(
            path="github.com/some-org/this-other-module",
            version="v0.0.1",
            replace=ParsedModule(
                path="./local-path",
            ),
        ),
        # locally replaced module, sibling folder
        ParsedModule(
            path="github.com/some-org/yet-another-module",
            version="v0.1.0",
            replace=ParsedModule(
                path="../sibling-path",
            ),
        ),
    ]

    modules_in_go_sum = frozenset(
        [
            ("golang.org/a/standard-module", "v0.0.0-20190311183353-d8887717615a"),
            # another-org/useful-module is missing
        ]
    )

    expect_modules = [
        Module(
            name="golang.org/a/standard-module",
            version="v0.0.0-20190311183353-d8887717615a",
            original_name="golang.org/a/standard-module",
            real_path="golang.org/a/standard-module",
        ),
        Module(
            name="github.com/another-org/useful-module",
            version="v2.0.0",
            original_name="github.com/a-neat-org/useful-module",
            real_path="github.com/another-org/useful-module",
            missing_hash_in_file=Path("target-module/go.sum"),
        ),
        Module(
            name="github.com/some-org/this-other-module",
            version="v1.5.0",
            original_name="github.com/some-org/this-other-module",
            real_path="github.com/my-org/my-repo/target-module/local-path",
        ),
        Module(
            name="github.com/some-org/yet-another-module",
            version="v1.5.0",
            original_name="github.com/some-org/yet-another-module",
            real_path="github.com/my-org/my-repo/sibling-path",
        ),
    ]

    if has_workspaces:
        go_work = mock.MagicMock(spec=GoWork)
        go_work.__bool__.return_value = True
        go_work_path = rooted_tmp_path.join_within_root("workspace_dir/go.work")
        type(go_work).rooted_path = mock.PropertyMock(return_value=go_work_path)
        expect_modules[1] = Module(
            name="github.com/another-org/useful-module",
            version="v2.0.0",
            original_name="github.com/a-neat-org/useful-module",
            real_path="github.com/another-org/useful-module",
            missing_hash_in_file=Path("workspace_dir/go.work.sum"),
        )

    modules = _create_modules_from_parsed_data(
        main_module,
        main_module_dir,
        parsed_modules,
        modules_in_go_sum,
        mock_version_resolver,
        go_work,
    )

    assert modules == expect_modules


def test_module_to_component() -> None:
    expected_component = Component(
        name="github.com/another-org/nice-repo",
        version="v0.0.1",
        purl="pkg:golang/github.com/another-org/nice-repo@v0.0.1?type=module",
        external_references=[
            ExternalReference(
                url="https://goproxy.corp.example.com",
                type=PROXY_REF_TYPE,
                comment=PROXY_COMMENT,
            )
        ],
    )

    component = Module(
        name="github.com/another-org/nice-repo",
        version="v0.0.1",
        original_name="github.com/my-org/nice-repo",
        real_path="github.com/another-org/nice-repo",
        proxy=["https://goproxy.corp.example.com"],
    ).to_component()

    assert component == expected_component


@pytest.mark.parametrize(
    "proxy_url, has_origin, expected_proxies",
    [
        ("https://goproxy.corp.example.com,direct", True, None),
        ("https://proxy.golang.org,direct", False, None),
        ("https://goproxy.corp.example.com,direct", False, ["https://goproxy.corp.example.com"]),
        (
            "https://goproxy.corp.example.com,https://proxy.golang.org,direct",
            False,
            ["https://goproxy.corp.example.com", "https://proxy.golang.org"],
        ),
    ],
)
@mock.patch("hermeto.core.package_managers.gomod.main.get_config")
def test_get_proxy_for_module(
    mock_config: mock.Mock,
    proxy_url: str,
    has_origin: bool,
    expected_proxies: list[str] | None,
) -> None:
    mock_config.return_value.gomod.proxy_url = proxy_url
    origin = (
        ParsedOrigin(vcs="git", url="https://github.com/org/repo", hash="abc123")
        if has_origin
        else None
    )
    module = ParsedModule(path="github.com/org/repo", version="v1.0.0", origin=origin)

    assert _get_proxy_for_module(module) == expected_proxies


def test_create_packages_from_parsed_data() -> None:
    # modules as they'd be resolved from _create_modules_from_parsed_data
    modules = [
        Module(
            name="github.com/my-org/my-repo",
            version="v1.5.0",
            original_name="github.com/my-org/my-repo",
            real_path="github.com/my-org/my-repo",
            main=True,
        ),
        Module(
            name="github.com/my-org/my-repo/child-module",
            version="v1.0.1",
            original_name="github.com/my-org/my-repo/child-module",
            real_path="github.com/my-org/my-repo/child-module",
        ),
        Module(
            name="github.com/stretchr/testify",
            version="v1.7.1",
            original_name="github.com/stretchr/testify",
            real_path="github.com/stretchr/testify",
        ),
        Module(
            name="github.com/release-engineering/retrodep/v2",
            version="v2.0.0",
            original_name="github.com/containerbuildsystem/retrodep/v2",
            real_path="github.com/release-engineering/retrodep/v2",
        ),
    ]

    parsed_packages = [
        # std pkg
        ParsedPackage(
            import_path="internal/cpu",
            standard=True,
        ),
        # normal pkg
        ParsedPackage(
            import_path="github.com/stretchr/testify/assert",
            module=ParsedModule(path="github.com/stretchr/testify", version="v1.7.1"),
        ),
        # main module package
        ParsedPackage(
            import_path="github.com/my-org/my-repo",
            module=ParsedModule(path="github.com/my-org/my-repo", version="v1.5.0"),
        ),
        # package from a replaced module
        ParsedPackage(
            import_path="github.com/containerbuildsystem/retrodep/v2",
            module=ParsedModule(
                path="github.com/containerbuildsystem/retrodep/v2", version="v2.0.0"
            ),
        ),
        # package from a child module, with module reference missing
        ParsedPackage(
            import_path="github.com/my-org/my-repo/child-module/child-pkg",
        ),
    ]

    expect_packages = [
        StandardPackage(name="internal/cpu"),
        Package(
            relative_path="assert",
            module=Module(
                name="github.com/stretchr/testify",
                version="v1.7.1",
                original_name="github.com/stretchr/testify",
                real_path="github.com/stretchr/testify",
            ),
        ),
        Package(
            relative_path="",
            module=Module(
                name="github.com/my-org/my-repo",
                version="v1.5.0",
                original_name="github.com/my-org/my-repo",
                real_path="github.com/my-org/my-repo",
                main=True,
            ),
        ),
        Package(
            relative_path="",
            module=Module(
                name="github.com/release-engineering/retrodep/v2",
                version="v2.0.0",
                original_name="github.com/containerbuildsystem/retrodep/v2",
                real_path="github.com/release-engineering/retrodep/v2",
            ),
        ),
        Package(
            relative_path="child-pkg",
            module=Module(
                name="github.com/my-org/my-repo/child-module",
                version="v1.0.1",
                original_name="github.com/my-org/my-repo/child-module",
                real_path="github.com/my-org/my-repo/child-module",
            ),
        ),
    ]

    packages = _create_packages_from_parsed_data(modules, parsed_packages)

    assert packages == expect_packages


def test_deduplicate_resolved_modules() -> None:
    # as reported by "go list -deps all"
    package_modules = [
        # local replacement
        ParsedModule(
            path="github.com/my-org/local-replacement",
            version="v1.0.0",
            replace=ParsedModule(path="./local-folder"),
        ),
        # dependency replacement
        ParsedModule(
            path="github.com/my-org/my-dep",
            version="v2.0.0",
            replace=ParsedModule(path="github.com/another-org/another-dep", version="v2.0.1"),
        ),
        # common dependency
        ParsedModule(
            path="github.com/awesome-org/neat-dep",
            version="v2.0.1",
        ),
    ]

    # as reported by "go mod download -json"
    downloaded_modules = [
        # duplicate of dependency replacement
        ParsedModule(
            path="github.com/another-org/another-dep",
            version="v2.0.1",
        ),
        # duplicate of common dependency
        ParsedModule(
            path="github.com/awesome-org/neat-dep",
            version="v2.0.1",
        ),
    ]

    dedup_modules = _deduplicate_resolved_modules(package_modules, downloaded_modules)

    expect_dedup_modules = {
        ParsedModule(
            path="github.com/my-org/local-replacement",
            version="v1.0.0",
            replace=ParsedModule(path="./local-folder"),
        ),
        ParsedModule(
            path="github.com/my-org/my-dep",
            version="v2.0.0",
            replace=ParsedModule(path="github.com/another-org/another-dep", version="v2.0.1"),
        ),
        ParsedModule(
            path="github.com/awesome-org/neat-dep",
            version="v2.0.1",
        ),
    }

    assert set(dedup_modules) == expect_dedup_modules


@pytest.mark.parametrize(
    "module_suffix, ref, expected, subpath",
    (
        # First commit with no tag
        (
            "",
            "78510c591e2be635b010a52a7048b562bad855a3",
            "v0.0.0-20191107200220-78510c591e2b",
            None,
        ),
        # No prior tag at all
        (
            "",
            "5a6e50a1f0e3ce42959d98b3c3a2619cb2516531",
            "v0.0.0-20191107202433-5a6e50a1f0e3",
            None,
        ),
        # Only a non-semver tag (v1)
        (
            "",
            "7911d393ab186f8464884870fcd0213c36ecccaf",
            "v0.0.0-20191107202444-7911d393ab18",
            None,
        ),
        # Directly maps to a semver tag (v1.0.0)
        ("", "d1b74311a7bf590843f3b58bf59ab047a6f771ae", "v1.0.0", None),
        # One commit after a semver tag (v1.0.0)
        (
            "",
            "e92462c73bbaa21540f7385e90cb08749091b66f",
            "v1.0.1-0.20191107202936-e92462c73bba",
            None,
        ),
        # A semver tag (v2.0.0) without the corresponding go.mod bump, which happens after a v1.0.0
        # semver tag
        (
            "",
            "61fe6324077c795fc81b602ee27decdf4a4cf908",
            "v1.0.1-0.20191107202953-61fe6324077c",
            None,
        ),
        # A semver tag (v2.1.0) after the go.mod file was bumped
        ("/v2", "39006a0b5b0654a299cc43f71e0dc1aa50c2bc72", "v2.1.0", None),
        # A pre-release semver tag (v2.2.0-alpha)
        ("/v2", "0b3468852566617379215319c0f4dfe7f5948a8f", "v2.2.0-alpha", None),
        # Two commits after a pre-release semver tag (v2.2.0-alpha)
        (
            "/v2",
            "863073fae6efd5e04bb972a05db0b0706ec8276e",
            "v2.2.0-alpha.0.20191107204050-863073fae6ef",
            None,
        ),
        # Directly maps to a semver non-annotated tag (v2.2.0)
        ("/v2", "709b220511038f443fe1b26ac09c3e6c06c9f7c7", "v2.2.0", None),
        # A non-semver tag (random-tag)
        (
            "/v2",
            "37cea8ddd9e6b6b81c7cfbc3223ce243c078388a",
            "v2.2.1-0.20191107204245-37cea8ddd9e6",
            None,
        ),
        # The go.mod file is bumped but there is no versioned commit
        (
            "/v2",
            "6c7249e8c989852f2a0ee0900378d55d8e1d7fe0",
            "v2.0.0-20191108212303-6c7249e8c989",
            None,
        ),
        # Three semver annotated tags on the same commit
        ("/v2", "a77e08ced4d6ae7d9255a1a2e85bd3a388e61181", "v2.2.5", None),
        # A non-annotated semver tag and an annotated semver tag
        ("/v2", "bf2707576336626c8bbe4955dadf1916225a6a60", "v2.3.3", None),
        # Two non-annotated semver tags
        ("/v2", "729d0e6d60317bae10a71fcfc81af69a0f6c07be", "v2.4.1", None),
        # Two semver tags, with one having the wrong major version and the other with the correct
        # major version
        ("/v2", "3decd63971ed53a5b7ff7b2ca1e75f3915e99cf2", "v2.5.0", None),
        # A semver tag that is incorrectly lower then the preceding semver tag
        ("/v2", "0dd249ad59176fee9b5451c2f91cc859e5ddbf45", "v2.0.1", None),
        # A commit after the incorrect lower semver tag
        (
            "/v2",
            "2883f3ddbbc811b112ff1fe51ba2ee7596ddbf24",
            "v2.5.1-0.20191118190931-2883f3ddbbc8",
            None,
        ),
        # Newest semver tag is applied to a submodule, but the root module is being processed
        (
            "/v2",
            "f3ee3a4a394fb44b055ed5710b8145e6e98c0d55",
            "v2.5.1-0.20211209210936-f3ee3a4a394f",
            None,
        ),
        # Submodule has a semver tag applied to it
        ("/v2", "f3ee3a4a394fb44b055ed5710b8145e6e98c0d55", "v2.5.1", "submodule"),
        # A commit after a submodule tag
        (
            "/v2",
            "cc6c9f554c0982786ff9e077c2b37c178e46828c",
            "v2.5.2-0.20211223131312-cc6c9f554c09",
            "submodule",
        ),
        # A commit with multiple tags in different submodules
        ("/v2", "5401bdd8a8ebfcccd2eea9451d407a5fdae6fc76", "v2.5.3", "submodule"),
        # Malformed semver tag, root module being processed
        ("/v2", "4a481f0bae82adef3ea6eae3d167af6e74499cb2", "v2.6.0", None),
        # Malformed semver tag, submodule being processed
        ("/v2", "4a481f0bae82adef3ea6eae3d167af6e74499cb2", "v2.6.0", "submodule"),
    ),
)
def test_get_golang_version(
    golang_repo_path: Path,
    module_suffix: str,
    ref: str,
    expected: str,
    subpath: str | None,
) -> None:
    module_name = f"github.com/mprahl/test-golang-pseudo-versions{module_suffix}"

    module_dir = RootedPath(golang_repo_path)
    repo = GitRepo(golang_repo_path)
    repo.git.checkout(ref)
    version_resolver = ModuleVersionResolver(repo, repo.commit(ref))

    if subpath:
        module_dir = module_dir.join_within_root(subpath)

    version = version_resolver.get_golang_version(module_name, module_dir)
    assert version == expected


def test_validate_local_replacements(tmpdir: Path) -> None:
    app_path = RootedPath(tmpdir).join_within_root("subpath")

    modules = [
        ParsedModule(
            path="example.org/foo", version="v1.0.0", replace=ParsedModule(path="./another-foo")
        ),
        ParsedModule(
            path="example.org/foo", version="v1.0.0", replace=ParsedModule(path="../sibling-foo")
        ),
    ]

    _validate_local_replacements(modules, app_path)


def test_invalid_local_replacements(tmpdir: Path) -> None:
    app_path = RootedPath(tmpdir)

    modules = [
        ParsedModule(
            path="example.org/foo", version="v1.0.0", replace=ParsedModule(path="../outside-repo")
        ),
    ]

    with pytest.raises(PathOutsideRoot):
        _validate_local_replacements(modules, app_path)


def test_parse_vendor(rooted_tmp_path: RootedPath, data_dir: Path) -> None:
    modules_txt = rooted_tmp_path.join_within_root("vendor/modules.txt")
    modules_txt.path.parent.mkdir(parents=True)
    modules_txt.path.write_text(get_mocked_data(data_dir, "vendored/modules.txt"))
    expect_modules = {
        ParsedModule(path="golang.org/x/text", version="v0.0.0-20170915032832-14c0d48ead0c"),
        ParsedModule(path="rsc.io/quote", version="v1.5.2"),
        ParsedModule(path="rsc.io/sampler", version="v1.3.0"),
    }
    assert set(_parse_vendor(rooted_tmp_path)) == expect_modules


@pytest.mark.parametrize(
    "file_content, expect_error_msg",
    [
        ("#invalid-line", "vendor/modules.txt: unexpected format: '#invalid-line'"),
        ("# main-module", "vendor/modules.txt: unexpected module line format: '# main-module'"),
        (
            "github.com/x/package",
            "vendor/modules.txt: package has no parent module: github.com/x/package",
        ),
    ],
)
def test_parse_vendor_unexpected_format(
    file_content: str, expect_error_msg: str, rooted_tmp_path: RootedPath
) -> None:
    vendor = rooted_tmp_path.join_within_root("vendor")
    vendor.path.mkdir()
    vendor.join_within_root("modules.txt").path.write_text(file_content)

    with pytest.raises(UnexpectedFormat, match=expect_error_msg):
        _parse_vendor(rooted_tmp_path)


@pytest.mark.parametrize("subpath", ["", "some/app/"])
@pytest.mark.parametrize(
    "vendor_before, vendor_changes, expected_change",
    [
        pytest.param({}, {}, None, id="no_vendoring"),
        pytest.param({"vendor": {"modules.txt": "foo v1.0.0\n"}}, {}, None, id="no_changes"),
        pytest.param(
            {},
            {"vendor": {"modules.txt": "foo v1.0.0\n"}},
            textwrap.dedent(
                """
                --- /dev/null
                +++ b/{subpath}vendor/modules.txt
                @@ -0,0 +1 @@
                +foo v1.0.0
                """
            ),
            id="modules_txt_added",
        ),
        pytest.param(
            {"vendor": {"modules.txt": "foo v1.0.0\n"}},
            {"vendor": {"modules.txt": "foo v2.0.0\n"}},
            textwrap.dedent(
                """
                --- a/{subpath}vendor/modules.txt
                +++ b/{subpath}vendor/modules.txt
                @@ -1 +1 @@
                -foo v1.0.0
                +foo v2.0.0
                """
            ),
            id="modules_txt_changes",
        ),
        pytest.param(
            {},
            {"vendor": {"some_file": "foo"}},
            textwrap.dedent(
                """
                A\t{subpath}vendor/some_file
                """
            ),
            id="a_file_was_added",
        ),
        pytest.param(
            {"vendor": {"some_file": "foo"}},
            {"vendor": {"some_file": "bar", "other_file": "baz"}},
            textwrap.dedent(
                """
                A\t{subpath}vendor/other_file
                M\t{subpath}vendor/some_file
                """
            ),
            id="multiple_changes",
        ),
        # vendor/ was added but only contains empty dirs => will be ignored
        pytest.param({}, {"vendor": {"empty_dir": {}}}, None, id="vendor_empty_dirs"),
        # change will be tracked even if vendor/ is .gitignore'd
        pytest.param(
            {".gitignore": "vendor/"},
            {"vendor": {"some_file": "foo"}},
            textwrap.dedent(
                """
                A\t{subpath}vendor/some_file
                """
            ),
            id="file_added_in_gitignored_vendor_dir",
        ),
    ],
)
def test_vendor_changed(
    subpath: str,
    vendor_before: dict[str, Any],
    vendor_changes: dict[str, Any],
    expected_change: str | None,
    rooted_tmp_path_repo: RootedPath,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = git.Repo(rooted_tmp_path_repo)

    app_dir = rooted_tmp_path_repo.join_within_root(subpath)
    os.makedirs(app_dir, exist_ok=True)

    write_file_tree(vendor_before, app_dir)
    repo.index.add([app_dir.join_within_root(path) for path in vendor_before])
    repo.index.commit("before vendoring", skip_hooks=True)

    write_file_tree(vendor_changes, app_dir, exist_ok=True)

    assert _vendor_changed(app_dir) == bool(expected_change)
    if expected_change:
        assert expected_change.format(subpath=subpath) in caplog.text

    # The _vendor_changed function should reset the `git add` => added files should not be tracked
    assert not repo.git.diff("--diff-filter", "A")


@pytest.mark.parametrize(
    "input_url",
    (
        "ssh://github.com/hermetoproject/integration-tests",
        "ssh://username@github.com/hermetoproject/integration-tests",
        "github.com:hermetoproject/integration-tests.git",
        "username@github.com:hermetoproject/integration-tests.git/",
        "https://github.com/hermetoproject/integration-tests",
        "https://github.com/hermetoproject/integration-tests.git",
        "https://github.com/hermetoproject/integration-tests.git/",
    ),
)
@mock.patch("hermeto.core.scm.GitRepo")
def test_get_repository_name(mock_git_repo: Any, input_url: str) -> None:
    expected_url = "github.com/hermetoproject/integration-tests"

    mocked_repo = mock.Mock()
    mocked_repo.remote.return_value.url = input_url
    mocked_repo.head.commit.hexsha = GIT_REF
    mock_git_repo.return_value = mocked_repo

    resolved_url = _get_repository_name(RootedPath("/my-folder/cloned-repo"))

    assert resolved_url == expected_url


@mock.patch("hermeto.core.package_managers.gomod.main.get_repo_id")
@mock.patch("hermeto.core.package_managers.gomod.main.get_config")
def test_get_repository_name_permissive_mode(
    mock_get_config: mock.Mock,
    mock_get_repo_id: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """Test that _get_repository_name returns None in PERMISSIVE mode when not a git repo."""
    mock_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.PERMISSIVE

    result = _get_repository_name(rooted_tmp_path)

    assert result is None


@mock.patch("hermeto.core.package_managers.gomod.main.get_repo_id")
@mock.patch("hermeto.core.package_managers.gomod.main.get_config")
def test_get_repository_name_strict_mode_raises_without_git_repo(
    mock_get_config: mock.Mock,
    mock_get_repo_id: mock.Mock,
    rooted_tmp_path: RootedPath,
) -> None:
    """Test that _get_repository_name re-raises NotAGitRepo in STRICT mode."""
    mock_get_repo_id.side_effect = NotAGitRepo("Not a git repo", solution="N/A")
    mock_get_config.return_value.mode = Mode.STRICT

    with pytest.raises(NotAGitRepo):
        _get_repository_name(rooted_tmp_path)


@mock.patch("hermeto.core.package_managers.gomod.main.get_repo_id")
@mock.patch("hermeto.core.package_managers.gomod.main.get_config")
def test_get_repository_name_permissive_mode_with_git_repo(
    mock_get_config: mock.Mock,
    mock_get_repo_id: mock.Mock,
    rooted_tmp_path_repo: RootedPath,
) -> None:
    """Test that _get_repository_name returns repo name in PERMISSIVE mode when git repo is available."""
    repo = git.Repo(rooted_tmp_path_repo)
    repo.create_remote("origin", "https://github.com/org/repo.git")

    repo_id = RepoID("https://github.com/org/repo.git", repo.head.commit.hexsha)
    mock_get_repo_id.return_value = repo_id
    mock_get_config.return_value.mode = Mode.PERMISSIVE

    result = _get_repository_name(rooted_tmp_path_repo)

    assert result == "github.com/org/repo"


def test_create_main_module_from_parsed_data_repo_name_none(
    rooted_tmp_path: RootedPath,
) -> None:
    """PERMISSIVE mode without a git repo: resolved_path falls back to the module path."""
    parsed_main_module = ParsedModule(path="example.com/org/myapp", version="v1.2.3")
    main_module_dir = rooted_tmp_path  # subpath_from_root == "."

    module = _create_main_module_from_parsed_data(
        main_module_dir=main_module_dir,
        repo_name=None,
        parsed_main_module=parsed_main_module,
    )

    assert module.real_path == "example.com/org/myapp"
    assert module.name == "example.com/org/myapp"
    assert module.version == "v1.2.3"


@pytest.fixture
def repo_remote_with_tag(rooted_tmp_path: RootedPath) -> tuple[RootedPath, RootedPath]:
    """
    Return the Paths to two Repos, with the first configured as the remote of the second.

    There are different git tags applied to the first and second commits of the README file
    """
    local_repo_path = rooted_tmp_path.join_within_root("local")
    remote_repo_path = rooted_tmp_path.join_within_root("remote")
    readme_file_path = remote_repo_path.join_within_root("README.md")

    local_repo_path.path.mkdir()
    remote_repo_path.path.mkdir()
    remote_repo = git.Repo.init(remote_repo_path)

    with open(readme_file_path, "wb"):
        pass
    remote_repo.index.add([readme_file_path])
    initial_commit = remote_repo.index.commit("Add README")

    with open(readme_file_path, "w") as f:
        f.write("This is a README")
    remote_repo.index.add([readme_file_path])
    remote_repo.index.commit("Update README")

    git.Repo.clone_from(remote_repo_path, local_repo_path)

    remote_repo.create_tag("v1.0.0", ref=initial_commit, env=GIT_PRISTINE_ENV)
    remote_repo.create_tag("v2.0.0", env=GIT_PRISTINE_ENV)

    return remote_repo_path, local_repo_path


def test_fetch_tags(repo_remote_with_tag: tuple[RootedPath, RootedPath]) -> None:
    _, local_repo_path = repo_remote_with_tag
    assert git.Repo(local_repo_path).tags == []
    version_resolver = ModuleVersionResolver.from_repo_path(local_repo_path)
    assert version_resolver._commit_tags == ["v2.0.0"]
    assert version_resolver._all_tags == ["v1.0.0", "v2.0.0"]


def test_fetch_tags_fail(repo_remote_with_tag: tuple[RootedPath, RootedPath]) -> None:
    # The remote_repo itself has no remote configured, so will fail when fetching tags
    remote_repo_path, _ = repo_remote_with_tag
    with pytest.raises(FetchError):
        ModuleVersionResolver.from_repo_path(remote_repo_path)


@pytest.mark.parametrize(
    "input_subdir, expected_outfile",
    [
        pytest.param("non-vendored", "resolve_gomod.json", id="without_workspaces"),
        pytest.param("workspaces", "resolve_gomod_workspaces.json", id="with_workspaces"),
    ],
)
@mock.patch("hermeto.core.package_managers.gomod.go.GoWork._get_go_work")
def test_parse_packages(
    mock_get_go_work: mock.Mock,
    rooted_tmp_path: RootedPath,
    data_dir: Path,
    input_subdir: str,
    expected_outfile: str,
) -> None:
    """Test parsing of packages into ParsedPackage structures with real-like data.

    Note querying workspaces will return some data duplicated - that's expected.
    """
    go_work = None
    mocked_indata: str

    ws_paths: list = []
    mocked_outdata = json.loads(get_mocked_data(data_dir, f"expected-results/{expected_outfile}"))
    expected = {ParsedPackage(**package) for package in mocked_outdata["packages"]}

    go = mock.MagicMock(spec=Go)
    if input_subdir != "workspaces":
        mocked_indata = get_mocked_data(data_dir, f"{input_subdir}/go_list_deps_threedot.json")
        go.return_value = mocked_indata
    else:
        side_effects = []
        mock_get_go_work.return_value = get_mocked_data(data_dir, f"{input_subdir}/go_work.json")
        go_work = GoWork.from_file(rooted_tmp_path.join_within_root("go.work"), go)

        # add each <workspace_module>/go_list_deps_threedot.json as a side-effect to Go() execution
        ws_paths = go_work.workspace_paths
        for wp in ws_paths:
            wp_relative = wp.relative_to(go_work.path.parent)
            indata_relative = f"{input_subdir}/{wp_relative}/go_list_deps_threedot.json"
            mocked_indata = get_mocked_data(data_dir, indata_relative)
            side_effects.append(mocked_indata)

        go.side_effect = side_effects

    run_params = {"env": {"GOMODCACHE": "foo"}}
    pkgs = _parse_packages(go_work, go, run_params)

    calls = go.call_args_list
    if input_subdir != "workspaces":
        go.assert_called_once()
    else:
        calls = go.call_args_list
        assert go.call_count == len(ws_paths)
        assert all([run_params | {"cwd": ws_paths[i]} in c.args for i, c in enumerate(calls)])

    # _parse_packages calls _go_list_deps always with the './...' pattern
    assert all("./..." in call.args[0] for call in calls)
    assert set(pkgs) == expected
