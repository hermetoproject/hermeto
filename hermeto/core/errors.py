import textwrap
from pathlib import Path
from typing import ClassVar

from hermeto import APP_NAME

_argument_not_specified = "__argument_not_specified__"


class BaseError(Exception):
    """Root of the error hierarchy. Don't raise this directly, use more specific error types."""

    is_invalid_usage: ClassVar[bool] = False
    default_solution: ClassVar[str | None] = None

    def __init__(
        self,
        reason: str,
        *,
        solution: str | None = _argument_not_specified,
        docs: str | None = None,
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
        if self.solution:
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
        solution: str | None = _argument_not_specified,
        docs: str | None = None,
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

    def __init__(self, reason: str, *, solution: str | None, docs: str | None = None) -> None:
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


class ExecutableNotFound(UsageError):
    """A required executable was not found in PATH."""

    def __init__(
        self,
        executable: str,
        *,
        solution: str | None = _argument_not_specified,
        docs: str | None = None,
    ) -> None:
        """Initialize ExecutableNotFound.

        :param executable: Name of the executable that was not found
        :param solution: politely suggest a potential solution to the user
        :param docs: include a link to relevant documentation (if there is any)
        """
        reason = f"{executable!r} executable not found in PATH"
        super().__init__(reason, solution=solution, docs=docs)

    default_solution = (
        "Please make sure that the required executable is installed in your PATH.\n"
        f"If you are using {APP_NAME} via its container image, this should not happen - "
        "please report this bug."
    )


class ChecksumVerificationFailed(PackageRejected):
    """Checksum verification failed for a file."""

    def __init__(
        self,
        filename: str | Path,
        *,
        solution: str | None = _argument_not_specified,
        docs: str | None = None,
    ) -> None:
        """Initialize ChecksumVerificationFailed.

        :param filename: Name of the file that failed checksum verification
        :param solution: politely suggest a potential solution to the user
        :param docs: include a link to relevant documentation (if there is any)
        """
        reason = f"Failed to verify {filename} against any of the provided checksums"
        super().__init__(reason, solution=solution, docs=docs)

    default_solution = (
        "Verify that the file has not been corrupted and that the expected checksums are correct."
    )


class ChecksumMissing(PackageRejected):
    """Required checksum/hash/integrity is missing for a dependency."""

    def __init__(
        self,
        reason: str,
        *,
        solution: str | None = _argument_not_specified,
        docs: str | None = None,
    ) -> None:
        """Initialize ChecksumMissing.

        :param reason: explain what checksum is missing and for which dependency
        :param solution: politely suggest a potential solution to the user
        :param docs: include a link to relevant documentation (if there is any)
        """
        super().__init__(reason, solution=solution, docs=docs)

    default_solution = "Please ensure that all dependencies have proper checksums/hashes specified in the lockfile."


class LockfileNotFound(PackageRejected):
    """A required lockfile was not found."""

    def __init__(
        self,
        lockfile_path: Path | str,
        lockfile_name: str,
        *,
        solution: str | None = _argument_not_specified,
        docs: str | None = None,
    ) -> None:
        """Initialize LockfileNotFound.

        :param lockfile_path: Path where lockfile was expected
        :param lockfile_name: Name of the expected lockfile
        :param solution: politely suggest a potential solution to the user
        :param docs: include a link to relevant documentation (if there is any)
        """
        reason = f"{APP_NAME} lockfile '{lockfile_name}' does not exist in '{lockfile_path}', refusing to continue"
        if solution == _argument_not_specified:
            solution = (
                f"Make sure your repository has {APP_NAME} lockfile '{lockfile_name}' "
                "checked in to the repository, or the supplied lockfile path is correct."
            )
        super().__init__(reason, solution=solution, docs=docs)


class InvalidLockfileFormat(PackageRejected):
    """Lockfile format is invalid or cannot be parsed."""

    def __init__(
        self,
        lockfile_path: Path | str,
        err_details: str | None,
        *,
        solution: str | None = _argument_not_specified,
        docs: str | None = None,
    ) -> None:
        """Initialize InvalidLockfileFormat.

        :param lockfile_path: Path to the invalid lockfile
        :param err_details: Details about what is invalid
        :param solution: politely suggest a potential solution to the user
        :param docs: include a link to relevant documentation (if there is any)
        """
        reason = f"{APP_NAME} lockfile '{lockfile_path}' format is not valid: {err_details}"
        if solution == _argument_not_specified:
            solution = "Check correct syntax in the lockfile."
        super().__init__(reason, solution=solution, docs=docs)


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
        stderr: str | None = None,
        solution: str | None = _argument_not_specified,
        docs: str | None = None,
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
