# SPDX-License-Identifier: GPL-3.0-only
from textwrap import dedent

import pytest

from hermeto import APP_NAME
from hermeto.core import errors


@pytest.mark.parametrize(
    "error, expect_msg",
    [
        pytest.param(
            errors.PackageRejected(
                "The package does not look valid",
                solution="Please fix your package\nOr read this second line",
            ),
            dedent(
                """
                The package does not look valid
                  Please fix your package
                  Or read this second line
                """
            ).strip(),
            id="package_rejected",
        ),
        pytest.param(
            errors.PackageManagerError("Some cli command failed"),
            dedent(
                f"""
                Some cli command failed
                  The cause of the failure could be:
                  - something is broken in {APP_NAME}
                  - something is wrong with your repository
                  - communication with an external service failed (please try again)
                  The output of the failing command should provide more details, please check the logs.
                """
            ).strip(),
            id="cli_command_error",
        ),
        pytest.param(
            errors.FetchError("Failed to fetch something"),
            dedent(
                f"""
                Failed to fetch something
                  The error might be intermittent, please try again.
                  If the issue seems to be on the {APP_NAME} side, please contact the maintainers.
                """
            ).strip(),
            id="fetch_error",
        ),
    ],
)
def test_error_friendly_msg(error: errors.BaseError, expect_msg: str) -> None:
    assert error.friendly_msg() == expect_msg


def test_unsupported_feature_default_friendly_msg() -> None:
    err = errors.UnsupportedFeature("This feature is not supported")
    expect_msg = dedent(
        f"""
        This feature is not supported
          If you need {APP_NAME} to support this feature, please contact the maintainers.
        """
    ).strip()
    assert err.friendly_msg() == expect_msg

    no_default = errors.UnsupportedFeature("This feature is not supported", solution=None)
    assert no_default.friendly_msg() == "This feature is not supported"


def test_turn_off_default_solution() -> None:
    err = errors.UnsupportedFeature("This feature is not supported", solution=None)
    assert err.friendly_msg() == "This feature is not supported"
