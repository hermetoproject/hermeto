from textwrap import dedent

from hermeto.core import errors


def test_package_rejected_friendly_msg() -> None:
    err = errors.PackageRejected(
        "The package does not look valid",
        solution="Please fix your package\nOr read this second line",
        docs="https://example.org",
    )
    expect_msg = dedent(
        """
        The package does not look valid
          Please fix your package
          Or read this second line
          Docs: https://example.org
        """
    ).strip()
    assert err.friendly_msg() == expect_msg


def test_unsupported_feature_default_friendly_msg() -> None:
    err = errors.UnsupportedFeature("This feature is not supported")
    expect_msg = dedent(
        """
        This feature is not supported
          If you need Cachi2 to support this feature, please contact the maintainers.
        """
    ).strip()
    assert err.friendly_msg() == expect_msg

    no_default = errors.UnsupportedFeature("This feature is not supported", solution=None)
    assert no_default.friendly_msg() == "This feature is not supported"


def test_turn_off_default_solution() -> None:
    err = errors.UnsupportedFeature("This feature is not supported", solution=None)
    assert err.friendly_msg() == "This feature is not supported"


def test_cli_command_error_friendly_msg() -> None:
    err = errors.PackageManagerError("Some cli command failed")
    expect_msg = dedent(
        """
        Some cli command failed
          The cause of the failure could be:
          - something is broken in Cachi2
          - something is wrong with your repository
          - communication with an external service failed (please try again)
          The output of the failing command should provide more details, please check the logs.
        """
    ).strip()
    assert err.friendly_msg() == expect_msg


def test_fetch_error_friendly_msg() -> None:
    err = errors.FetchError("Failed to fetch something")
    expect_msg = dedent(
        """
        Failed to fetch something
          The error might be intermittent, please try again.
          If the issue seems to be on the Cachi2 side, please contact the maintainers.
        """
    ).strip()
    assert err.friendly_msg() == expect_msg
