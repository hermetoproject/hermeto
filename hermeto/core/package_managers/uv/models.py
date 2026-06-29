# SPDX-License-Identifier: GPL-3.0-only
from collections.abc import Mapping
from typing import Any, Literal, get_args

import pydantic
import tomlkit
from tomlkit.exceptions import ParseError

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound
from hermeto.core.rooted_path import RootedPath

SUPPORTED_LOCKFILE_VERSION = 1

UvSourceKind = Literal[
    "registry",
    "git",
    "url",
    "path",
    "directory",
    "editable",
    "virtual",
]

_SOURCE_KINDS: tuple[str, ...] = get_args(UvSourceKind)

# Sources that live in the project/workspace tree; they have no remote artifact
_LOCAL_KINDS = frozenset({"path", "directory", "editable", "virtual"})


def _normalize_source(raw: Mapping[str, Any]) -> dict[str, str]:
    """Translate a raw uv.lock source mapping into ``UvSource`` fields.

    uv.lock records the source as a single-key mapping, one key per kind, e.g.
    ``{"registry": "https://pypi.org/simple"}`` or ``{"git": "https://...#sha"}``.
    Collapse it into the explicit ``{"kind": ..., "location": ...}`` form the
    model stores. Extra keys (e.g. a git ``subdirectory``) are dropped: they are
    not needed to fetch or checksum (git sources are cloned in full).

    :raises ValueError: if the mapping does not hold exactly one known source key.
    """
    matched = [key for key in _SOURCE_KINDS if key in raw]
    if len(matched) != 1:
        raise ValueError(f"source must contain exactly one of {_SOURCE_KINDS}, got {sorted(raw)}")
    kind = matched[0]
    return {"kind": kind, "location": raw[kind]}


def _normalize_lock(data: Any) -> Any:
    """Return lockfile data with each package's raw ``source`` collapsed.

    Keeps ``UvSource`` a plain ``{kind, location}`` model: the raw single-key
    lockfile form is translated here, so the model never has to disambiguate it.
    A new structure is returned rather than mutating the loaded document;
    non-mapping input is passed through for pydantic to reject with a clear error.
    """
    if not isinstance(data, Mapping):
        return data

    packages = [
        {**package, "source": _normalize_source(package["source"])}
        if isinstance(package, Mapping) and isinstance(package.get("source"), Mapping)
        else package
        for package in data.get("package", [])
    ]
    return {**data, "package": packages}


class UvSource(pydantic.BaseModel):
    """The single resolved source of a uv.lock ``[[package]]``.

    Raw lockfile sources are normalized into ``{kind, location}`` by
    :func:`_normalize_source` before validation; see :meth:`UvLock.from_file`.
    """

    kind: UvSourceKind
    location: str

    @property
    def is_local(self) -> bool:
        """Whether the source lives in the project tree and so is not fetched."""
        return self.kind in _LOCAL_KINDS


class UvArtifact(pydantic.BaseModel, extra="ignore"):
    """A wheel or sdist recorded for a uv.lock ``[[package]]``.

    Remote artifacts (registry/url sources) carry a ``url``; locally-sourced
    artifacts (``path`` sources) carry a ``filename`` instead. ``hash`` may be
    absent when uv records no checksum.
    """

    url: str | None = None
    filename: str | None = None
    hash: str | None = None
    size: int | None = None

    @pydantic.model_validator(mode="after")
    def _require_url_or_filename(self) -> "UvArtifact":
        if not self.url and not self.filename:
            raise ValueError("artifact must have either a 'url' or a 'filename'")
        return self

    @property
    def checksum_info(self) -> ChecksumInfo | None:
        """Return the recorded checksum as a ChecksumInfo, or None if absent.
        """
        if self.hash is None:
            return None
        return ChecksumInfo.from_hash(self.hash)


class UvPackage(pydantic.BaseModel, extra="ignore"):
    """A single ``[[package]]`` entry in uv.lock.

    ``name`` and ``version`` are always present in uv.lock. Dependency edges,
    ``metadata``, ``optional-dependencies`` and ``dev-dependencies`` are present
    in the file but not modelled, as they are unused for fetch and verification.
    """

    name: str
    version: str
    source: UvSource
    sdist: UvArtifact | None = None
    wheels: list[UvArtifact] = pydantic.Field(default_factory=list)


class UvLock(pydantic.BaseModel, extra="ignore", populate_by_name=True):
    """A parsed uv.lock file.

    Only ``version`` and ``packages`` are modelled. ``revision`` is ignored
    because revisions are forward/backward compatible within a major version;
    ``requires-python``, ``resolution-markers``, ``manifest`` and ``options``
    are ignored as they are not needed to fetch artifacts.
    """

    version: int
    # uv.lock spells this ``package``; expose it as ``packages`` here.
    packages: list[UvPackage] = pydantic.Field(default_factory=list, alias="package")

    @pydantic.field_validator("version")
    @classmethod
    def _supported_version(cls, version: int) -> int:
        if version != SUPPORTED_LOCKFILE_VERSION:
            raise ValueError(
                f"unsupported uv.lock version: {version} "
                f"(only version {SUPPORTED_LOCKFILE_VERSION} is supported)"
            )
        return version

    @classmethod
    def from_file(cls, directory: RootedPath) -> "UvLock":
        """Find, load, parse and validate a uv.lock file from a directory.

        :raises InvalidLockfileFormat: if the file is not valid TOML, or does not
            match the expected uv.lock structure/version.
        :raises LockfileNotFound: if no uv.lock file is found in the directory.
        """
        path = directory.join_within_root("uv.lock")
        if not path.path.exists():
            raise LockfileNotFound(
                files=path.path,
                solution="Run `uv lock` in the project directory to generate uv.lock.",
            )

        with open(path) as f:
            try:
                data = tomlkit.load(f)
            except ParseError as e:
                raise InvalidLockfileFormat(
                    lockfile_path=path.path,
                    err_details="Invalid TOML syntax.",
                    solution="Regenerate the lockfile with `uv lock`.",
                ) from e

        try:
            return cls.model_validate(_normalize_lock(data))
        except ValueError as e:
            raise InvalidLockfileFormat(
                lockfile_path=path.path,
                err_details=str(e),
                solution="Regenerate the lockfile with `uv lock` (matching your uv version).",
            ) from e
