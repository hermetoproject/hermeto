import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import NamedTuple, Optional, Union
from urllib.parse import parse_qs, unquote

from hermeto import APP_NAME
from hermeto.core.errors import UnexpectedFormat, UnsupportedFeature

# https://github.com/yarnpkg/berry/blob/b6026842dfec4b012571b5982bb74420c7682a73/packages/plugin-http/sources/constants.ts
TARBALL_RE = re.compile(r"^[^?]*\.(?:tar\.gz|tgz)(?:\?.*)?(?:#.*)?$")

# https://github.com/yarnpkg/berry/blob/8ff18d709a4211f92837ff2f59eaf4972ca579c0/packages/plugin-patch/sources/patchUtils.ts#L11
BUILTIN_REGEXP: re.Pattern[str] = re.compile(r"^builtin<([^>]+)>$")

# --- Locator types ---


class NpmLocator(NamedTuple):
    """Locator that handles registry dependencies.

    Sample locator string:
        [@scope/]name@npm:version

    Attributes:
        scope: the scope of the dependency (without '@')
        name: the name of the dependency
        version: the semver version of the dependency
    """

    scope: Optional[str]
    name: str
    version: str


class WorkspaceLocator(NamedTuple):
    """Locator that handles workspace dependencies.

    Sample locator string:
        [@scope/]name@workspace:path/to/dir

    Attributes:
        scope: the scope of the dependency (without '@')
        name: the name of the dependency
        relpath: relative path from the project root to the workspace
    """

    # The scope and name in a workspace locator seem reliable, yarnberry won't let you use an
    # arbitrary name (it must match the workspace's package.json)
    scope: Optional[str]
    name: str
    relpath: Path


class PatchLocator(NamedTuple):
    """Locator that handles patched dependencies.

    Sample locator string:
        [@scope/]name@patch:<patched-dep-locator>#path/to/patch::version=1.0.0&hash=abc123&locator=<workspace-locator>
        [@scope/]name@patch:<patched-dep-locator>#~builtin<compat/patch-name>::version=1.0.0&hash=abc123

    Attributes:
        package: the dependency that gets patched
        patches: a list of paths (relative to a parent locator) or builtin patch identifiers
            (e.g. "builtin<compat/typescript>")
        locator: relative paths (if any) are relative to this locator
    """

    # The scope and name in a patch locator (and most locators other than Npm and Workspace) are
    # not reliable. Yarnberry *will* let you use a completely arbitrary name.

    package: "Locator"
    patches: Sequence[Union[str, Path]]
    locator: Optional["WorkspaceLocator"]


class FileLocator(NamedTuple):
    """Locator that handles file dependencies.

    Sample locator string:
        [@scope/]name@file:path/to/tarball.tar.gz::locator=<workspace-locator>
        [@scope/]name@file:path/to/directory#path/to/directory::hash=321cba&locator=<workspace-locator>

    Attributes:
        relpath: relative path to a file or directory
        locator: the path is relative to this locator
    """

    relpath: Path
    locator: "WorkspaceLocator"


class PortalLocator(NamedTuple):
    """Locator that handles portal dependencies.

    Sample locator string:
        [@scope/]name@portal:path/to/directory::locator=<workspace-locator>

    Attributes:
        relpath: relative path to a file or directory
        locator: the path is relative to this locator
    """

    relpath: Path
    locator: "WorkspaceLocator"


class LinkLocator(NamedTuple):
    """Locator that handles link dependencies.

    Sample locator string:
        [@scope/]name@link:path/to/directory::locator=<workspace-locator>

    Nearly identical to portal dependencies with an important difference. Portal dependencies have
    a package.json, link dependencies do not (have to).

    Attributes:
        scope: the scope of the dependency (without '@')
        name: the name of the dependency
        relpath: relative path to a file or directory
        locator: the path is relative to this locator
    """

    # Link dependencies don't need to have a package.json, we have no choice but to depend on the
    # scope and name specified by the user.
    scope: Optional[str]
    name: str
    relpath: Path
    locator: "WorkspaceLocator"


class HttpsLocator(NamedTuple):
    """Locator that handles remote files fetched by HTTPS.

    Sample locator string:
        [@scope/]name@https://domain.com/url/to/file.tar.gz

    Attributes:
        url: the URL of the remote file
    """

    url: str


Locator = Union[
    NpmLocator,
    WorkspaceLocator,
    PatchLocator,
    FileLocator,
    PortalLocator,
    LinkLocator,
    HttpsLocator,
]


# --- Parsing locator types ---


def parse_locator(locator_str: str) -> Locator:
    """Parse a locator, determine its type and return the data relevant for said type.

    :raises UnexpectedFormat:
        if the locator or the reference in the locator doesn't match the expected format
        if the type of the locator cannot be determined
        if the locator doesn't follow the form we expect for the locator's type
    :raises UnsupportedFeature: if the locator has a type that we don't support
    """
    try:
        locator = _parse_locator(locator_str)
        parsed_reference = locator.parsed_reference
        protocol = (
            parsed_reference.protocol.removesuffix(":") if parsed_reference.protocol else None
        )

        if "commit" in parse_qs(parsed_reference.selector) or protocol == "exec":
            raise UnsupportedFeature(
                f"{APP_NAME} does not support Git or Exec dependencies for Yarn Berry: {locator_str}",
                docs=None,  # TODO: docs needed
            )
        elif protocol == "npm":
            return NpmLocator(locator.scope, locator.name, version=parsed_reference.selector)
        elif protocol == "workspace":
            relpath = Path(parsed_reference.selector)
            return WorkspaceLocator(locator.scope, locator.name, relpath)
        elif protocol == "patch":
            return _parse_patch_locator(locator)
        elif protocol == "file":
            return _parse_file_locator(locator)
        elif protocol == "portal":
            relpath, parent_locator = _parse_file_locator(locator)
            return PortalLocator(relpath, parent_locator)
        elif protocol == "link":
            relpath, parent_locator = _parse_file_locator(locator)
            return LinkLocator(locator.scope, locator.name, relpath, parent_locator)
        elif protocol in ("http", "https") and TARBALL_RE.match(locator.raw_reference):
            return HttpsLocator(url=locator.raw_reference)
    except UnexpectedFormat as e:
        raise UnexpectedFormat(f"parsing {locator_str!r}: {e}") from e

    raise UnexpectedFormat(f"parsing {locator_str!r}: unknown protocol")


def _parse_patch_locator(locator: "_ParsedLocator") -> PatchLocator:
    # https://github.com/yarnpkg/berry/blob/b6026842dfec4b012571b5982bb74420c7682a73/packages/plugin-patch/sources/patchUtils.ts#L13
    reference = locator.parsed_reference
    if not reference.source:
        raise UnexpectedFormat("missing source in locator")

    original_package = parse_locator(reference.source)

    def process_patch_path(patch: str) -> Union[str, Path]:
        # Almost verbatim from
        # https://github.com/yarnpkg/berry/blob/8ff18d709a4211f92837ff2f59eaf4972ca579c0/packages/plugin-patch/sources/patchUtils.ts#L122
        #
        # Yarn patches can be optional, where failing to apply the patch is not fatal, only a warning
        # `optional!' denotes an optional patch in Yarn v4
        # https://github.com/yarnpkg/berry/blob/93a56643ba3c813a87920dcf75c644eaf3b38e6f/packages/plugin-patch/sources/patchUtils.ts#L147
        # Flags are currently unused by Hermeto, but must be stripped for path parsing
        flag_index = patch.rfind("!")
        if flag_index != -1:
            path = patch[flag_index + 1 :]
        else:
            path = patch
        # '~' denotes an optional patch in Yarn v3; BUT '~/' can be part of the patch path
        # https://github.com/yarnpkg/berry/blob/b6026842dfec4b012571b5982bb74420c7682a73/packages/plugin-patch/sources/patchUtils.ts#L92
        if path.startswith("~") and not path.startswith("~/"):
            path = path.removeprefix("~")

        if BUILTIN_REGEXP.fullmatch(path):
            return path
        else:
            return Path(path)

    patches = tuple(process_patch_path(p) for p in reference.selector.split("&"))
    if locator_param := reference.get_param("locator"):
        parent_locator = parse_locator(locator_param)
        if not isinstance(parent_locator, WorkspaceLocator):
            raise UnsupportedFeature(
                f"{APP_NAME} only supports Patch dependencies bound to a WorkspaceLocator, "
                f"not to a(n) {type(parent_locator).__name__}: {locator}"
            )
    else:
        parent_locator = None

    return PatchLocator(original_package, patches, parent_locator)


def _parse_file_locator(locator: "_ParsedLocator") -> FileLocator:
    reference = locator.parsed_reference

    relpath = Path(reference.selector)
    # for 'file:' directories, Yarnberry uses the path as both source and selector
    # https://github.com/yarnpkg/berry/blob/b6026842dfec4b012571b5982bb74420c7682a73/packages/plugin-file/sources/fileUtils.ts#L16
    if reference.source and Path(reference.source) != relpath:
        raise UnexpectedFormat("conflicting paths in locator")

    locator_param = reference.get_param("locator")
    if not locator_param:
        raise UnexpectedFormat("missing 'locator' param")

    parent_locator = parse_locator(locator_param)
    if not isinstance(parent_locator, WorkspaceLocator):
        protocol = locator.parsed_reference.protocol or "file:"
        dep_type = protocol.removesuffix(":").title()
        raise UnsupportedFeature(
            f"{APP_NAME} only supports {dep_type} dependencies bound to a WorkspaceLocator, "
            f"not to a(n) {type(parent_locator).__name__}: {locator}"
        )
    return FileLocator(relpath, parent_locator)


# --- Parsing locators generically ---


# dataclass rather than NamedTuple because NamedTuple doesn't support cached_property
@dataclass(frozen=True)
class _ParsedLocator:
    scope: Optional[str]
    name: str
    raw_reference: str

    def __str__(self) -> str:
        name_at_ref = f"{self.name}@{self.raw_reference}"
        if self.scope:
            return f"@{self.scope}/{name_at_ref}"
        return name_at_ref

    @cached_property
    def parsed_reference(self) -> "_ParsedReference":
        return _parse_reference(self.raw_reference)


class _ParsedReference(NamedTuple):
    protocol: Optional[str]
    source: Optional[str]
    selector: str
    params: Optional[dict[str, list[str]]]

    def get_param(self, param_name: str) -> Optional[str]:
        if not self.params or not (param_value := self.params.get(param_name)):
            return None
        if len(param_value) != 1:
            raise UnexpectedFormat(f"expected 1 {param_name!r} param, got {len(param_value)}")
        return param_value[0]


def _parse_locator(locator_str: str) -> _ParsedLocator:
    # https://github.com/yarnpkg/berry/blob/b6026842dfec4b012571b5982bb74420c7682a73/packages/yarnpkg-core/sources/structUtils.ts#L411
    locator_re = re.compile(r"^(?:@([^/]+?)/)?([^@/]+?)(?:@(.+))$")
    match = locator_re.match(locator_str)
    if not match:
        raise UnexpectedFormat("could not parse locator (expected [@scope/]name@reference)")
    scope, name, reference = match.groups()
    return _ParsedLocator(scope, name, reference)


def _parse_reference(reference_str: str) -> _ParsedReference:
    """Parse a reference string.

    [@scope/]name@reference
                  ^^^^^^^^^

    References follow these forms:

        <protocol>:<selector>::<bindings>
        <protocol>:<source>#<selector>::<bindings>

    See https://github.com/yarnpkg/berry/blob/b6026842dfec4b012571b5982bb74420c7682a73/packages/yarnpkg-core/sources/structUtils.ts#L452
    """
    reference_re = re.compile(r"^([^#:]*:)?((?:(?!::)[^#])*)(?:#((?:(?!::).)*))?(?:::(.*))?$")
    match = reference_re.match(reference_str)
    if not match:
        raise UnexpectedFormat("could not parse reference")

    groups = match.groups()
    has_source = bool(groups[2])  # <protocol>:<source>#<selector>::<bindings>
    # doesn't have source:          <protocol>:<selector>::<bindings>

    protocol = groups[0]
    source = unquote(groups[1]) if has_source else None
    selector = unquote(groups[2]) if has_source else unquote(groups[1])
    bindings = parse_qs(groups[3]) if groups[3] else None

    return _ParsedReference(
        protocol,
        source,
        selector,
        # For some reason, Yarnberry calls them bindings in the docstring but params in code
        params=bindings,
    )
