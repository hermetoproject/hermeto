import re
from textwrap import dedent
from typing import Any, Union

import pytest

from hermeto.core.errors import PackageRejected, UnexpectedFormat, UnsupportedFeature
from hermeto.core.package_managers.pip.requirements import (
    PipRequirement,
    PipRequirementsFile,
    validate_requirements,
)
from hermeto.core.rooted_path import RootedPath
from tests.unit.package_managers.pip.test_main import mock_requirement


def test_validate_whl_url_when_binaries_allowed() -> None:
    url = "https://example.org/file.whl"
    req = mock_requirement("foo", "url", url=url, download_line=f"foo @ {url}")

    validate_requirements([req], allow_binary=True)


def test_validate_whl_url_when_binaries_not_allowed() -> None:
    url = "https://example.org/file.whl"
    req = mock_requirement("foo", "url", url=url, download_line=f"foo @ {url}")

    with pytest.raises(PackageRejected):
        validate_requirements([req], allow_binary=False)


class TestPipRequirementsFile:
    """PipRequirementsFile tests."""

    PIP_REQUIREMENT_ATTRS: dict[str, Any] = {
        "download_line": None,
        "environment_marker": None,
        "extras": [],
        "hashes": [],
        "kind": None,
        "options": [],
        "package": None,
        "qualifiers": {},
        "raw_package": None,
        "version_specs": [],
    }

    @pytest.mark.parametrize(
        "file_contents, expected_requirements, expected_global_options",
        (
            # Dependency from pypi
            (
                "aiowsgi",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi",
                        "raw_package": "aiowsgi",
                    }
                ],
                [],
            ),
            # Dependency from pypi with pinned version
            (
                "aiowsgi==0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with minimum version
            (
                "aiowsgi>=0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi>=0.7",
                        "version_specs": [(">=", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with version range
            (
                "aiowsgi>=0.7,<1.0",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi>=0.7,<1.0",
                        "version_specs": [(">=", "0.7"), ("<", "1.0")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with picky version
            (
                "aiowsgi>=0.7,<1.0,!=0.8",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi>=0.7,<1.0,!=0.8",
                        "version_specs": [(">=", "0.7"), ("<", "1.0"), ("!=", "0.8")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with extras
            (
                "aiowsgi[spam,bacon]==0.7",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi[spam,bacon]==0.7",
                        "version_specs": [("==", "0.7")],
                        "extras": ["spam", "bacon"],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with major version compatibility
            (
                "aiowsgi~=0.6",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi~=0.6",
                        "version_specs": [("~=", "0.6")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with environment markers
            (
                'aiowsgi; python_version < "2.7"',
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": 'aiowsgi; python_version < "2.7"',
                        "environment_marker": 'python_version < "2.7"',
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Dependency from pypi with hashes
            (
                dedent(
                    """\
                    amqp==2.5.2 \\
                       --hash=sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545 \\
                       --hash=sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8
                    """
                ),
                [
                    {
                        "package": "amqp",
                        "kind": "pypi",
                        "download_line": "amqp==2.5.2",
                        "version_specs": [("==", "2.5.2")],
                        "hashes": [
                            "sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545",
                            "sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8",
                        ],
                        "raw_package": "amqp",
                    },
                ],
                [],
            ),
            # Dependency from URL with egg name
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with package name
            (
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                        ),
                        "raw_package": "cnr_server",
                        "url": "https://github.com/quay/appr/archive/58c88e49.tar.gz",
                    },
                ],
                [],
            ),
            # Dependency from URL with both egg and package names
            (
                "ignored @ https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Editable dependency from URL
            (
                "-e https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "options": ["-e"],
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with hashes
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server "
                    "--hash=sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d91"
                    "2c7f55ec2e6c70c8"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                        ),
                        "hashes": [
                            "sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f55"
                            "ec2e6c70c8",
                        ],
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with a percent-escaped #cachito_hash
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                    "&cachito_hash=sha256%3A4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb3218"
                    "9d912c7f55ec2e6c70c8"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server&cachito_hash=sha256%3A4fd9429bfbb796a48c0bde6bd30"
                            "1ff5b3cc02adb32189d912c7f55ec2e6c70c8"
                        ),
                        "qualifiers": {
                            "egg": "cnr_server",
                            "cachito_hash": (
                                "sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f55"
                                "ec2e6c70c8"
                            ),
                        },
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                            "&cachito_hash=sha256%3A4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb3218"
                            "9d912c7f55ec2e6c70c8"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with environment markers
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server; "
                    'python_version < "2.7"'
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server"
                            ' ; python_version < "2.7"'
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "environment_marker": 'python_version < "2.7"',
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from URL with multiple qualifiers
            (
                (
                    "https://github.com/quay/appr/archive/58c88e49.tar.gz"
                    "#egg=cnr_server&spam=maps&bacon=nocab"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "url",
                        "download_line": (
                            "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                        "qualifiers": {"egg": "cnr_server", "spam": "maps", "bacon": "nocab"},
                        "raw_package": "cnr_server",
                        "url": (
                            "https://github.com/quay/appr/archive/58c88e49.tar.gz"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                    },
                ],
                [],
            ),
            # Dependency from VCS with egg name
            (
                "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                    },
                ],
                [],
            ),
            # Dependency from VCS with package name
            (
                "cnr_server @ git+https://github.com/quay/appr.git@58c88e49",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                        ),
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49",
                    },
                ],
                [],
            ),
            # Dependency from VCS with both egg and package names
            (
                "ignored @ git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server"
                        ),
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                    },
                ],
                [],
            ),
            # Editable dependency from VCS
            (
                "-e git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server"
                        ),
                        "options": ["-e"],
                        "qualifiers": {"egg": "cnr_server"},
                        "raw_package": "cnr_server",
                        "url": "git+https://github.com/quay/appr.git@58c88e49#egg=cnr_server",
                    },
                ],
                [],
            ),
            # Dependency from VCS with multiple qualifiers
            (
                (
                    "git+https://github.com/quay/appr.git@58c88e49"
                    "#egg=cnr_server&spam=maps&bacon=nocab"
                ),
                [
                    {
                        "package": "cnr-server",
                        "kind": "vcs",
                        "download_line": (
                            "cnr_server @ git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                        "qualifiers": {"egg": "cnr_server", "spam": "maps", "bacon": "nocab"},
                        "raw_package": "cnr_server",
                        "url": (
                            "git+https://github.com/quay/appr.git@58c88e49"
                            "#egg=cnr_server&spam=maps&bacon=nocab"
                        ),
                    },
                ],
                [],
            ),
            # No dependencies
            ("", [], []),
            # Comments are ignored
            (
                dedent(
                    """\
                    aiowsgi==0.7 # inline comment
                    # Line comment
                    asn1crypto==1.3.0 # inline comment \
                    with line continuation
                    # Line comment \
                    with line continuation
                        # Line comment with multiple leading white spaces
                    """
                ),
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                    {
                        "package": "asn1crypto",
                        "kind": "pypi",
                        "download_line": "asn1crypto==1.3.0",
                        "version_specs": [("==", "1.3.0")],
                        "raw_package": "asn1crypto",
                    },
                ],
                [],
            ),
            # Empty lines are ignored
            (
                dedent(
                    """\
                    aiowsgi==0.7
                            \

                    asn1crypto==1.3.0

                    """
                ),
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                    {
                        "package": "asn1crypto",
                        "kind": "pypi",
                        "download_line": "asn1crypto==1.3.0",
                        "version_specs": [("==", "1.3.0")],
                        "raw_package": "asn1crypto",
                    },
                ],
                [],
            ),
            # Line continuation is honored
            (
                dedent(
                    """\
                    aiowsgi\\
                    \\
                    ==\\
                    \\
                    \\
                    \\
                    0.7\\
                    """
                ),
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                [],
            ),
            # Global options
            (
                "--only-binary :all:",
                [],
                ["--only-binary", ":all:"],
            ),
            # Global options with a requirement
            (
                "aiowsgi==0.7 --only-binary :all:",
                [
                    {
                        "package": "aiowsgi",
                        "kind": "pypi",
                        "download_line": "aiowsgi==0.7",
                        "version_specs": [("==", "0.7")],
                        "raw_package": "aiowsgi",
                    },
                ],
                ["--only-binary", ":all:"],
            ),
        ),
    )
    def test_parsing_of_valid_cases(
        self,
        file_contents: str,
        expected_requirements: list[dict[str, str]],
        expected_global_options: list[dict[str, str]],
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Test the various valid use cases of requirements in a requirements file."""
        requirements_file = rooted_tmp_path.join_within_root("requirements.txt")
        requirements_file.path.write_text(file_contents)

        pip_requirements = PipRequirementsFile(requirements_file)

        assert pip_requirements.options == expected_global_options
        assert len(pip_requirements.requirements) == len(expected_requirements)
        for pip_requirement, expected_requirement in zip(
            pip_requirements.requirements, expected_requirements
        ):
            self._assert_pip_requirement(pip_requirement, expected_requirement)

    @pytest.mark.parametrize(
        "file_contents, expected_error",
        (
            # Invalid (probably) format
            ("--spam", "Unknown requirements file option '--spam'"),
            (
                "--prefer-binary=spam",
                "Unexpected value for requirements file option '--prefer-binary=spam'",
            ),
            ("--only-binary", "Requirements file option '--only-binary' requires a value"),
            ("aiowsgi --hash", "Requirements file option '--hash' requires a value"),
            (
                "-e",
                re.escape(
                    "Requirements file option(s) ['-e'] can only be applied to a requirement"
                ),
            ),
            (
                "aiowsgi==0.7 asn1crypto==1.3.0",
                "Unable to parse the requirement 'aiowsgi==0.7 asn1crypto==1.3.0'",
            ),
            (
                "cnr_server@foo@https://github.com/quay/appr/archive/58c88e49.tar.gz",
                "Unable to extract scheme from direct access requirement",
            ),
            # Valid format but we don't support it
            (
                "pip @ file:///localbuilds/pip-1.3.1.zip",
                UnsupportedFeature("Direct references with 'file' scheme are not supported"),
            ),
            (
                "file:///localbuilds/pip-1.3.1.zip",
                UnsupportedFeature("Direct references with 'file' scheme are not supported"),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz",
                UnsupportedFeature("Dependency name could not be determined from the requirement"),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=",
                UnsupportedFeature("Dependency name could not be determined from the requirement"),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg",
                UnsupportedFeature("Dependency name could not be determined from the requirement"),
            ),
        ),
    )
    def test_parsing_of_invalid_cases(
        self, file_contents: str, expected_error: Union[str, Exception], rooted_tmp_path: RootedPath
    ) -> None:
        """Test the invalid use cases of requirements in a requirements file."""
        requirements_file = rooted_tmp_path.join_within_root("requirements.txt")
        requirements_file.path.write_text(file_contents)

        pip_requirements = PipRequirementsFile(requirements_file)

        expected_err_type = (
            type(expected_error) if isinstance(expected_error, Exception) else UnexpectedFormat
        )

        with pytest.raises(expected_err_type, match=str(expected_error)):
            pip_requirements.requirements

    def test_corner_cases_when_parsing_single_line(self) -> None:
        """Test scenarios in PipRequirement that cannot be triggered via PipRequirementsFile."""
        # Empty lines are NOT ignored
        with pytest.raises(UnexpectedFormat, match="Unable to parse the requirement"):
            assert PipRequirement.from_line("     ", []) is None

        with pytest.raises(UnexpectedFormat, match="Unable to parse the requirement"):
            PipRequirement.from_line("aiowsgi==0.7 \nasn1crypto==1.3.0", [])

    def test_replace_requirements(self, rooted_tmp_path: RootedPath) -> None:
        """Test generating a new requirements file with replacements."""
        original_file_path = rooted_tmp_path.join_within_root("original-requirements.txt")
        new_file_path = rooted_tmp_path.join_within_root("new-requirements.txt")

        original_file_path.path.write_text(
            dedent(
                """\
                https://github.com/quay/appr/archive/58c88.tar.gz#egg=cnr_server --hash=sha256:123
                -e spam @ git+https://github.com/monty/spam.git@123456
                aiowsgi==0.7
                asn1crypto==1.3.0
                """
            )
        )

        # Mapping of the new URL value to be used in modified requirements
        new_urls = {
            "cnr_server": "https://cachito/nexus/58c88.tar.gz",
            "spam": "https://cachito/nexus/spam-123456.tar.gz",
            "asn1crypto": "https://cachito/nexus/asn1crypto-1.3.0.tar.gz",
        }

        # Mapping of the new hash values to be used in modified requirements
        new_hashes = {
            "spam": ["sha256:45678"],
            "aiowsgi": ["sha256:90123"],
            "asn1crypto": ["sha256:01234"],
        }

        expected_new_file = dedent(
            """\
            cnr_server @ https://cachito/nexus/58c88.tar.gz#egg=cnr_server --hash=sha256:123
            spam @ https://cachito/nexus/spam-123456.tar.gz --hash=sha256:45678
            aiowsgi==0.7 --hash=sha256:90123
            asn1crypto @ https://cachito/nexus/asn1crypto-1.3.0.tar.gz --hash=sha256:01234
            """
        )

        expected_attr_changes: dict[str, dict[str, Any]] = {
            "cnr_server": {
                "download_line": "cnr_server @ https://cachito/nexus/58c88.tar.gz#egg=cnr_server",
                "url": "https://cachito/nexus/58c88.tar.gz#egg=cnr_server",
            },
            "spam": {
                "hashes": ["sha256:45678"],
                "options": [],
                "kind": "url",
                "download_line": "spam @ https://cachito/nexus/spam-123456.tar.gz",
                "url": "https://cachito/nexus/spam-123456.tar.gz",
            },
            "aiowsgi": {"hashes": ["sha256:90123"]},
            "asn1crypto": {
                "download_line": "asn1crypto @ https://cachito/nexus/asn1crypto-1.3.0.tar.gz",
                "hashes": ["sha256:01234"],
                "kind": "url",
                "version_specs": [],
                "url": "https://cachito/nexus/asn1crypto-1.3.0.tar.gz",
            },
        }

        pip_requirements = PipRequirementsFile(original_file_path)

        new_requirements = []
        for pip_requirement in pip_requirements.requirements:
            url = new_urls.get(pip_requirement.raw_package)
            hashes = new_hashes.get(pip_requirement.raw_package)
            new_requirements.append(pip_requirement.copy(url=url, hashes=hashes))

        # Verify a new PipRequirementsFile can be loaded in memory and written correctly to disk.
        new_file = PipRequirementsFile.from_requirements_and_options(
            new_requirements, pip_requirements.options
        )

        assert new_file.generate_file_content() == expected_new_file

        with open(new_file_path, "w") as f:
            new_file.write(f)

        # Parse the newly generated requirements file to ensure it's parsed correctly.
        new_pip_requirements = PipRequirementsFile(new_file_path)

        assert new_pip_requirements.options == pip_requirements.options
        for new_pip_requirement, pip_requirement in zip(
            new_pip_requirements.requirements, pip_requirements.requirements
        ):
            for attr in self.PIP_REQUIREMENT_ATTRS:
                expected_value = expected_attr_changes.get(pip_requirement.raw_package, {}).get(
                    attr, getattr(pip_requirement, attr)
                )
                assert getattr(new_pip_requirement, attr) == expected_value, (
                    f"unexpected {attr!r} value for package {pip_requirement.raw_package!r}"
                )

    def test_write_requirements_file(self, rooted_tmp_path: RootedPath) -> None:
        """Test PipRequirementsFile.write method."""
        original_file_path = rooted_tmp_path.join_within_root("original-requirements.txt")
        new_file_path = rooted_tmp_path.join_within_root("test-requirements.txt")

        content = dedent(
            """\
            --only-binary :all:
            aiowsgi==0.7
            asn1crypto==1.3.0
            """
        )

        original_file_path.path.write_text(content)
        assert original_file_path.path.exists()
        pip_requirements = PipRequirementsFile(original_file_path)
        assert pip_requirements.requirements
        assert pip_requirements.options

        with open(new_file_path, "w") as f:
            pip_requirements.write(f)

        with open(new_file_path) as f:
            assert f.read() == content

    @pytest.mark.parametrize(
        "requirement_line, requirement_options, expected_str_line",
        (
            ("aiowsgi==1.2.3", [], "aiowsgi==1.2.3"),
            ("aiowsgi>=0.7", [], "aiowsgi>=0.7"),
            ('aiowsgi; python_version < "2.7"', [], 'aiowsgi; python_version < "2.7"'),
            (
                "amqp==2.5.2",
                [
                    "--hash",
                    "sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545",
                    "--hash",
                    "sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8",
                ],
                (
                    "amqp==2.5.2 --hash=sha256:6e649ca13a7df3faacdc8bbb280aa9a6602d22fd9d545 "
                    "--hash=sha256:77f1aef9410698d20eaeac5b73a87817365f457a507d8"
                ),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [],
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
            ),
            (
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz",
                [],
                "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz",
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["-e"],
                (
                    "-e cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz"
                    "#egg=cnr_server"
                ),
            ),
            (
                "https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["--hash", "sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f"],
                (
                    "cnr_server @ https://github.com/quay/appr/archive/58c88e49.tar.gz#"
                    "egg=cnr_server --hash=sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02ad"
                    "b32189d912c7f"
                ),
            ),
            (
                "git+https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                [],
                (
                    "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz#"
                    "egg=cnr_server"
                ),
            ),
            (
                "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz",
                [],
                "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz",
            ),
            (
                "git+https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["-e"],
                (
                    "-e cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz"
                    "#egg=cnr_server"
                ),
            ),
            (
                "git+https://github.com/quay/appr/archive/58c88e49.tar.gz#egg=cnr_server",
                ["--hash", "sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02adb32189d912c7f"],
                (
                    "cnr_server @ git+https://github.com/quay/appr/archive/58c88e49.tar.gz#"
                    "egg=cnr_server --hash=sh256:sha256:4fd9429bfbb796a48c0bde6bd301ff5b3cc02ad"
                    "b32189d912c7f"
                ),
            ),
        ),
    )
    def test_pip_requirement_to_str(
        self, requirement_line: str, requirement_options: list[str], expected_str_line: str
    ) -> None:
        """Test PipRequirement.__str__ method."""
        assert (
            str(PipRequirement.from_line(requirement_line, requirement_options))
            == expected_str_line
        )

    @pytest.mark.parametrize(
        "requirement_line, requirement_options, new_values, expected_changes",
        (
            # Existing hashes are retained
            ("spam", ["--hash", "sha256:123"], {}, {}),
            # Existing hashes are replaced
            (
                "spam",
                ["--hash", "sha256:123"],
                {"hashes": ["sha256:234"]},
                {"hashes": ["sha256:234"]},
            ),
            # Hashes are added
            ("spam", [], {"hashes": ["sha256:234"]}, {"hashes": ["sha256:234"]}),
            # pypi is modified to url
            (
                "spam",
                [],
                {"url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz"},
                {
                    "download_line": "spam @ https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                },
            ),
            # url is modified to another url
            (
                "https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # vcs is modified to URL
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Editable option, "-e", is dropped when setting url
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["-e"],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "options": [],
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Editable option, "--e", is not dropped when url is not set
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["-e"],
                {},
                {},
            ),
            # Editable option, "--editable", is dropped when setting url
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["--editable"],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam"
                    ),
                    "kind": "url",
                    "options": [],
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Editable option, "--editable", is not dropped when url is not set
            (
                "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam",
                ["--editable"],
                {},
                {},
            ),
            # Environment markers persist
            (
                (
                    "git+https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam"
                    '; python_version < "2.7"'
                ),
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam "
                        '; python_version < "2.7"'
                    ),
                    "kind": "url",
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam",
                },
            ),
            # Extras are cleared when setting a new URL
            (
                "spam[SALTY]",
                [],
                {"url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz"},
                {
                    "download_line": "spam @ https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                    "kind": "url",
                    "extras": [],
                    "url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                },
            ),
            # Extras are NOT cleared when a new URL is not set
            (
                "spam[SALTY]",
                [],
                {},
                {},
            ),
            # Version specs are cleared when setting a new URL
            (
                "spam==1.2.3",
                [],
                {"url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz"},
                {
                    "download_line": "spam @ https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                    "kind": "url",
                    "version_specs": [],
                    "url": "https://cachito.example.com/nexus/spam-1.2.3.tar.gz",
                },
            ),
            # Version specs are NOT cleared when a new URL is not set
            (
                "spam==1.2.3",
                [],
                {},
                {},
            ),
            # Qualifiers persists
            (
                "https://github.com/monty/spam/archive/58c88.tar.gz#egg=spam&spam=maps",
                [],
                {"url": "https://cachito.example.com/nexus/spam-58c88.tar.gz"},
                {
                    "download_line": (
                        "spam @ https://cachito.example.com/nexus/spam-58c88.tar.gz#"
                        "egg=spam&spam=maps"
                    ),
                    "url": "https://cachito.example.com/nexus/spam-58c88.tar.gz#egg=spam&spam=maps",
                },
            ),
        ),
    )
    def test_pip_requirement_copy(
        self,
        requirement_line: str,
        requirement_options: list[str],
        new_values: Union[dict[str, str], dict[str, list[str]]],
        expected_changes: dict[str, str],
    ) -> None:
        """Test PipRequirement.copy method."""
        original_requirement = PipRequirement.from_line(requirement_line, requirement_options)
        new_requirement = original_requirement.copy(**new_values)

        for attr in self.PIP_REQUIREMENT_ATTRS:
            expected_changes.setdefault(attr, getattr(original_requirement, attr))

        self._assert_pip_requirement(new_requirement, expected_changes)

    def test_invalid_kind_for_url(self) -> None:
        """Test extracting URL from a requirement that does not have one."""
        requirement = PipRequirement()
        requirement.download_line = "aiowsgi==0.7"
        requirement.kind = "pypi"

        with pytest.raises(ValueError, match="Cannot extract URL from pypi requirement"):
            _ = requirement.url

    def _assert_pip_requirement(self, pip_requirement: Any, expected_requirement: Any) -> None:
        for attr, default_value in self.PIP_REQUIREMENT_ATTRS.items():
            expected_requirement.setdefault(attr, default_value)

        for attr, expected_value in expected_requirement.items():
            if attr in ("version_specs", "extras"):
                # Account for differences in order
                assert set(getattr(pip_requirement, attr)) == set(expected_value), (
                    f"unexpected value for {attr!r}"
                )
            else:
                assert getattr(pip_requirement, attr) == expected_value, (
                    f"unexpected value for {attr!r}"
                )
