# SPDX-License-Identifier: GPL-3.0-only

import hashlib

import pypi_simple
import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.models.sbom import PROXY_COMMENT, PROXY_REF_TYPE
from hermeto.core.package_managers.python.pip.packages import (
    PyPIPackage,
    URLPackage,
    VCSPackage,
)
from hermeto.core.rooted_path import RootedPath

CUSTOM_PYPI_ENDPOINT = "https://my-pypi.org/simple/"
GIT_REF = "a" * 40

_REQ_FILE = "requirements.txt"


@pytest.mark.parametrize(
    "dep, expected_purl",
    [
        pytest.param(
            PyPIPackage(
                name="pypi_package",
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


def test_to_component_without_proxy_has_no_external_refs() -> None:
    """A PyPI package fetched without a proxy has no external references."""
    pkg = PyPIPackage(
        name="foo",
        requirement_file=_REQ_FILE,
        missing_req_file_checksum=False,
        package_type="sdist",
        version="1.0",
        index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
    )

    component = pkg.to_component(build_dependency=False)

    assert component.external_references is None


def test_to_component_with_proxy_attaches_external_ref() -> None:
    """A PyPI package fetched through a proxy records the proxy URL as an external reference."""
    proxy = "https://pypi-proxy.example.com/simple/"
    pkg = PyPIPackage(
        name="foo",
        requirement_file=_REQ_FILE,
        missing_req_file_checksum=False,
        package_type="sdist",
        version="1.0",
        index_url=pypi_simple.PYPI_SIMPLE_ENDPOINT,
        proxy_url=proxy,
    )

    component = pkg.to_component(build_dependency=False)

    assert component.external_references is not None
    assert len(component.external_references) == 1
    ref = component.external_references[0]
    assert ref.url == proxy
    assert ref.type == PROXY_REF_TYPE
    assert ref.comment == PROXY_COMMENT


def _url_package(**overrides: object) -> URLPackage:
    kwargs: dict[str, object] = dict(
        name="foo",
        requirement_file=_REQ_FILE,
        missing_req_file_checksum=False,
        package_type="",
        original_url="https://example.org/foo.tar.gz",
        checksum="",
    )
    kwargs.update(overrides)
    return URLPackage(**kwargs)  # type: ignore[arg-type]


def test_url_package_verify_matches_any_recorded_hash(rooted_tmp_path: RootedPath) -> None:
    """A URL package with multiple recorded hashes verifies if any algorithm matches."""
    downloaded = rooted_tmp_path.join_within_root("foo.tar.gz")
    downloaded.path.write_bytes(b"payload")
    sha256 = hashlib.sha256(b"payload").hexdigest()

    pkg = _url_package(
        checksum="sha512:deadbeef",
        checksums_to_match={ChecksumInfo("sha512", "deadbeef"), ChecksumInfo("sha256", sha256)},
    )
    pkg.path = downloaded.path

    assert pkg.verify() is True


def test_url_package_verify_falls_back_to_single_checksum(rooted_tmp_path: RootedPath) -> None:
    """Without a hash set, verification falls back to the single ``checksum``."""
    downloaded = rooted_tmp_path.join_within_root("foo.tar.gz")
    downloaded.path.write_bytes(b"payload")
    sha256 = hashlib.sha256(b"payload").hexdigest()

    pkg = _url_package(checksum=f"sha256:{sha256}")
    pkg.path = downloaded.path

    assert pkg.verify() is True
