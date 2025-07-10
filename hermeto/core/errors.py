import textwrap
from typing import ClassVar, Optional

from hermeto import APP_NAME

_argument_not_specified = "__argument_not_specified__"


class BaseError(Exception):
    """Root of the error hierarchy. Don't raise this directly, use more specific error types."""

    is_invalid_usage: ClassVar[bool] = False
    default_solution: ClassVar[Optional[str]] = None

    def __init__(
        self,
        reason: str,
        *,
        solution: Optional[str] = _argument_not_specified,
        docs: Optional[str] = None,
    ) -> None:
        """Initialize BaseError.

        :param reason: explain what went wrong
        :param solution: politely suggest a potential solution to the user
        :param docs: include a link to relevant documentation (if there is any)
        """
        super().__init__(reason)
        if solution == _argument_not_specified:
            self.solution = self.default_solution
        else:
            self.solution = solution
        self.docs = docs

    def friendly_msg(self) -> str:
        """Return the user-friendly representation of this error."""
        msg = str(self)
        if msg is not None and  self.solution:
            msg += f"\n{textwrap.indent(self.solution, prefix='  ')}"
        if self.docs:
            msg += f"\n  Docs: {self.docs}"
        return msg


class UsageError(BaseError):
    """Generic error for "Hermeto was used incorrectly." Prefer more specific errors."""

    is_invalid_usage: ClassVar[bool] = True


class PathOutsideRoot(UsageError):
    """Afer joining a subpath, the result is outside the root of a rooted path."""

    def __init__(
        self,
        s_self: str,
        s_other: str = "",
        s_root: str = "",
        *,
        solution: Optional[str] = _argument_not_specified,
        docs: Optional[str] = None,
    ) -> None:
        """Initialize a PathOutsideRoot.

        :param s_self: The current path before joining.
        :param s_other: The path component that was joined.
        :param s_root: The root directory that must not be left.
        :param solution: politely suggest a potential solution to the user
        :param docs: include a link to relevant documentation (if there is any)
        """
        reason = f"Path {s_self}/{s_other} outside {s_root}, refusing to proceed"
        super().__init__(reason, solution=solution, docs=docs)

    default_solution = (
        f"With security in mind, {APP_NAME} will not access files outside the "
        "specified source/output directories."
    )


class InvalidInput(UsageError):
    """User input was invalid."""


class PackageRejected(UsageError):
    """The Application refused to process the package the user requested.

    a) The package appears invalid (e.g. missing go.mod for a Go module).
    b) The package does not meet our extra requirements (e.g. missing checksums).
    """

    def __init__(self, reason: str, *, solution: Optional[str], docs: Optional[str] = None) -> None:
        """Initialize a Package Rejected error.

        Compared to the parent class, the solution param is required (but can be explicitly None).

        :param reason: explain why we rejected the package
        :param solution: politely suggest a potential solution to the user
        :param docs: include a link to relevant documentation (if there is any)
        """
        super().__init__(reason, solution=solution, docs=docs)


class NotAGitRepo(PackageRejected):
    """A package turned out to be not a git repository."""


class UnexpectedFormat(UsageError):
    """The Application failed to parse a file in the user's package (e.g. requirements.txt)."""

    default_solution = (
        "Please check if the format of your file is correct.\n"
        f"If yes, please let the maintainers know that {APP_NAME} doesn't handle it properly."
    )


class UnsupportedFeature(UsageError):
    """The Application doesn't support a feature the user requested.

    The requested feature might be valid, but application doesn't implement it.
    """

    default_solution = (
        f"If you need {APP_NAME} to support this feature, please contact the maintainers."
    )


class FetchError(BaseError):
    """The Application failed to fetch a dependency or other data needed to process a package."""

    default_solution = (
        "The error might be intermittent, please try again.\n"
        f"If the issue seems to be on the {APP_NAME} side, please contact the maintainers."
    )


class PackageManagerError(BaseError):
    """The package manager subprocess returned an error.

    Maybe some configuration is invalid, maybe the package manager was unable to fetch a dependency,
    maybe the error is intermittent. We don't really know, but we do at least log the stderr.
    """

    def __init__(
        self,
        reason: str,
        *,
        stderr: Optional[str] = None,
        solution: Optional[str] = _argument_not_specified,
        docs: Optional[str] = None,
    ) -> None:
        """Initialize a PackageManagerError.

        :param reason: explain what went wrong
        :param stderr: stderr output generated by the used CLI command
        :param solution: politely suggest a potential solution to the user
        :param docs: include a link to relevant documentation (if there is any)
        """
        self.stderr = stderr
        super().__init__(reason, solution=solution, docs=docs)

    default_solution = textwrap.dedent(
        f"""
        The cause of the failure could be:
        - something is broken in {APP_NAME}
        - something is wrong with your repository
        - communication with an external service failed (please try again)
        The output of the failing command should provide more details, please check the logs.
        """
    ).strip()
