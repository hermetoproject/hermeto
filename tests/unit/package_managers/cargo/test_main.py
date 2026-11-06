# SPDX-License-Identifier: GPL-3.0-only
import textwrap

import pytest

from hermeto.core.package_managers.cargo.main import _is_lockfile_mismatch


@pytest.mark.parametrize(
    "stderr, expected",
    [
        pytest.param(
            textwrap.dedent(
                """\
                    Updating crates.io index
                error: failed to sync

                Caused by:
                  failed to load lockfile for /src

                Caused by:
                  cannot update the lock file /src/Cargo.lock because --locked was passed to prevent this
                  help: to generate the lock file without accessing the network, remove the --locked flag and use --offline instead.
                """
            ),
            True,
            id="cargo_vendor",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                error: failed to sync

                Caused by:
                  failed to load pkg lockfile

                Caused by:
                  the lock file /src/Cargo.lock needs to be updated but --locked was passed to prevent this
                """
            ),
            True,
            id="cargo_vendor_before_1.93",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                Gathering metadata for vendored packages
                error: Executing cargo metadata: `cargo metadata` exited with an error:     Updating crates.io index
                error: cannot update the lock file /src/Cargo.lock because --locked was passed to prevent this
                help: to generate the lock file without accessing the network, remove the --locked flag and use --offline instead.
                """
            ),
            True,
            id="cargo_vendor_filterer",
        ),
        pytest.param(
            textwrap.dedent(
                """\
                    Updating crates.io index
                error: failed to sync

                Caused by:
                  failed to load lockfile for /src

                Caused by:
                  no matching package named `not-a-real-crate` found
                  location searched: crates.io index
                  required by package `t v0.1.0 (/src)`
                """
            ),
            False,
            id="unrelated_failure",
        ),
    ],
)
def test_is_lockfile_mismatch(stderr: str, expected: bool) -> None:
    assert _is_lockfile_mismatch(stderr) is expected
