"""This module provides functionality to parse and validate requirements.txt files."""

import functools
import io
import logging
import re
from collections.abc import Iterator
from re import Pattern
from typing import IO, Any, Literal
from urllib import parse as urlparse

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from hermeto import APP_NAME
from hermeto.core.errors import (
    InvalidChecksum,
    MissingChecksum,
    PackageRejected,
    UnexpectedFormat,
    UnsupportedFeature,
)
from hermeto.core.rooted_path import RootedPath

log = logging.getLogger(__name__)


# Check that the path component of a URL ends with a full-length git ref
GIT_REF_IN_PATH = re.compile(r"@[a-fA-F0-9]{40}$")

# All supported sdist formats, see https://docs.python.org/3/distutils/sourcedist.html
SDIST_FILE_EXTENSIONS = [".zip", ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.Z", ".tar"]
WHEEL_FILE_EXTENSION = ".whl"
ALL_FILE_EXTENSIONS = SDIST_FILE_EXTENSIONS + [WHEEL_FILE_EXTENSION]


class PipRequirementsFile:
    """Parse requirements from a pip requirements file."""

    # Comment lines start with optional leading spaces followed by "#"
    LINE_COMMENT = re.compile(r"(^|\s)#.*$")

    # Options allowed in a requirements file. The values represent whether or not the option
    # requires a value.
    # https://pip.pypa.io/en/stable/reference/pip_install/#requirements-file-format
    OPTIONS = {
        "--constraint": True,
        "--editable": False,  # The required value is the requirement itself, not a parameter
        "--extra-index-url": True,
        "--find-links": True,
        "--index-url": True,
        "--no-binary": True,
        "--no-index": False,
        "--only-binary": True,
        "--pre": False,
        "--prefer-binary": False,
        "--require-hashes": False,
        "--requirement": True,
        "--trusted-host": True,
        "--use-feature": True,
        "-c": True,
        "-e": False,  # The required value is the requirement itself, not a parameter
        "-f": True,
        "--hash": True,
        "-i": True,
        "-r": True,
    }

    # Options that are specific to a single requirement in the requirements file. All other
    # options apply to all the requirements.
    REQUIREMENT_OPTIONS = {"-e", "--editable", "--hash"}

    def __init__(self, file_path: RootedPath) -> None:
        """Initialize a PipRequirementsFile.

        :param RootedPath file_path: the full path to the requirements file
        """
        self.file_path = file_path

    @classmethod
    def from_requirements_and_options(
        cls, requirements: list["PipRequirement"], options: list[str]
    ) -> "PipRequirementsFile":
        """Create a new PipRequirementsFile instance from given parameters.

        :param list requirements: list of PipRequirement instances
        :param list options: list of strings of global options
        :return: new instance of PipRequirementsFile
        """
        new_instance = cls(RootedPath("/"))
        new_instance._parsed = {"requirements": list(requirements), "options": list(options)}
        return new_instance

    def write(self, file_obj: IO[str]) -> None:
        """Write the options and requirements to a file."""
        if self.options:
            file_obj.write(" ".join(self.options))
            file_obj.write("\n")
        for requirement in self.requirements:
            file_obj.write(str(requirement))
            file_obj.write("\n")

    def generate_file_content(self) -> str:
        """Generate the file content from the parsed options and requirements."""
        fileobj = io.StringIO()
        self.write(fileobj)
        return fileobj.getvalue()

    @property
    def requirements(self) -> list["PipRequirement"]:
        """Return a list of PipRequirement objects."""
        return self._parsed["requirements"]

    @property
    def options(self) -> list[str]:
        """Return a list of options."""
        return self._parsed["options"]

    @functools.cached_property
    def _parsed(self) -> dict[str, Any]:
        """Return the parsed requirements file.

        :return: a dict with the keys ``requirements`` and ``options``
        """
        parsed: dict[str, list[str | PipRequirement]] = {"requirements": [], "options": []}

        for line in self._read_lines():
            (
                global_options,
                requirement_options,
                requirement_line,
            ) = self._split_options_and_requirement(line)
            if global_options:
                parsed["options"].extend(global_options)

            if requirement_line:
                parsed["requirements"].append(
                    PipRequirement.from_line(requirement_line, requirement_options)
                )

        return parsed

    def _read_lines(self) -> Iterator[str]:
        """Read and yield the lines from the requirements file.

        Lines ending in the line continuation character are joined with the next line.
        Comment lines are ignored.
        """
        buffered_line: list[str] = []

        with open(self.file_path) as f:
            for line in f.read().splitlines():
                if not line.endswith("\\"):
                    buffered_line.append(line)
                    new_line = "".join(buffered_line)
                    new_line = self.LINE_COMMENT.sub("", new_line).strip()
                    if new_line:
                        yield new_line
                    buffered_line = []
                else:
                    buffered_line.append(line.rstrip("\\"))

        # Last line ends in "\"
        if buffered_line:
            yield "".join(buffered_line)

    def _split_options_and_requirement(self, line: str) -> tuple[list[str], list[str], str]:
        """Split global and requirement options from the requirement line.

        :param str line: requirement line from the requirements file
        :return: three-item tuple where the first item is a list of global options, the
            second item a list of requirement options, and the last item a str of the
            requirement without any options.
        """
        global_options: list[str] = []
        requirement_options: list[str] = []
        requirement: list[str] = []

        # Indicates the option must be followed by a value
        _require_value = False
        # Reference to either global_options or requirement_options list
        _context_options = []

        for part in line.split():
            if _require_value:
                _context_options.append(part)
                _require_value = False
            elif part.startswith("-"):
                option = None
                value = None
                if "=" in part:
                    option, value = part.split("=", 1)
                else:
                    option = part

                if option not in self.OPTIONS:
                    raise UnexpectedFormat(f"Unknown requirements file option {part!r}")

                _require_value = self.OPTIONS[option]

                if option in self.REQUIREMENT_OPTIONS:
                    _context_options = requirement_options
                else:
                    _context_options = global_options

                if value and not _require_value:
                    raise UnexpectedFormat(
                        f"Unexpected value for requirements file option {part!r}"
                    )

                _context_options.append(option)
                if value:
                    _context_options.append(value)
                    _require_value = False
            else:
                requirement.append(part)

        if _require_value:
            raise UnexpectedFormat(
                f"Requirements file option {_context_options[-1]!r} requires a value"
            )

        if requirement_options and not requirement:
            raise UnexpectedFormat(
                f"Requirements file option(s) {requirement_options!r} can only be applied to a "
                "requirement",
            )

        return global_options, requirement_options, " ".join(requirement)


class PipRequirement:
    """Parse a requirement and its options from a requirement line."""

    # Regex used to determine if a direct access requirement specifies a
    # package name, e.g. "name @ https://..."
    HAS_NAME_IN_DIRECT_ACCESS_REQUIREMENT = re.compile(r"@.+://")

    def __init__(self) -> None:
        """Initialize a PipRequirement."""
        # The package name after it has been processed by setuptools, e.g. "_" are replaced
        # with "-"
        self.package: str = ""
        # The package name as defined in the requirement line
        self.raw_package: str = ""
        self.extras: set[str] = set()
        self.version_specs: list[tuple[str, str]] = []
        self.environment_marker: str | None = None
        self.hashes: list[str] = []
        self.qualifiers: dict[str, str] = {}

        self.kind: Literal["pypi", "url", "vcs"]
        self.download_line: str = ""

        self.options: list[str] = []

        self._url: Any = None

    @property
    def url(self) -> str:
        """Extract the URL from the download line of a VCS or URL requirement."""
        if self._url is None:
            if self.kind not in ("url", "vcs"):
                raise ValueError(f"Cannot extract URL from {self.kind} requirement")
            # package @ url ; environment_marker
            parts = self.download_line.split()
            self._url = parts[2]

        return self._url

    def __str__(self) -> str:
        """Return the string representation of the PipRequirement."""
        line: list[str] = []
        line.extend(self.options)
        line.append(self.download_line)
        line.extend(f"--hash={h}" for h in self.hashes)
        return " ".join(line)

    def copy(self, url: str | None = None, hashes: list[str] | None = None) -> "PipRequirement":
        """Duplicate this instance of PipRequirement.

        :param str url: set a new direct access URL for the requirement. If provided, the
            new requirement is always of ``url`` kind.
        :param list hashes: overwrite hash values for the new requirement
        :return: new PipRequirement instance
        """
        options = list(self.options)
        download_line = self.download_line
        if url:
            download_line_parts: list[str] = []
            download_line_parts.append(self.raw_package)
            download_line_parts.append("@")

            qualifiers_line = "&".join(f"{key}={value}" for key, value in self.qualifiers.items())
            if qualifiers_line:
                download_line_parts.append(f"{url}#{qualifiers_line}")
            else:
                download_line_parts.append(url)

            if self.environment_marker:
                download_line_parts.append(";")
                download_line_parts.append(self.environment_marker)

            download_line = " ".join(download_line_parts)

            # Pip does not support editable mode for requirements installed via an URL, only
            # via VCS. Remove this option to avoid errors later on.
            options = list(set(self.options) - {"-e", "--editable"})
            if self.options != options:
                log.warning(
                    "Removed editable option when copying the requirement %r", self.raw_package
                )

        requirement = self.__class__()
        # Extras are incorrectly treated as part of the URL itself. If we're setting
        # the URL, they must be skipped.
        if not url:
            requirement.extras = set(self.extras)
        requirement.package = self.package
        requirement.raw_package = self.raw_package
        # Version specs are ignored by pip when applied to a URL, let's do the same.
        requirement.version_specs = [] if url else list(self.version_specs)
        requirement.environment_marker = self.environment_marker
        requirement.hashes = list(hashes or self.hashes)
        requirement.qualifiers = dict(self.qualifiers)
        requirement.kind = "url" if url else self.kind
        requirement.download_line = download_line
        requirement.options = options

        return requirement

    @classmethod
    def from_line(cls, line: str, options: list[str]) -> "PipRequirement":
        """Create an instance of PipRequirement from the given requirement and its options.

        Only ``url`` and ``vcs`` direct access requirements are supported. ``file`` is not.

        :param str line: the requirement line
        :param str list: the options associated with the requirement
        :return: PipRequirement instance
        """
        to_be_parsed = line
        qualifiers: dict[str, str] = {}
        requirement = cls()

        if not (direct_access_kind := cls._assess_direct_access_requirement(line)):
            requirement.kind = "pypi"
        else:
            requirement.kind = direct_access_kind
            to_be_parsed, qualifiers = cls._adjust_direct_access_requirement(
                to_be_parsed, cls.HAS_NAME_IN_DIRECT_ACCESS_REQUIREMENT
            )

        try:
            req = Requirement(to_be_parsed)
        except InvalidRequirement as exc:
            # see https://github.com/pypa/setuptools/pull/2137
            raise UnexpectedFormat(f"Unable to parse the requirement {to_be_parsed!r}: {exc}")

        hashes, options = cls._split_hashes_from_options(options)

        requirement.download_line = to_be_parsed
        requirement.options = options
        requirement.package = canonicalize_name(req.name)
        requirement.raw_package = req.name
        requirement.version_specs = [(spec.operator, spec.version) for spec in req.specifier]
        requirement.extras = req.extras
        requirement.environment_marker = str(req.marker) if req.marker else None
        requirement.hashes = hashes
        requirement.qualifiers = qualifiers

        return requirement

    @staticmethod
    def _assess_direct_access_requirement(line: str) -> Literal["url", "vcs"] | None:
        """Determine if the line contains a direct access requirement.

        :param str line: the requirement line
        :return: two-item tuple where the first item is the kind of direct access requirement,
            e.g. "vcs", and the second item is a bool indicating if the requirement is a
            direct access requirement
        """
        URL_SCHEMES = {"http", "https", "ftp"}
        VCS_SCHEMES = {
            "bzr",
            "bzr+ftp",
            "bzr+http",
            "bzr+https",
            "git",
            "git+ftp",
            "git+http",
            "git+https",
            "hg",
            "hg+ftp",
            "hg+http",
            "hg+https",
            "svn",
            "svn+ftp",
            "svn+http",
            "svn+https",
        }
        direct_access_kind: Literal["url", "vcs"]

        if ":" not in line:
            return None
        # Extract the scheme from the line and strip off the package name if needed
        # e.g. name @ https://...
        scheme_parts = line.split(":", 1)[0].split("@")
        if len(scheme_parts) > 2:
            raise UnexpectedFormat(
                f"Unable to extract scheme from direct access requirement {line!r}"
            )
        scheme = scheme_parts[-1].lower().strip()

        if scheme in URL_SCHEMES:
            direct_access_kind = "url"
        elif scheme in VCS_SCHEMES:
            direct_access_kind = "vcs"
        else:
            raise UnsupportedFeature(
                f"Direct references with {scheme!r} scheme are not supported, {line!r}"
            )

        return direct_access_kind

    @staticmethod
    def _adjust_direct_access_requirement(
        line: str, direct_ref_pattern: Pattern[str]
    ) -> tuple[str, dict[str, str]]:
        """Modify the requirement line so it can be parsed and extract qualifiers.

        :param str line: a direct access requirement line
        :param str direct_ref_pattern: a Regex used to determine if a requirement
            specifies a package name
        :return: two-item tuple where the first item is a modified direct access requirement
            line that can be parsed, and the second item is a dict of the
            qualifiers extracted from the direct access URL
        """
        package_name = None
        qualifiers: dict[str, str] = {}
        url = line
        environment_marker = None

        if direct_ref_pattern.search(line):
            package_name, url = line.split("@", 1)

        # For direct access requirements, a space is needed after the semicolon.
        if "; " in url:
            url, environment_marker = url.split("; ", 1)

        parsed_url = urlparse.urlparse(url)
        if parsed_url.fragment:
            for section in parsed_url.fragment.split("&"):
                if "=" in section:
                    attr, value = section.split("=", 1)
                    value = urlparse.unquote(value)
                    qualifiers[attr] = value
                    if attr == "egg":
                        # Use the egg name as the package name to avoid ambiguity when both are
                        # provided. This matches the behavior of "pip install".
                        package_name = value

        if not package_name:
            raise UnsupportedFeature(
                reason=(
                    f"Dependency name could not be determined from the requirement {line!r} "
                    f"({APP_NAME} needs the name to be explicitly declared)"
                ),
                solution="Please specify the name of the dependency: <name> @ <url>",
            )

        requirement_parts = [package_name.strip(), "@", url.strip()]
        if environment_marker:
            # Although a space before the semicolon is not needed by pip, it is needed when
            # using packaging later on.
            requirement_parts.append(";")
            requirement_parts.append(environment_marker.strip())
        return " ".join(requirement_parts), qualifiers

    @staticmethod
    def _split_hashes_from_options(options: list[str]) -> tuple[list[str], list[str]]:
        """Separate the --hash options from the given options.

        :param list options: requirement options
        :return: two-item tuple where the first item is a list of hashes, and the second item
            is a list of options without any ``--hash`` options
        """
        hashes: list[str] = []
        reduced_options: list[str] = []
        is_hash = False

        for item in options:
            if is_hash:
                hashes.append(item)
                is_hash = False
                continue

            is_hash = item == "--hash"
            if not is_hash:
                reduced_options.append(item)

        return hashes, reduced_options


def process_requirements_options(options: list[str]) -> dict[str, Any]:
    """
    Process global options from a requirements.txt file.

    | Rejected option     | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | --extra-index-url   | We only support one index                               |
    | --no-index          | Index is the only thing we support                      |
    | -f --find-links     | We only support index                                   |
    | --only-binary       | Only sdist                                              |

    | Ignored option      | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | -c --constraint     | All versions must already be pinned                     |
    | -e --editable       | Only relevant when installing                           |
    | --no-binary         | Implied                                                 |
    | --prefer-binary     | Prefer sdist                                            |
    | --pre               | We do not care if version is pre-release (it is pinned) |
    | --use-feature       | We probably do not have that feature                    |
    | -* --*              | Did not exist when this implementation was done         |

    | Undecided option    | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | -r --requirement    | We could support this but there is no good reason to    |

    | Relevant option     | Reason                                                  |
    |---------------------|---------------------------------------------------------|
    | -i --index-url      | Supported                                               |
    | --require-hashes    | Hashes are optional, so this makes sense                |
    | --trusted-host      | Disables SSL verification for URL dependencies          |

    :param list[str] options: Global options from a requirements file
    :return: Dict with all the relevant options and their values
    :raise UnsupportedFeature: If any option was rejected
    """
    reject = {
        "--extra-index-url",
        "--no-index",
        "-f",
        "--find-links",
        "--only-binary",
    }

    ignored: list[str] = []
    rejected: list[str] = []

    opts: dict[str, Any] = {
        "require_hashes": False,
        "trusted_hosts": [],
        "index_url": None,
    }

    i = 0
    while i < len(options):
        option = options[i]

        if option == "--require-hashes":
            opts["require_hashes"] = True
        elif option == "--trusted-host":
            opts["trusted_hosts"].append(options[i + 1])
            i += 1
        elif option in ("-i", "--index-url"):
            opts["index_url"] = options[i + 1]
            i += 1
        elif option in reject:
            rejected.append(option)
        elif option.startswith("-"):
            # This is a bit simplistic, option arguments may also start with a '-' but
            # should be good enough for a log message
            ignored.append(option)

        i += 1

    if ignored:
        msg = f"{APP_NAME} will ignore the following options: {', '.join(ignored)}"
        log.info(msg)

    if rejected:
        msg = f"{APP_NAME} does not support the following options: {', '.join(rejected)}"
        raise UnsupportedFeature(msg)

    return opts


def validate_requirements(requirements: list[PipRequirement]) -> None:
    """
    Validate that all requirements meet our expectations.

    :param list[PipRequirement] requirements: All requirements from a file
    :raise PackageRejected: If any requirement does not meet expectations
    :raise UnsupportedFeature: If any requirement uses unsupported features
    :raise InvalidChecksum: If any provided checksum data is not valid
    """
    for req in requirements:
        # Fail if PyPI requirement is not pinned to an exact version
        if req.kind == "pypi":
            vspec = req.version_specs
            if len(vspec) != 1 or vspec[0][0] not in ("==", "==="):
                msg = f"Requirement must be pinned to an exact version: {req.download_line}"
                raise PackageRejected(
                    msg,
                    solution=(
                        "Please pin all packages as <name>==<version>\n"
                        "You may wish to use a tool such as pip-compile to pin automatically."
                    ),
                )

        # Fail if VCS requirement uses any VCS other than git or does not have a valid ref
        elif req.kind == "vcs":
            url = urlparse.urlparse(req.url)

            if not url.scheme.startswith("git"):
                raise UnsupportedFeature(
                    f"Unsupported VCS for {req.download_line}: {url.scheme} (only git is supported)"
                )

            if not GIT_REF_IN_PATH.search(url.path):
                msg = f"No git ref in {req.download_line} (expected 40 hexadecimal characters)"
                raise PackageRejected(
                    msg,
                    solution=(
                        "Please specify the full commit hash for git URLs or switch to https URLs."
                    ),
                )

        # Fail if URL requirement does not specify exactly one hash (--hash or #cachito_hash)
        # or does not have a recognized file extension
        elif req.kind == "url":
            n_hashes = len(req.hashes) + (1 if req.qualifiers.get("cachito_hash") else 0)
            if n_hashes != 1:
                raise InvalidChecksum(
                    checksum=req.hashes,
                    solution=(
                        f"URL requirement must specify exactly one hash, but specifies {n_hashes}"
                        "Please specify the expected hashes for all plain URLs using "
                        "--hash options (one --hash for each)"
                    ),
                )

            url = urlparse.urlparse(req.url)
            if not any(url.path.endswith(ext) for ext in ALL_FILE_EXTENSIONS):
                msg = (
                    "URL for requirement does not contain any recognized file extension: "
                    f"{req.download_line} (expected one of {', '.join(ALL_FILE_EXTENSIONS)})"
                )
                raise PackageRejected(msg, solution=None)


def validate_requirements_hashes(requirements: list[PipRequirement], require_hashes: bool) -> None:
    """
    Validate that hashes are not missing and follow the "algorithm:digest" format.

    :param list[PipRequirement] requirements: All requirements from a file
    :param bool require_hashes: True if hashes are required for all requirements
    :raise MissingChecksum: If any hashes are missing
    :raise InvalidChecksum: If hashes have invalid format
    """
    for req in requirements:
        if req.kind == "url":
            hashes = req.hashes or [req.qualifiers["cachito_hash"]]
        else:
            hashes = req.hashes

        if require_hashes and not hashes:
            # We shouldn't get here, but it's a definite error if we do.
            # VCS reqs *cannot* be hashed, so we'll always hit
            # this for any VCS req in a 'requirements.txt' which has *any* hash
            # (other than a URL req with `cachito_hash``).
            # For URL # requirements, having a hash is required to pass *basic* validation.
            raise MissingChecksum(
                None,
                solution=(
                    f"Hash is required, dependency does not specify any: {req.download_line}"
                    "Please specify the expected hashes for all dependencies"
                ),
            )

        for hash_spec in hashes:
            _, _, digest = hash_spec.partition(":")
            if not digest:
                raise InvalidChecksum(
                    checksum=hash_spec,
                    solution=f"Not a valid hash specifier: {hash_spec!r} (expected 'algorithm:digest')",
                )
