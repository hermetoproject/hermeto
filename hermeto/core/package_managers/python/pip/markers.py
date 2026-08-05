# SPDX-License-Identifier: GPL-3.0-only
"""Evaluate PEP 508 environment markers against user-supplied binary filters.

When a ``binary`` filter declares a target platform (arch/os/python), a
requirement whose environment marker excludes that platform (for example
``platform_machine != "ppc64le"``) would never be installed by pip. Hermeto must
therefore skip it instead of trying to resolve an artifact that does not exist,
which otherwise fails with ``PackageRejected`` for wheel-only locks that carry no
matching sdist hash.

See https://github.com/hermetoproject/hermeto/issues/1570

Approach: a marker is evaluated only when every environment variable it reads is
pinned by the filters; if it reads any dimension the filters do not pin, the
requirement is kept (fail-open). This "referenced-key" gate is deliberate rather
than relying on marker evaluation to fail on unpinned values: packaging routes
different operators through different code paths (e.g. ``x in y`` runs
``str.__contains__`` on the literal, and ``<`` / ``>`` on non-version keys are a
constant ``False``), so a sentinel value cannot reliably signal "unknown" for
every operator.

Design constraints:

* No runtime platform detection. The evaluation environment is built solely from
  the user's filters. ``packaging.markers.Marker.evaluate`` merges the supplied
  environment *over* ``default_environment()`` (the real host), so every
  ``default_environment()`` key must be present; unpinned keys use a poison value
  (never a host value).

* Fail open. For a hermetic build tool a false *skip* is the worst outcome (a
  silent ``ModuleNotFoundError`` at image build time), whereas a false *keep* is
  either wasted bandwidth or a loud, diagnosable ``PackageRejected``.
"""

import logging
from collections.abc import Iterator
from itertools import product

from packaging.markers import InvalidMarker, Marker, Variable, default_environment

from hermeto.core.binary_filters import parse_filter_spec
from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.package_managers.python.pip.requirements import PipRequirement

log = logging.getLogger(__name__)

# The set of environment keys packaging expects is fixed; capture it once so env
# construction never re-invokes host detection and there is no per-requirement
# cost. Only the key *names* are used -- every value is overwritten with a pin or
# a poison, so no host value ever leaks.
_DEFAULT_ENV_KEYS = tuple(default_environment())


# Hermeto ``os`` filter tokens mapped to the marker fields a pinned OS fully
# determines. ``os`` tokens are substring-matched against wheel platform *tags*
# elsewhere (a different string space: a linux target matches "manylinux"/"linux"
# tags, macOS matches "macosx" tags, Windows matches "win"), so they must be
# translated here rather than used verbatim as ``sys_platform``. The canonical
# tokens are the ones the docs use ("linux", "macosx"); the rest are accepted
# aliases. Tokens absent from this map leave these fields unpinned (fail-open
# keep) -- so being permissive here only ever makes marker evaluation more precise
# for whatever token the user actually passed.
_DARWIN_FIELDS = {"sys_platform": "darwin", "os_name": "posix", "platform_system": "Darwin"}
_WINDOWS_FIELDS = {"sys_platform": "win32", "os_name": "nt", "platform_system": "Windows"}
_OS_TO_MARKER_FIELDS = {
    "linux": {"sys_platform": "linux", "os_name": "posix", "platform_system": "Linux"},
    "macosx": _DARWIN_FIELDS,  # the documented macOS token; matches "macosx_*" tags
    "macos": _DARWIN_FIELDS,
    "osx": _DARWIN_FIELDS,
    "darwin": _DARWIN_FIELDS,
    "windows": _WINDOWS_FIELDS,
    "win32": _WINDOWS_FIELDS,
    "win": _WINDOWS_FIELDS,
}

# Hermeto ``py_impl`` filter tokens mapped to the marker fields they determine.
# Tokens absent from this map (e.g. the generic "py") leave these fields unpinned
# rather than guessing an implementation the user did not declare.
_PY_IMPL_TO_MARKER = {
    "cp": {"implementation_name": "cpython", "platform_python_implementation": "CPython"},
    "pp": {"implementation_name": "pypy", "platform_python_implementation": "PyPy"},
}


class _UncontrolledEnvKey(Exception):
    """Raised if a poison value is ever compared during evaluation.

    With the referenced-key gate this should not happen for a well-formed marker,
    so it is a defensive backstop: it still causes a fail-open keep and names the
    offending key.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


class _Poison(str):
    """A ``str`` subclass filling env keys the filters do not pin.

    It must subclass ``str``: ``Marker.evaluate`` unconditionally calls
    ``env["python_full_version"].endswith("+")`` before evaluating anything, so a
    non-``str`` value here would raise ``AttributeError``. The buffer is the empty
    string so that any comparison packaging *does* route through these overrides
    (the referenced-key gate should prevent that, but this is defense in depth)
    raises rather than silently comparing. Interpretability of a leaked poison is
    preserved via ``__repr__``/``__str__`` rather than the compared value.
    """

    _key: str

    def __new__(cls, key: str) -> "_Poison":
        obj = str.__new__(cls, "")
        obj._key = key
        return obj

    def __repr__(self) -> str:
        return f"_Poison({self._key!r})"

    def __str__(self) -> str:
        return f"POISON:{self._key}"

    def __eq__(self, other: object) -> bool:
        raise _UncontrolledEnvKey(self._key)

    def __ne__(self, other: object) -> bool:
        raise _UncontrolledEnvKey(self._key)

    def __lt__(self, other: object) -> bool:
        raise _UncontrolledEnvKey(self._key)

    def __le__(self, other: object) -> bool:
        raise _UncontrolledEnvKey(self._key)

    def __gt__(self, other: object) -> bool:
        raise _UncontrolledEnvKey(self._key)

    def __ge__(self, other: object) -> bool:
        raise _UncontrolledEnvKey(self._key)

    def __contains__(self, other: object) -> bool:
        raise _UncontrolledEnvKey(self._key)

    __hash__ = None  # type: ignore[assignment]


def _concrete_groups(
    filters: PipBinaryFilters,
) -> tuple[list[list[dict[str, str]]], dict[str, str]]:
    """Translate filters into concrete marker-env values.

    Returns ``(groups, base)`` where ``base`` holds env values that apply to every
    combination and ``groups`` is a list of dimensions to take the product over;
    each dimension is a list of mutually-exclusive partial env dicts (its OR
    values). Correlated fields (a single OS pins sys_platform + os_name +
    platform_system together) are kept in one partial dict so the product never
    mixes e.g. sys_platform="linux" with platform_system="Darwin".

    A dimension is included only when the filters fully determine it; otherwise
    its keys are left out (and stay poison / uncontrolled -> fail-open).
    """
    groups: list[list[dict[str, str]]] = []

    # platform (regex) mode is mutually exclusive with arch/os and does not
    # translate to platform_machine/sys_platform, so those stay unpinned.
    if filters.platform is None:
        arches = parse_filter_spec(filters.arch)
        if arches is not None:
            groups.append([{"platform_machine": arch} for arch in sorted(arches)])

        oses = parse_filter_spec(filters.os)
        if oses is not None and all(os_token in _OS_TO_MARKER_FIELDS for os_token in oses):
            groups.append([dict(_OS_TO_MARKER_FIELDS[os_token]) for os_token in sorted(oses)])

    impls = parse_filter_spec(filters.py_impl)
    if impls is not None and all(impl in _PY_IMPL_TO_MARKER for impl in impls):
        groups.append([dict(_PY_IMPL_TO_MARKER[impl]) for impl in sorted(impls)])

    base: dict[str, str] = {}
    if filters.py_version is not None:
        # Hermeto packs the version as an int (312 -> 3.12); divmod handles a
        # two-digit major correctly where string slicing would not. py_version
        # pins only the minor version -- python_full_version (the patch level) is
        # intentionally left unpinned, so markers like python_full_version >=
        # "3.12.1" fail open instead of being decided by a synthesized ".0".
        major, minor = divmod(filters.py_version, 100)
        base["python_version"] = f"{major}.{minor}"

    return groups, base


def _controlled_keys(filters: PipBinaryFilters) -> set[str]:
    """Return the set of marker env keys the filters pin to concrete values."""
    groups, base = _concrete_groups(filters)
    keys = set(base)
    for group in groups:
        for option in group:
            keys.update(option)
    return keys


def _iter_filter_envs(filters: PipBinaryFilters) -> Iterator[dict[str, str]]:
    """Yield one marker environment per concrete combination of pinned filters.

    Every ``default_environment()`` key is present in each env: pinned keys get
    concrete strings, the rest stay ``_Poison`` (never host values).
    """
    groups, base = _concrete_groups(filters)
    for combo in product(*groups) if groups else [()]:
        env: dict[str, str] = {key: _Poison(key) for key in _DEFAULT_ENV_KEYS}
        env.update(base)
        for partial in combo:
            env.update(partial)
        yield env


def _marker_variables(marker: Marker) -> set[str]:
    """Return the set of environment variable names a marker reads.

    Walks packaging's parsed marker structure (a nested list of
    ``(Variable|Value, Op, Value|Variable)`` tuples joined by "and"/"or"). Only
    ``Variable`` nodes are environment keys; ``Value`` nodes are string literals.
    """
    names: set[str] = set()

    def _walk(node: object) -> None:
        if isinstance(node, list):
            for child in node:
                _walk(child)
        elif isinstance(node, tuple):
            lhs, _op, rhs = node
            for side in (lhs, rhs):
                if isinstance(side, Variable):
                    names.add(side.value)

    _walk(marker._markers)
    return names


def requirement_matches_binary_filters(
    requirement: PipRequirement,
    filters: PipBinaryFilters,
) -> bool:
    """Return whether a requirement should be fetched under these binary filters.

    ``True`` means keep/fetch, ``False`` means skip. A requirement is skipped only
    when its environment marker evaluates false for *every* concrete filter
    combination. If the marker reads any dimension the filters do not pin, the
    result is ambiguous and the requirement is kept (fail-open).

    The caller must only invoke this when ``filters`` describes a target platform;
    skipping is not applied in source-only mode.
    """
    marker_str = requirement.environment_marker
    if not marker_str:
        return True

    # ``from_line`` already parsed and re-serialized the marker, so re-parsing
    # here (which keeps this helper independent of the PipRequirement internals)
    # should not fail -- but if packaging ever rejects it, fail open rather than
    # aborting the whole fetch, honoring the fail-open contract.
    try:
        marker = Marker(marker_str)
    except InvalidMarker:
        log.info(
            "Keeping %r: its environment marker %r could not be parsed (fail-open)",
            requirement.download_line,
            marker_str,
        )
        return True

    uncontrolled = _marker_variables(marker) - _controlled_keys(filters)
    if uncontrolled:
        log.info(
            "Keeping %r: its environment marker reads %s, which the binary filters "
            "do not pin (fail-open)",
            requirement.download_line,
            ", ".join(repr(key) for key in sorted(uncontrolled)),
        )
        return True

    for env in _iter_filter_envs(filters):
        try:
            # context="requirement" avoids injecting extra="" the way the default
            # metadata context does; combined with the gate above, evaluation only
            # touches pinned, concrete values.
            if marker.evaluate(env, context="requirement"):
                return True
        except Exception:
            # Defensive: the gate should make evaluation total, so any error here
            # is unexpected -- fail open rather than risk a wrong skip.
            log.debug(
                "Keeping %r: could not evaluate its environment marker (fail-open)",
                requirement.download_line,
                exc_info=True,
            )
            return True

    return False
