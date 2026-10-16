# SPDX-License-Identifier: GPL-3.0-only
"""Tests for PEP 508 marker evaluation under binary filters.

See https://github.com/hermetoproject/hermeto/issues/1570

These tests deliberately drive the real ``packaging.markers.Marker.evaluate`` via
real ``PipRequirement.from_line`` and ``PipBinaryFilters`` instances -- the whole
point of #1570 is a subtle interaction with packaging internals, which a mocked
evaluator would hide.
"""

import logging

import pytest
from packaging.markers import default_environment

from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.package_managers.python.pip.markers import (
    _iter_filter_envs,
    _marker_variables,
    _Poison,
    _UncontrolledEnvKey,
    requirement_matches_binary_filters,
)
from hermeto.core.package_managers.python.pip.requirements import PipRequirement

# A real git commit-shaped ref (40 hex chars) so VCS requirements parse.
GIT_REF = "a" * 40

# The literal marker from the #1570 issue body.
BCRYPT_1570_MARKER = (
    'implementation_name == "cpython" '
    'and platform_machine != "ppc64le" '
    'and platform_machine != "s390x" '
    'and sys_platform == "linux"'
)


def _pypi_req(
    marker: str | None = None, name: str = "bcrypt", version: str = "5.0.0"
) -> PipRequirement:
    line = f"{name}=={version}"
    if marker:
        line += f" ; {marker}"
    return PipRequirement.from_line(line, [])


def _url_req(marker: str | None = None) -> PipRequirement:
    line = "foo @ https://example.com/foo-1.0.tar.gz"
    if marker:
        line += f" ; {marker}"
    return PipRequirement.from_line(line, [])


def _vcs_req(marker: str | None = None) -> PipRequirement:
    line = f"foo @ git+https://github.com/org/repo@{GIT_REF}"
    if marker:
        line += f" ; {marker}"
    return PipRequirement.from_line(line, [])


class TestFlagship:
    """The #1570 case must skip, and must not crash on packaging's version repair.

    These two are the first line of defense: they fail loudly if the poison is
    ever changed to a plain object (AttributeError on ``.endswith``) or to a
    non-empty string (silent ``False`` on version markers).
    """

    def test_issue_1570_bcrypt_is_skipped_on_ppc64le(self) -> None:
        req = _pypi_req(BCRYPT_1570_MARKER)
        filters = PipBinaryFilters(arch="ppc64le", os="linux")  # note: no py_version

        assert requirement_matches_binary_filters(req, filters) is False

    def test_poison_survives_python_full_version_repair(self) -> None:
        # py_version unset -> python_full_version stays poisoned. packaging calls
        # python_full_version.endswith("+") before evaluating; this must not raise.
        req = _pypi_req('platform_machine != "ppc64le"')
        filters = PipBinaryFilters(arch="ppc64le", os="linux")

        result = requirement_matches_binary_filters(req, filters)

        assert isinstance(result, bool)
        assert result is False


@pytest.mark.parametrize(
    "req, filters, expected",
    [
        pytest.param(
            _pypi_req(),
            PipBinaryFilters(arch="ppc64le", os="linux"),
            True,
            id="no_marker_keeps",
        ),
        pytest.param(
            _pypi_req('platform_machine != "ppc64le" and sys_platform == "linux"'),
            PipBinaryFilters(arch="ppc64le", os="linux"),
            False,
            id="exclude_ppc_on_ppc_skips",
        ),
        pytest.param(
            _pypi_req('platform_machine != "ppc64le" and sys_platform == "linux"'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            True,
            id="exclude_ppc_on_x86_keeps",
        ),
        pytest.param(
            _pypi_req('platform_machine != "ppc64le"'),
            PipBinaryFilters(arch="x86_64,ppc64le", os="linux"),
            True,
            id="multi_arch_or_keeps_if_any_matches",
        ),
        pytest.param(
            _pypi_req('platform_machine != "ppc64le"'),
            PipBinaryFilters(arch=":all:", os="linux"),
            True,
            id="all_arch_keeps_fail_open",
        ),
        pytest.param(
            _pypi_req('python_version < "3.11"'),
            PipBinaryFilters(arch="x86_64", os="linux", py_version=312),
            False,
            id="py_version_pinned_excludes",
        ),
        pytest.param(
            _pypi_req('python_version >= "3.12"'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            True,
            id="py_version_unset_fails_open",
        ),
        pytest.param(
            _pypi_req('sys_platform == "linux"'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            True,
            id="only_linux_on_linux_keeps",
        ),
        pytest.param(
            _pypi_req('sys_platform == "linux"'),
            PipBinaryFilters(arch="x86_64", os="macos"),
            False,
            id="only_linux_on_macos_skips",
        ),
        pytest.param(
            # os="darwin" must map to sys_platform "darwin", not the literal token.
            _pypi_req('sys_platform != "darwin"'),
            PipBinaryFilters(arch="x86_64", os="darwin"),
            False,
            id="os_darwin_maps_to_sys_platform_darwin",
        ),
        pytest.param(
            # "macosx" is the documented macOS token; it must map to sys_platform
            # "darwin" too, so a linux-only marker is skipped when targeting macOS.
            _pypi_req('sys_platform == "linux"'),
            PipBinaryFilters(arch="x86_64", os="macosx"),
            False,
            id="os_macosx_maps_to_sys_platform_darwin_skip",
        ),
        pytest.param(
            _pypi_req('sys_platform == "darwin"'),
            PipBinaryFilters(arch="x86_64", os="macosx"),
            True,
            id="os_macosx_matches_darwin_marker_keep",
        ),
        pytest.param(
            # A mixed linux,macosx target still fully determines the OS dimension
            # (both tokens are known), so an os-only marker evaluates concretely.
            _pypi_req('sys_platform == "win32"'),
            PipBinaryFilters(arch="x86_64", os="linux,macosx"),
            False,
            id="os_linux_macosx_both_known_skips_win_marker",
        ),
        pytest.param(
            _pypi_req('platform_release == "5.10"'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            True,
            id="uncontrolled_key_fails_open",
        ),
        pytest.param(
            # extra is never pinned; must fail open, not evaluate against "".
            _pypi_req('platform_machine != "ppc64le" and extra == "test"'),
            PipBinaryFilters(arch="ppc64le", os="linux"),
            True,
            id="extra_marker_fails_open",
        ),
        pytest.param(
            _pypi_req('"y" in platform_release'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            True,
            id="in_operator_on_poison_fails_open",
        ),
        pytest.param(
            _pypi_req('"y" not in platform_release'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            True,
            id="not_in_operator_on_poison_fails_open",
        ),
        pytest.param(
            _url_req('platform_machine == "s390x"'),
            PipBinaryFilters(arch="ppc64le", os="linux"),
            False,
            id="url_requirement_marker_skips",
        ),
        pytest.param(
            _vcs_req('platform_machine == "s390x"'),
            PipBinaryFilters(arch="ppc64le", os="linux"),
            False,
            id="vcs_requirement_marker_skips",
        ),
        pytest.param(
            # platform regex mode must not synthesize platform_machine/sys_platform.
            _pypi_req('platform_machine == "s390x"'),
            PipBinaryFilters(platform="ppc64le"),
            True,
            id="platform_regex_mode_fails_open",
        ),
        pytest.param(
            # py_impl default is cp -> implementation_name is cpython.
            _pypi_req('implementation_name == "pypy"'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            False,
            id="py_impl_cp_excludes_pypy_marker",
        ),
        pytest.param(
            # ":all:" appearing among other values means unconstrained.
            _pypi_req('platform_machine != "ppc64le"'),
            PipBinaryFilters(arch="x86_64,:all:", os="linux"),
            True,
            id="all_among_values_keeps_fail_open",
        ),
        pytest.param(
            # A py_impl token with no known marker mapping stays poisoned.
            _pypi_req('implementation_name == "cpython"'),
            PipBinaryFilters(arch="x86_64", os="linux", py_impl="py"),
            True,
            id="unknown_py_impl_fails_open",
        ),
        pytest.param(
            # An os token with no known sys_platform mapping stays poisoned.
            _pypi_req('sys_platform == "linux"'),
            PipBinaryFilters(arch="x86_64", os="haiku"),
            True,
            id="unknown_os_fails_open",
        ),
        # Membership with the variable on the LEFT: the value being searched for is
        # unpinned, so this must fail open rather than be decided by substring.
        pytest.param(
            _pypi_req('platform_machine not in "x86_64"'),
            PipBinaryFilters(arch=":all:", os="linux"),
            True,
            id="membership_var_on_left_unpinned_fails_open",
        ),
        pytest.param(
            _pypi_req('platform_machine in "x86_64,aarch64"'),
            PipBinaryFilters(arch=":all:", os="linux"),
            True,
            id="membership_in_var_on_left_unpinned_fails_open",
        ),
        # py_version pins only the minor; a same-minor patch comparison is unknown.
        pytest.param(
            _pypi_req('python_full_version >= "3.12.1"'),
            PipBinaryFilters(arch="x86_64", os="linux", py_version=312),
            True,
            id="python_full_version_patch_fails_open",
        ),
        # A pinned os also determines os_name and platform_system.
        pytest.param(
            _pypi_req('os_name != "posix"'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            False,
            id="pinned_os_determines_os_name_skip",
        ),
        pytest.param(
            _pypi_req('platform_system == "Linux"'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            True,
            id="pinned_os_determines_platform_system_keep",
        ),
        pytest.param(
            _pypi_req('os_name == "nt"'),
            PipBinaryFilters(arch="x86_64", os="windows"),
            True,
            id="pinned_windows_determines_os_name_keep",
        ),
        # Nested boolean trees: an unpinned key anywhere in the tree fails open,
        # regardless of the surrounding and/or structure (verifies the gate walks
        # the whole marker, not just top-level clauses).
        pytest.param(
            _pypi_req('platform_machine == "ppc64le" and platform_release == "5"'),
            PipBinaryFilters(arch="ppc64le", os="linux"),
            True,
            id="nested_controlled_and_poison_fails_open",
        ),
        pytest.param(
            _pypi_req('platform_release == "5" or platform_machine == "ppc64le"'),
            PipBinaryFilters(arch="ppc64le", os="linux"),
            True,
            id="nested_poison_or_controlled_fails_open",
        ),
        # Fully-controlled nested trees are evaluated concretely.
        pytest.param(
            _pypi_req(
                '(platform_machine == "ppc64le" or platform_machine == "s390x") '
                'and sys_platform == "linux"'
            ),
            PipBinaryFilters(arch="x86_64", os="linux"),
            False,
            id="fully_controlled_nested_skips",
        ),
        pytest.param(
            _pypi_req(
                '(platform_machine == "x86_64" or platform_machine == "s390x") '
                'and sys_platform == "linux"'
            ),
            PipBinaryFilters(arch="x86_64", os="linux"),
            True,
            id="fully_controlled_nested_keeps",
        ),
        # PEP 440 wildcard on a pinned python_version.
        pytest.param(
            _pypi_req('python_version == "3.11.*"'),
            PipBinaryFilters(arch="x86_64", os="linux", py_version=311),
            True,
            id="wildcard_python_version_matches",
        ),
        pytest.param(
            _pypi_req('python_version == "3.11.*"'),
            PipBinaryFilters(arch="x86_64", os="linux", py_version=312),
            False,
            id="wildcard_python_version_excludes",
        ),
        # PEP 508 string comparisons are case-sensitive; the env uses the real
        # lowercase sys_platform, so "Linux" does not match (as pip would decide).
        pytest.param(
            _pypi_req('sys_platform == "Linux"'),
            PipBinaryFilters(arch="x86_64", os="linux"),
            False,
            id="sys_platform_comparison_is_case_sensitive",
        ),
    ],
)
def test_requirement_matches_binary_filters(
    req: PipRequirement, filters: PipBinaryFilters, expected: bool
) -> None:
    assert requirement_matches_binary_filters(req, filters) is expected


@pytest.mark.parametrize("py_version", [0, 207, 400, -1, 31299])
def test_unusual_py_version_values_return_bool_without_crashing(py_version: int) -> None:
    """py_version is an unvalidated int; odd values (Python 2.x, 4.x, 0, negative,
    packed-too-large) must not crash the helper -- they yield a bool (fail-open on
    any resulting version-parse error)."""
    req = _pypi_req('python_version >= "3.0"')
    result = requirement_matches_binary_filters(
        req, PipBinaryFilters(arch="x86_64", os="linux", py_version=py_version)
    )
    assert isinstance(result, bool)


def test_large_marker_is_handled_efficiently() -> None:
    """Marker size does not drive the env product (env count = filter cardinality),
    so even a pathologically long marker is handled in linear time."""
    marker = " or ".join(f'platform_machine == "arch{i}"' for i in range(500))
    result = requirement_matches_binary_filters(
        _pypi_req(marker), PipBinaryFilters(arch="x86_64", os="linux")
    )
    assert result is False


class TestFailOpenLogging:
    def test_uncontrolled_key_logs_info_naming_the_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        req = _pypi_req('platform_release == "5.10"')
        filters = PipBinaryFilters(arch="x86_64", os="linux")

        with caplog.at_level(logging.INFO):
            assert requirement_matches_binary_filters(req, filters) is True

        assert "platform_release" in caplog.text

    def test_extra_marker_logs_info_naming_extra(self, caplog: pytest.LogCaptureFixture) -> None:
        req = _pypi_req('extra == "test"')
        filters = PipBinaryFilters(arch="x86_64", os="linux")

        with caplog.at_level(logging.INFO):
            assert requirement_matches_binary_filters(req, filters) is True

        assert "extra" in caplog.text

    def test_unexpected_evaluate_error_fails_open_at_debug(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Defensive branch: any error other than an unpinned-dimension access must
        # still fail open (keep), logged at debug rather than info.
        def boom(*args: object, **kwargs: object) -> bool:
            raise ValueError("unexpected packaging failure")

        monkeypatch.setattr(
            "hermeto.core.package_managers.python.pip.markers.Marker.evaluate", boom
        )
        req = _pypi_req('platform_machine != "ppc64le"')
        filters = PipBinaryFilters(arch="ppc64le", os="linux")

        with caplog.at_level(logging.DEBUG):
            assert requirement_matches_binary_filters(req, filters) is True

        assert any(record.levelno == logging.DEBUG for record in caplog.records)

    def test_unparsable_marker_fails_open_at_info(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The re-parse should never fail (from_line already parsed it), but if
        # packaging ever rejects the marker it must fail open, not abort the fetch.
        from packaging.markers import InvalidMarker

        def boom(*args: object, **kwargs: object) -> object:
            raise InvalidMarker("synthetic parse failure")

        monkeypatch.setattr("hermeto.core.package_managers.python.pip.markers.Marker", boom)
        req = _pypi_req('platform_machine != "ppc64le"')
        filters = PipBinaryFilters(arch="ppc64le", os="linux")

        with caplog.at_level(logging.INFO):
            assert requirement_matches_binary_filters(req, filters) is True

        assert "could not be parsed" in caplog.text


class TestMarkerVariables:
    """Guards the (acknowledged) private ``marker._markers`` walk against silent
    breakage on a packaging upgrade -- if the structure changes, these fail loudly
    rather than the gate quietly reading no variables and mis-skipping."""

    def test_extracts_all_referenced_keys_both_operand_orders(self) -> None:
        from packaging.markers import Marker

        marker = Marker(
            'platform_machine != "ppc64le" and (sys_platform == "linux" or "x" in platform_release)'
        )

        assert _marker_variables(marker) == {
            "platform_machine",
            "sys_platform",
            "platform_release",
        }

    def test_ignores_string_literals(self) -> None:
        from packaging.markers import Marker

        # Only the variable is an env key; both quoted operands are literals.
        assert _marker_variables(Marker('"linux" == sys_platform')) == {"sys_platform"}


class TestCompleteEnvInvariant:
    """Every synthesized env must cover all default_environment() keys as str.

    A missing key would leak the host value into evaluation (violating "no
    runtime platform detection"); a non-str value would crash packaging's
    unconditional python_full_version repair.
    """

    @pytest.mark.parametrize(
        "filters",
        [
            PipBinaryFilters(arch="ppc64le", os="linux"),
            PipBinaryFilters(arch="x86_64,aarch64", os="linux,macos", py_version=312),
            PipBinaryFilters(arch=":all:", os=":all:", py_impl=":all:"),
            PipBinaryFilters(platform="ppc64le"),
        ],
    )
    def test_env_covers_all_default_keys_as_str(self, filters: PipBinaryFilters) -> None:
        expected_keys = set(default_environment())

        envs = list(_iter_filter_envs(filters))

        assert envs, "at least one env combination must be produced"
        for env in envs:
            assert set(env) == expected_keys
            assert all(isinstance(value, str) for value in env.values())

    def test_unpinned_dimensions_are_poison(self) -> None:
        # arch pinned, os/py unpinned -> platform_machine concrete, others poison.
        (env,) = list(_iter_filter_envs(PipBinaryFilters(arch="ppc64le", os=":all:")))

        assert env["platform_machine"] == "ppc64le"
        assert isinstance(env["python_full_version"], _Poison)
        assert isinstance(env["sys_platform"], _Poison)


class TestPoison:
    def test_buffer_is_empty_but_labels_are_interpretable(self) -> None:
        poison = _Poison("python_version")

        assert len(poison) == 0  # empty buffer is load-bearing for fail-open
        assert str(poison) == "POISON:python_version"
        assert repr(poison) == "_Poison('python_version')"

    @pytest.mark.parametrize("op", ["eq", "ne", "lt", "le", "gt", "ge"])
    def test_comparisons_raise(self, op: str) -> None:
        import operator

        poison = _Poison("platform_machine")
        with pytest.raises(_UncontrolledEnvKey) as exc_info:
            getattr(operator, op)(poison, "x86_64")
        assert exc_info.value.key == "platform_machine"

    def test_membership_raises(self) -> None:
        poison = _Poison("platform_release")
        with pytest.raises(_UncontrolledEnvKey) as exc_info:
            "x" in poison
        assert exc_info.value.key == "platform_release"

    def test_is_unhashable(self) -> None:
        with pytest.raises(TypeError):
            hash(_Poison("platform_machine"))
