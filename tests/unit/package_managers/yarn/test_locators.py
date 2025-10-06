import re
from itertools import zip_longest
from pathlib import Path

import pytest

from hermeto import APP_NAME
from hermeto.core.errors import UnexpectedFormat, UnsupportedFeature
from hermeto.core.package_managers.yarn.locators import (
    FileLocator,
    HttpsLocator,
    LinkLocator,
    Locator,
    NpmLocator,
    PatchLocator,
    PortalLocator,
    WorkspaceLocator,
    _parse_locator,
    _parse_reference,
    _ParsedLocator,
    _ParsedReference,
    parse_locator,
)

SUPPORTED_LOCATORS = [
    # scoped registry deps
    "@isaacs/cliui@npm:8.0.2",
    "@npmcli/fs@npm:3.1.0",
    # unscoped registry deps
    "abbrev@npm:1.1.1",
    "agent-base@npm:6.0.2",
    # workspaces
    "@montypython/brian@workspace:packages/the-life-of/brian",
    "the-answer@workspace:packages/the-answer",
    # file-like deps
    "ansi-regex-link@link:external-packages/ansi-regex::locator=berryscary%40workspace%3A.",
    "once-portal@portal:external-packages/once::locator=berryscary%40workspace%3A.",
    "supports-hyperlinks-folder@file:external-packages/supports-hyperlinks#external-packages/supports-hyperlinks::hash=cfa5f5&locator=berryscary%40workspace%3A.",
    "strip-ansi-tarball@file:../../external-packages/strip-ansi-4.0.0.tgz::locator=the-answer%40workspace%3Apackages%2Fthe-answer",
    "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
    # https dep
    "c2-wo-deps-2@https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz",
    # optional custom patch for a registry dep
    "left-pad@npm:1.3.0",
    "left-pad@patch:left-pad@npm%3A1.3.0#~./my-patches/left-pad.patch::version=1.3.0&hash=629bda&locator=berryscary%40workspace%3A.",
    "left-pad@patch:left-pad@npm%3A1.3.0#optional!./my-patches/left-pad.patch::version=1.3.0&hash=629bda&locator=berryscary%40workspace%3A.",
    # optional builtin patch
    "fsevents@npm:2.3.2",
    "fsevents@patch:fsevents@npm%3A2.3.2#~builtin<compat/fsevents>::version=2.3.2&hash=df0bf1",
    "fsevents@patch:fsevents@npm%3A2.3.2#optional!builtin<compat/fsevents>::version=2.3.2&hash=df0bf1",
    # patched patch dependency
    "fsevents@patch:fsevents@patch%3Afsevents@npm%253A2.3.2%23./my-patches/fsevents.patch%3A%3Aversion=2.3.2&hash=cf0bf0&locator=berryscary%2540workspace%253A.#~builtin<compat/fsevents>::version=2.3.2&hash=df0bf1",
    # non-optional builtin patch (in reality, the typescript patch is optional)
    "typescript@npm:5.1.6",
    "typescript@patch:typescript@npm%3A5.1.6#builtin<compat/typescript>::version=5.1.6&hash=5da071",
    # multiple patches of all kinds
    "is-positive@npm:3.1.0",
    "is-negative@patch:is-positive@npm%3A3.1.0#~builtin<foo>&./my-patches/is-positive.patch&builtin<bar>&~./baz.patch::version=3.1.0&locator=berryscary%40workspace%3A.",
    # project-relative patch without locator
    "left-pad@patch:left-pad@npm%3A1.3.0#~/patches/left-pad.patch::version=1.3.0&hash=629bda",
    # absolute patch without locator
    "left-pad@patch:left-pad@npm%3A1.3.0#/tmp/patches/left-pad.patch::version=1.3.0&hash=629bda",
    # multiple flags patch
    "left-pad@patch:left-pad@npm%3A1.3.0#optional!locator!./patches/left-pad.patch::version=1.3.0&hash=629bda&locator=berryscary%40workspace%3A.",
]

UNSUPPORTED_LOCATORS = [
    # exec dep
    "holy-hand-grenade@exec:./generate-holy-hand-grenade.js#./generate-holy-hand-grenade.js::hash=3b5cbd&locator=berryscary%40workspace%3A.",
    # git deps
    "c2-wo-deps@https://bitbucket.org/cachi-testing/cachi2-without-deps.git#commit=9e164b97043a2d91bbeb992f6cc68a3d1015086a",
    "ccto-wo-deps@git@github.com:cachito-testing/cachito-npm-without-deps.git#commit=2f0ce1d7b1f8b35572d919428b965285a69583f6",
    # specific workspace of a git dep
    "npm-lifecycle-scripts@https://github.com/chmeliik/js-lifecycle-scripts.git#workspace=my-workspace&commit=0e786c88d5aca79a68428dadaed4b096bf2ae3e0",
    # patched git dep
    "ccto-wo-deps@patch:ccto-wo-deps@git@github.com%3Acachito-testing/cachito-npm-without-deps.git%23commit=2f0ce1d7b1f8b35572d919428b965285a69583f6#./.yarn/patches/ccto-wo-deps-git@github.com-e0fce8c89c.patch::version=1.0.0&hash=51a91f&locator=berryscary%40workspace%3A.",
]

ALL_LOCATORS = SUPPORTED_LOCATORS + UNSUPPORTED_LOCATORS

PARSED_LOCATORS_AND_REFERENCES = [
    (
        _ParsedLocator(scope="isaacs", name="cliui", raw_reference="npm:8.0.2"),
        _ParsedReference(protocol="npm:", source=None, selector="8.0.2", params=None),
    ),
    (
        _ParsedLocator(scope="npmcli", name="fs", raw_reference="npm:3.1.0"),
        _ParsedReference(protocol="npm:", source=None, selector="3.1.0", params=None),
    ),
    (
        _ParsedLocator(scope=None, name="abbrev", raw_reference="npm:1.1.1"),
        _ParsedReference(protocol="npm:", source=None, selector="1.1.1", params=None),
    ),
    (
        _ParsedLocator(scope=None, name="agent-base", raw_reference="npm:6.0.2"),
        _ParsedReference(protocol="npm:", source=None, selector="6.0.2", params=None),
    ),
    (
        _ParsedLocator(
            scope="montypython", name="brian", raw_reference="workspace:packages/the-life-of/brian"
        ),
        _ParsedReference(
            protocol="workspace:", source=None, selector="packages/the-life-of/brian", params=None
        ),
    ),
    (
        _ParsedLocator(
            scope=None, name="the-answer", raw_reference="workspace:packages/the-answer"
        ),
        _ParsedReference(
            protocol="workspace:", source=None, selector="packages/the-answer", params=None
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="ansi-regex-link",
            raw_reference="link:external-packages/ansi-regex::locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="link:",
            source=None,
            selector="external-packages/ansi-regex",
            params={"locator": ["berryscary@workspace:."]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="once-portal",
            raw_reference="portal:external-packages/once::locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="portal:",
            source=None,
            selector="external-packages/once",
            params={"locator": ["berryscary@workspace:."]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="supports-hyperlinks-folder",
            raw_reference="file:external-packages/supports-hyperlinks#external-packages/supports-hyperlinks::hash=cfa5f5&locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="file:",
            source="external-packages/supports-hyperlinks",
            selector="external-packages/supports-hyperlinks",
            params={"hash": ["cfa5f5"], "locator": ["berryscary@workspace:."]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="strip-ansi-tarball",
            raw_reference="file:../../external-packages/strip-ansi-4.0.0.tgz::locator=the-answer%40workspace%3Apackages%2Fthe-answer",
        ),
        _ParsedReference(
            protocol="file:",
            source=None,
            selector="../../external-packages/strip-ansi-4.0.0.tgz",
            params={"locator": ["the-answer@workspace:packages/the-answer"]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="strip-ansi-tarball",
            raw_reference="file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="file:",
            source=None,
            selector="external-packages/strip-ansi-4.0.0.tgz",
            params={"locator": ["berryscary@workspace:."]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="c2-wo-deps-2",
            raw_reference="https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz",
        ),
        _ParsedReference(
            protocol="https:",
            source=None,
            selector="//bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz",
            params=None,
        ),
    ),
    (
        _ParsedLocator(scope=None, name="left-pad", raw_reference="npm:1.3.0"),
        _ParsedReference(protocol="npm:", source=None, selector="1.3.0", params=None),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="left-pad",
            raw_reference="patch:left-pad@npm%3A1.3.0#~./my-patches/left-pad.patch::version=1.3.0&hash=629bda&locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="patch:",
            source="left-pad@npm:1.3.0",
            selector="~./my-patches/left-pad.patch",
            params={
                "version": ["1.3.0"],
                "hash": ["629bda"],
                "locator": ["berryscary@workspace:."],
            },
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="left-pad",
            raw_reference="patch:left-pad@npm%3A1.3.0#optional!./my-patches/left-pad.patch::version=1.3.0&hash=629bda&locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="patch:",
            source="left-pad@npm:1.3.0",
            selector="optional!./my-patches/left-pad.patch",
            params={
                "version": ["1.3.0"],
                "hash": ["629bda"],
                "locator": ["berryscary@workspace:."],
            },
        ),
    ),
    (
        _ParsedLocator(scope=None, name="fsevents", raw_reference="npm:2.3.2"),
        _ParsedReference(protocol="npm:", source=None, selector="2.3.2", params=None),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="fsevents",
            raw_reference="patch:fsevents@npm%3A2.3.2#~builtin<compat/fsevents>::version=2.3.2&hash=df0bf1",
        ),
        _ParsedReference(
            protocol="patch:",
            source="fsevents@npm:2.3.2",
            selector="~builtin<compat/fsevents>",
            params={"version": ["2.3.2"], "hash": ["df0bf1"]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="fsevents",
            raw_reference="patch:fsevents@npm%3A2.3.2#optional!builtin<compat/fsevents>::version=2.3.2&hash=df0bf1",
        ),
        _ParsedReference(
            protocol="patch:",
            source="fsevents@npm:2.3.2",
            selector="optional!builtin<compat/fsevents>",
            params={"version": ["2.3.2"], "hash": ["df0bf1"]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="fsevents",
            raw_reference="patch:fsevents@patch%3Afsevents@npm%253A2.3.2%23./my-patches/fsevents.patch%3A%3Aversion=2.3.2&hash=cf0bf0&locator=berryscary%2540workspace%253A.#~builtin<compat/fsevents>::version=2.3.2&hash=df0bf1",
        ),
        _ParsedReference(
            protocol="patch:",
            source="fsevents@patch:fsevents@npm%3A2.3.2#./my-patches/fsevents.patch::version=2.3.2&hash=cf0bf0&locator=berryscary%40workspace%3A.",
            selector="~builtin<compat/fsevents>",
            params={"version": ["2.3.2"], "hash": ["df0bf1"]},
        ),
    ),
    (
        _ParsedLocator(scope=None, name="typescript", raw_reference="npm:5.1.6"),
        _ParsedReference(protocol="npm:", source=None, selector="5.1.6", params=None),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="typescript",
            raw_reference="patch:typescript@npm%3A5.1.6#builtin<compat/typescript>::version=5.1.6&hash=5da071",
        ),
        _ParsedReference(
            protocol="patch:",
            source="typescript@npm:5.1.6",
            selector="builtin<compat/typescript>",
            params={"version": ["5.1.6"], "hash": ["5da071"]},
        ),
    ),
    (
        _ParsedLocator(scope=None, name="is-positive", raw_reference="npm:3.1.0"),
        _ParsedReference(protocol="npm:", source=None, selector="3.1.0", params=None),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="is-negative",
            raw_reference="patch:is-positive@npm%3A3.1.0#~builtin<foo>&./my-patches/is-positive.patch&builtin<bar>&~./baz.patch::version=3.1.0&locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="patch:",
            source="is-positive@npm:3.1.0",
            selector="~builtin<foo>&./my-patches/is-positive.patch&builtin<bar>&~./baz.patch",
            params={"version": ["3.1.0"], "locator": ["berryscary@workspace:."]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="left-pad",
            raw_reference="patch:left-pad@npm%3A1.3.0#~/patches/left-pad.patch::version=1.3.0&hash=629bda",
        ),
        _ParsedReference(
            protocol="patch:",
            source="left-pad@npm:1.3.0",
            selector="~/patches/left-pad.patch",
            params={"version": ["1.3.0"], "hash": ["629bda"]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="left-pad",
            raw_reference="patch:left-pad@npm%3A1.3.0#/tmp/patches/left-pad.patch::version=1.3.0&hash=629bda",
        ),
        _ParsedReference(
            protocol="patch:",
            source="left-pad@npm:1.3.0",
            selector="/tmp/patches/left-pad.patch",
            params={"version": ["1.3.0"], "hash": ["629bda"]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="left-pad",
            raw_reference="patch:left-pad@npm%3A1.3.0#optional!locator!./patches/left-pad.patch::version=1.3.0&hash=629bda&locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="patch:",
            source="left-pad@npm:1.3.0",
            selector="optional!locator!./patches/left-pad.patch",
            params={
                "version": ["1.3.0"],
                "hash": ["629bda"],
                "locator": ["berryscary@workspace:."],
            },
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="holy-hand-grenade",
            raw_reference="exec:./generate-holy-hand-grenade.js#./generate-holy-hand-grenade.js::hash=3b5cbd&locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="exec:",
            source="./generate-holy-hand-grenade.js",
            selector="./generate-holy-hand-grenade.js",
            params={"hash": ["3b5cbd"], "locator": ["berryscary@workspace:."]},
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="c2-wo-deps",
            raw_reference="https://bitbucket.org/cachi-testing/cachi2-without-deps.git#commit=9e164b97043a2d91bbeb992f6cc68a3d1015086a",
        ),
        _ParsedReference(
            protocol="https:",
            source="//bitbucket.org/cachi-testing/cachi2-without-deps.git",
            selector="commit=9e164b97043a2d91bbeb992f6cc68a3d1015086a",
            params=None,
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="ccto-wo-deps",
            raw_reference="git@github.com:cachito-testing/cachito-npm-without-deps.git#commit=2f0ce1d7b1f8b35572d919428b965285a69583f6",
        ),
        _ParsedReference(
            protocol="git@github.com:",
            source="cachito-testing/cachito-npm-without-deps.git",
            selector="commit=2f0ce1d7b1f8b35572d919428b965285a69583f6",
            params=None,
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="npm-lifecycle-scripts",
            raw_reference="https://github.com/chmeliik/js-lifecycle-scripts.git#workspace=my-workspace&commit=0e786c88d5aca79a68428dadaed4b096bf2ae3e0",
        ),
        _ParsedReference(
            protocol="https:",
            source="//github.com/chmeliik/js-lifecycle-scripts.git",
            selector="workspace=my-workspace&commit=0e786c88d5aca79a68428dadaed4b096bf2ae3e0",
            params=None,
        ),
    ),
    (
        _ParsedLocator(
            scope=None,
            name="ccto-wo-deps",
            raw_reference="patch:ccto-wo-deps@git@github.com%3Acachito-testing/cachito-npm-without-deps.git%23commit=2f0ce1d7b1f8b35572d919428b965285a69583f6#./.yarn/patches/ccto-wo-deps-git@github.com-e0fce8c89c.patch::version=1.0.0&hash=51a91f&locator=berryscary%40workspace%3A.",
        ),
        _ParsedReference(
            protocol="patch:",
            source="ccto-wo-deps@git@github.com:cachito-testing/cachito-npm-without-deps.git#commit=2f0ce1d7b1f8b35572d919428b965285a69583f6",
            selector="./.yarn/patches/ccto-wo-deps-git@github.com-e0fce8c89c.patch",
            params={
                "version": ["1.0.0"],
                "hash": ["51a91f"],
                "locator": ["berryscary@workspace:."],
            },
        ),
    ),
]

PARSED_SUPPORTED_LOCATORS = [
    NpmLocator(scope="isaacs", name="cliui", version="8.0.2"),
    NpmLocator(scope="npmcli", name="fs", version="3.1.0"),
    NpmLocator(scope=None, name="abbrev", version="1.1.1"),
    NpmLocator(scope=None, name="agent-base", version="6.0.2"),
    WorkspaceLocator(scope="montypython", name="brian", relpath=Path("packages/the-life-of/brian")),
    WorkspaceLocator(scope=None, name="the-answer", relpath=Path("packages/the-answer")),
    LinkLocator(
        scope=None,
        name="ansi-regex-link",
        relpath=Path("external-packages/ansi-regex"),
        locator=WorkspaceLocator(scope=None, name="berryscary", relpath=Path(".")),
    ),
    PortalLocator(
        relpath=Path("external-packages/once"),
        locator=WorkspaceLocator(scope=None, name="berryscary", relpath=Path(".")),
    ),
    FileLocator(
        relpath=Path("external-packages/supports-hyperlinks"),
        locator=WorkspaceLocator(scope=None, name="berryscary", relpath=Path(".")),
    ),
    FileLocator(
        relpath=Path("../../external-packages/strip-ansi-4.0.0.tgz"),
        locator=WorkspaceLocator(
            scope=None, name="the-answer", relpath=Path("packages/the-answer")
        ),
    ),
    FileLocator(
        relpath=Path("external-packages/strip-ansi-4.0.0.tgz"),
        locator=WorkspaceLocator(scope=None, name="berryscary", relpath=Path(".")),
    ),
    HttpsLocator(
        url="https://bitbucket.org/cachi-testing/cachi2-without-deps-second/get/09992d418fc44a2895b7a9ff27c4e32d6f74a982.tar.gz"
    ),
    NpmLocator(scope=None, name="left-pad", version="1.3.0"),
    PatchLocator(
        package=NpmLocator(scope=None, name="left-pad", version="1.3.0"),
        patches=(Path("my-patches/left-pad.patch"),),
        locator=WorkspaceLocator(scope=None, name="berryscary", relpath=Path(".")),
    ),
    PatchLocator(
        package=NpmLocator(scope=None, name="left-pad", version="1.3.0"),
        patches=(Path("my-patches/left-pad.patch"),),
        locator=WorkspaceLocator(scope=None, name="berryscary", relpath=Path(".")),
    ),
    NpmLocator(scope=None, name="fsevents", version="2.3.2"),
    PatchLocator(
        package=NpmLocator(scope=None, name="fsevents", version="2.3.2"),
        patches=("builtin<compat/fsevents>",),
        locator=None,
    ),
    PatchLocator(
        package=NpmLocator(scope=None, name="fsevents", version="2.3.2"),
        patches=("builtin<compat/fsevents>",),
        locator=None,
    ),
    PatchLocator(
        package=PatchLocator(
            package=NpmLocator(scope=None, name="fsevents", version="2.3.2"),
            patches=(Path("my-patches/fsevents.patch"),),
            locator=WorkspaceLocator(scope=None, name="berryscary", relpath=Path(".")),
        ),
        patches=("builtin<compat/fsevents>",),
        locator=None,
    ),
    NpmLocator(scope=None, name="typescript", version="5.1.6"),
    PatchLocator(
        package=NpmLocator(scope=None, name="typescript", version="5.1.6"),
        patches=("builtin<compat/typescript>",),
        locator=None,
    ),
    NpmLocator(scope=None, name="is-positive", version="3.1.0"),
    PatchLocator(
        package=NpmLocator(scope=None, name="is-positive", version="3.1.0"),
        patches=(
            "builtin<foo>",
            Path("my-patches/is-positive.patch"),
            "builtin<bar>",
            Path("baz.patch"),
        ),
        locator=WorkspaceLocator(scope=None, name="berryscary", relpath=Path(".")),
    ),
    PatchLocator(
        package=NpmLocator(scope=None, name="left-pad", version="1.3.0"),
        patches=(Path("~/patches/left-pad.patch"),),
        locator=None,
    ),
    PatchLocator(
        package=NpmLocator(scope=None, name="left-pad", version="1.3.0"),
        patches=(Path("/tmp/patches/left-pad.patch"),),
        locator=None,
    ),
    PatchLocator(
        package=NpmLocator(scope=None, name="left-pad", version="1.3.0"),
        patches=(Path("patches/left-pad.patch"),),
        locator=WorkspaceLocator(scope=None, name="berryscary", relpath=Path(".")),
    ),
]


@pytest.mark.parametrize(
    "locator_str, expect_parsed_locator, expect_parsed_reference",
    [
        (locator_str, parsed_locator, parsed_reference)
        for locator_str, (parsed_locator, parsed_reference) in zip_longest(
            ALL_LOCATORS, PARSED_LOCATORS_AND_REFERENCES
        )
    ],
)
def test_parse_locator_helper(
    locator_str: str,
    expect_parsed_locator: _ParsedLocator,
    expect_parsed_reference: _ParsedReference,
) -> None:
    """Test the helpers that parse locators and references generically.

    Note that these don't care which locator types are supported and which aren't.
    """
    locator = _parse_locator(locator_str)
    assert locator == expect_parsed_locator
    assert locator.parsed_reference == expect_parsed_reference
    assert str(locator) == locator_str


@pytest.mark.parametrize(
    "locator_str",
    [
        "name",
        "@scope/name",
        "name@",
        "@scope/name@",
        "@reference",
        "@scope@reference",
        "@scope/@reference",
        "name/@reference",
    ],
)
def test_unexpected_locator_format(locator_str: str) -> None:
    with pytest.raises(UnexpectedFormat, match="could not parse locator"):
        _parse_locator(locator_str)


def test_unexpected_reference_format() -> None:
    with pytest.raises(UnexpectedFormat, match="could not parse reference"):
        # it is very difficult to find something that doesn't match, probably not doable without \n
        # kudos to hypothesis: https://hypothesis.readthedocs.io/en/latest/index.html
        # ^<empty protocol>:<no source><empty selector>::<empty params>$ - and 'x' is left unmatched
        _parse_reference(":::\nx")


@pytest.mark.parametrize(
    "locator_str, expect_locator", zip_longest(SUPPORTED_LOCATORS, PARSED_SUPPORTED_LOCATORS)
)
def test_parse_locator(locator_str: str, expect_locator: Locator) -> None:
    assert parse_locator(locator_str) == expect_locator
    # test that all locator types are hashable
    hash(expect_locator)


@pytest.mark.parametrize("locator_str", UNSUPPORTED_LOCATORS)
def test_parse_unsupported_locator(locator_str: str) -> None:
    with pytest.raises(
        UnsupportedFeature, match=f"{APP_NAME} does not support Git or Exec dependencies"
    ):
        parse_locator(locator_str)


@pytest.mark.parametrize(
    "locator_str",
    [
        "name@no-protocol",
        "name@yarn:1.0.0",
        "name@https://not-a-tarball.com",
        "name@git+ssh://no-commit-hash.com",
    ],
)
def test_parse_unknown_protocol(locator_str: str) -> None:
    with pytest.raises(
        UnexpectedFormat, match=re.escape(f"parsing {locator_str!r}: unknown protocol")
    ):
        parse_locator(locator_str)


@pytest.mark.parametrize(
    "locator_str, expect_err",
    [
        (
            "name@patch:#builtin<foo>",
            UnexpectedFormat("parsing 'name@patch:#builtin<foo>': missing source in locator"),
        ),
        (
            "name@patch:npm%3A1.0.0#builtin<foo>",
            UnexpectedFormat(
                "parsing 'name@patch:npm%3A1.0.0#builtin<foo>': "
                "parsing 'npm:1.0.0': could not parse locator (expected [@scope/]name@reference)"
            ),
        ),
        (
            "name@patch:name@npm%3A1.0.0#builtin<foo>::locator=workspace%3A.",
            UnexpectedFormat(
                "parsing 'name@patch:name@npm%3A1.0.0#builtin<foo>::locator=workspace%3A.': "
                "parsing 'workspace:.': could not parse locator (expected [@scope/]name@reference)"
            ),
        ),
        (
            "name@patch:name@npm%3A1.0.0#builtin<foo>::locator=foo&locator=bar",
            UnexpectedFormat(
                "parsing 'name@patch:name@npm%3A1.0.0#builtin<foo>::locator=foo&locator=bar': expected 1 'locator' param, got 2"
            ),
        ),
        (
            "name@patch:name@git@github.com/foo/bar%23commit=abcdef#builtin<foo>",
            UnsupportedFeature(
                f"{APP_NAME} does not support Git or Exec dependencies for Yarn Berry: name@git@github.com/foo/bar#commit=abcdef"
            ),
        ),
        (
            "name@patch:name@npm%3A1.0.0#./my-custom.patch::locator=name@npm%3A1.0.0",
            UnsupportedFeature(
                f"{APP_NAME} only supports Patch dependencies bound to a WorkspaceLocator, not to a(n) NpmLocator: "
                "name@patch:name@npm%3A1.0.0#./my-custom.patch::locator=name@npm%3A1.0.0"
            ),
        ),
    ],
)
def test_fail_to_parse_patch_locator(locator_str: str, expect_err: Exception) -> None:
    with pytest.raises(type(expect_err), match=re.escape(str(expect_err))):
        parse_locator(locator_str)


@pytest.mark.parametrize(
    "locator_str, expect_err",
    [
        (
            "name@file:./path/to/dir#./path/to/different/dir::locator=foo@workspace%3A.",
            UnexpectedFormat(
                "parsing 'name@file:./path/to/dir#./path/to/different/dir::locator=foo@workspace%3A.': conflicting paths in locator"
            ),
        ),
        (
            "name@file:./path/to/file.tar.gz",
            UnexpectedFormat("parsing 'name@file:./path/to/file.tar.gz': missing 'locator' param"),
        ),
        (
            "name@portal:./path/to/directory::locator=workspace%3A.",
            UnexpectedFormat(
                "parsing 'name@portal:./path/to/directory::locator=workspace%3A.': parsing 'workspace:.': could not parse locator"
            ),
        ),
        (
            "name@file:./path/to/file.tar.gz::locator=name@npm%3A1.0.0",
            UnsupportedFeature(
                f"{APP_NAME} only supports File dependencies bound to a WorkspaceLocator, not to a(n) NpmLocator: "
                "name@file:./path/to/file.tar.gz::locator=name@npm%3A1.0.0"
            ),
        ),
        (
            "name@portal:./path/to/directory::locator=name@npm%3A1.0.0",
            UnsupportedFeature(
                f"{APP_NAME} only supports Portal dependencies bound to a WorkspaceLocator, not to a(n) NpmLocator: "
                "name@portal:./path/to/directory::locator=name@npm%3A1.0.0"
            ),
        ),
    ],
)
def test_fail_to_parse_file_locator(locator_str: str, expect_err: Exception) -> None:
    with pytest.raises(type(expect_err), match=re.escape(str(expect_err))):
        parse_locator(locator_str)
