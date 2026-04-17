# SPDX-License-Identifier: GPL-3.0-only
from textwrap import dedent

from hermeto import APP_NAME
from hermeto.core import errors


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
