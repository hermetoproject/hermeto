# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pypi_simple
import pytest

from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.package_managers.pip.packages import (
    PyPIPackage,
    URLPackage,
    VCSPackage,
)

CUSTOM_PYPI_ENDPOINT = "https://my-pypi.org/simple/"
GIT_REF = "a" * 40

_PATH = Path("/deps/pip/pkg.tar.gz")
_REQ_FILE = "requirements.txt"


@pytest.mark.parametrize(
    "dep, expected_purl",
    [
        pytest.param(
            PyPIPackage(
                name="pypi_package",
                path=_PATH,
                requirement_file=_REQ_FILE,
                missing_req_file_checksum=False,
                package_type="sdist",
                version="1.0.0",
                index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
            ),
            "pkg:pypi/pypi-package@1.0.0",
            id="pypi-default-index",
        ),
        pytest.param(
            PyPIPackage(
                name="mypypi_package",
                path=_PATH,
                requirement_file=_REQ_FILE,
                missing_req_file_checksum=False,
                package_type="sdist",
                version="2.0.0",
                index_url=CUSTOM_PYPI_ENDPOINT,
            ),
            f"pkg:pypi/mypypi-package@2.0.0?repository_url={CUSTOM_PYPI_ENDPOINT}",
            id="pypi-custom-index",
        ),
        pytest.param(
            VCSPackage(
                name="git_dependency",
                path=_PATH,
                requirement_file=_REQ_FILE,
                missing_req_file_checksum=False,
                package_type="sdist",
                url="https://github.com/my-org/git_dependency",
                ref=GIT_REF,
            ),
            f"pkg:pypi/git-dependency?vcs_url=git%2Bhttps://github.com/my-org/git_dependency%40{GIT_REF}",
            id="vcs-https",
        ),
        pytest.param(
            VCSPackage(
                name="Git_dependency",
                path=_PATH,
                requirement_file=_REQ_FILE,
                missing_req_file_checksum=False,
                package_type="sdist",
                url="file:///github.com/my-org/git_dependency",
                ref=GIT_REF,
            ),
            f"pkg:pypi/git-dependency?vcs_url=git%2Bfile:///github.com/my-org/git_dependency%40{GIT_REF}",
            id="vcs-file",
        ),
        pytest.param(
            VCSPackage(
                name="git_dependency",
                path=_PATH,
                requirement_file=_REQ_FILE,
                missing_req_file_checksum=False,
                package_type="sdist",
                url="ssh://git@github.com/my-org/git_dependency",
                ref=GIT_REF,
            ),
            f"pkg:pypi/git-dependency?vcs_url=git%2Bssh://git%40github.com/my-org/git_dependency%40{GIT_REF}",
            id="vcs-ssh",
        ),
        pytest.param(
            URLPackage(
                name="https_dependency",
                path=_PATH,
                requirement_file=_REQ_FILE,
                missing_req_file_checksum=False,
                package_type="sdist",
                original_url=f"https://github.com/my-org/https_dependency/{GIT_REF}/file.tar.gz",
                checksum="sha256:de526c1",
            ),
            f"pkg:pypi/https-dependency?checksum=sha256:de526c1&download_url=https://github.com/my-org/https_dependency/{GIT_REF}/file.tar.gz",
            id="url",
        ),
    ],
)
def test_make_purl(dep: PyPIPackage | VCSPackage | URLPackage, expected_purl: str) -> None:
    assert dep._make_purl() == expected_purl


def test_to_component_missing_checksum_populates_missing_hash_property() -> None:
    """When the requirements file has no checksum, the component records which file is missing it."""
    pkg = PyPIPackage(
        name="foo",
        path=_PATH,
        requirement_file=_REQ_FILE,
        missing_req_file_checksum=True,
        package_type="sdist",
        version="1.0",
        index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
    )

    component = pkg.to_component(build_dependency=False)
    props = PropertySet.from_properties(component.properties)

    assert props.missing_hash_in_file == frozenset({_REQ_FILE})


def test_to_component_with_checksum_has_empty_missing_hash_property() -> None:
    """When the requirements file provides a checksum, missing_hash_in_file is empty."""
    pkg = PyPIPackage(
        name="foo",
        path=_PATH,
        requirement_file=_REQ_FILE,
        missing_req_file_checksum=False,
        package_type="sdist",
        version="1.0",
        index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
    )

    component = pkg.to_component(build_dependency=False)
    props = PropertySet.from_properties(component.properties)

    assert props.missing_hash_in_file == frozenset()


def test_to_component_pypi_package_has_version() -> None:
    """PyPI packages carry their resolved version into the SBOM component."""
    pkg = PyPIPackage(
        name="foo",
        path=_PATH,
        requirement_file=_REQ_FILE,
        missing_req_file_checksum=False,
        package_type="sdist",
        version="2.5.0",
        index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
    )

    component = pkg.to_component(build_dependency=False)

    assert component.version == "2.5.0"


@pytest.mark.parametrize(
    "dep",
    [
        pytest.param(
            VCSPackage(
                name="bar",
                path=_PATH,
                requirement_file=_REQ_FILE,
                missing_req_file_checksum=False,
                package_type="",
                url="https://github.com/org/bar",
                ref=GIT_REF,
            ),
            id="vcs",
        ),
        pytest.param(
            URLPackage(
                name="baz",
                path=_PATH,
                requirement_file=_REQ_FILE,
                missing_req_file_checksum=False,
                package_type="sdist",
                original_url="https://example.com/baz-1.0.tar.gz",
                checksum="sha256:abc123",
            ),
            id="url",
        ),
    ],
)
def test_to_component_non_pypi_package_has_no_version(dep: VCSPackage | URLPackage) -> None:
    """VCS and URL packages have no meaningful version for the SBOM component."""
    component = dep.to_component(build_dependency=False)

    assert component.version is None
