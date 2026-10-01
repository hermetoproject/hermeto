# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import InvalidInput, PackageRejected, UnsupportedFeature
from hermeto.core.models.input import PipBinaryFilters
from hermeto.core.models.property_semantics import PropertySet
from hermeto.core.package_managers.python.packaging_tool import PythonPackagingTool
from hermeto.core.package_managers.python.pip import lockfile as pip
from hermeto.core.package_managers.python.pip.packages import (
    PipPackage,
    PyPIPackage,
    URLPackage,
    VCSPackage,
)
from hermeto.core.package_managers.python.pylock.models import Pylock
from hermeto.core.rooted_path import RootedPath
from tests.common_utils import GIT_REF

CUSTOM_PYPI_ENDPOINT = "https://my-pypi.org/simple/"


def make_pylock(packages: list[dict[str, Any]]) -> Pylock:
    """Build a minimal valid Pylock model from package tables."""
    return Pylock.model_validate(
        {"lock-version": "1.0", "created-by": "test", "packages": packages}
    )


@pytest.mark.parametrize(
    "packaging_tool, filename, expected",
    [
        (None, "requirements.txt", PythonPackagingTool.REQUIREMENTS),
        (None, "requirement.txt", PythonPackagingTool.REQUIREMENTS),
        (None, "requirements-build.txt", PythonPackagingTool.REQUIREMENTS),
        (None, "pylock.toml", PythonPackagingTool.PYLOCK),
        (None, "pylock.dev.toml", PythonPackagingTool.PYLOCK),
        (None, None, PythonPackagingTool.REQUIREMENTS),
        (PythonPackagingTool.PYLOCK, "requirements.txt", PythonPackagingTool.PYLOCK),
        (PythonPackagingTool.REQUIREMENTS, "pylock.toml", PythonPackagingTool.REQUIREMENTS),
    ],
)
def test_infer_packaging_tool(
    packaging_tool: PythonPackagingTool | None, filename: str | None, expected: PythonPackagingTool
) -> None:
    """The format follows an explicit tool, otherwise the lockfile filename."""
    lockfile = Path(filename) if filename is not None else None
    assert pip._infer_packaging_tool(packaging_tool, lockfile) == expected


def test_infer_packaging_tool_unknown_filename_is_rejected() -> None:
    with pytest.raises(InvalidInput):
        pip._infer_packaging_tool(None, Path("deps.lock"))


class TestDownloadDependencies:
    """Tests for the single shared download pipeline used by all lockfile formats."""

    @mock.patch("hermeto.core.package_managers.python.pip.lockfile.async_download_files")
    @mock.patch("hermeto.core.package_managers.python.pip.packages.download_binary_file")
    @mock.patch("hermeto.core.package_managers.python.pip.packages.clone_as_tarball")
    @mock.patch(
        "hermeto.core.package_managers.python.pip.packages._checksum_must_match_or_path_unlink",
        return_value=True,
    )
    @mock.patch("hermeto.core.package_managers.python.pip.packages._check_metadata_in_sdist")
    def test_dispatches_by_type(
        self,
        mock_metadata: Any,
        mock_checksum: Any,
        mock_clone: Any,
        mock_download: Any,
        mock_async: Any,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """vcs is cloned, url is downloaded sync, index is batched — all become components."""
        deps = [
            VCSPackage(
                name="vcs-pkg",
                requirement_file="pylock.toml",
                missing_req_file_checksum=True,
                package_type="",
                url="https://github.com/spam/eggs",
                ref=GIT_REF,
            ),
            URLPackage(
                name="url-pkg",
                requirement_file="pylock.toml",
                missing_req_file_checksum=False,
                package_type="",
                original_url="https://example.org/url_pkg.tar.gz",
                checksum="sha256:abcdef",
            ),
            PyPIPackage(
                name="index-pkg",
                requirement_file="pylock.toml",
                missing_req_file_checksum=False,
                package_type="sdist",
                version="1.0",
                index_url="https://pypi.org/simple/",
                url="https://files.example.org/index_pkg-1.0.tar.gz",
            ),
        ]

        result = pip._download_dependencies(deps, rooted_tmp_path)

        by_type = sorted(type(d).__name__ for d in result)
        assert by_type == ["PyPIPackage", "URLPackage", "VCSPackage"]
        mock_clone.assert_called_once()
        mock_download.assert_called_once()
        mock_async.assert_called_once()

    @mock.patch("hermeto.core.package_managers.python.pip.lockfile.async_download_files")
    @mock.patch("hermeto.core.package_managers.python.pip.packages.download_binary_file")
    @mock.patch(
        "hermeto.core.package_managers.python.pip.packages._checksum_must_match_or_path_unlink",
        return_value=False,
    )
    def test_url_dropped_on_checksum_mismatch(
        self,
        mock_checksum: Any,
        mock_download: Any,
        mock_async: Any,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """A URL package whose checksum does not match is dropped from the result."""
        deps: list[PipPackage] = [
            URLPackage(
                name="url-pkg",
                requirement_file="reqs.txt",
                missing_req_file_checksum=False,
                package_type="",
                original_url="https://example.org/url_pkg.tar.gz",
                checksum="sha256:abcdef",
            ),
        ]

        assert pip._download_dependencies(deps, rooted_tmp_path) == []

    @mock.patch("hermeto.core.package_managers.python.pip.lockfile.async_download_files")
    def test_index_batch_skips_existing_and_sets_auth_header(
        self,
        mock_async: Any,
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Only missing files are batched, and proxy auth becomes a per-URL header."""
        existing = PyPIPackage(
            name="cached",
            requirement_file="reqs.txt",
            missing_req_file_checksum=False,
            package_type="sdist",
            version="1.0",
            index_url="https://pypi.org/simple/",
            url="https://files.example.org/cached-1.0.tar.gz",
        )
        # Pre-create the destination so it is skipped by the batch.
        existing.download_location(rooted_tmp_path).path.touch()
        fresh = PyPIPackage(
            name="fresh",
            requirement_file="reqs.txt",
            missing_req_file_checksum=False,
            package_type="sdist",
            version="1.0",
            index_url="https://pypi.org/simple/",
            url="https://files.example.org/fresh-1.0.tar.gz",
            auth_header="Basic dXNlcjpwYXNz",
        )

        with (
            mock.patch(
                "hermeto.core.package_managers.python.pip.packages._checksum_must_match_or_path_unlink",
                return_value=True,
            ),
            mock.patch(
                "hermeto.core.package_managers.python.pip.packages._check_metadata_in_sdist"
            ),
        ):
            pip._download_dependencies([existing, fresh], rooted_tmp_path)

        files_arg, _ = mock_async.call_args[0][0], mock_async.call_args
        assert list(files_arg) == [fresh.url]
        headers = mock_async.call_args.kwargs["headers"]
        assert headers == {fresh.url: {"Authorization": "Basic dXNlcjpwYXNz"}}


class TestPylockLockfile:
    """Tests for extracting dependencies from a pylock.toml."""

    def _lockfile(self, packages: list[dict[str, Any]], rooted_tmp_path: RootedPath) -> Any:
        return pip.PylockLockfile(
            make_pylock(packages), rooted_tmp_path.join_within_root("pylock.toml")
        )

    def test_maps_each_source_kind(self, rooted_tmp_path: RootedPath) -> None:
        """vcs -> VCSPackage, archive -> URLPackage, index -> PyPIPackage (sdist)."""
        lockfile = self._lockfile(
            [
                {
                    "name": "vcs-pkg",
                    "vcs": {
                        "type": "git",
                        "url": "https://github.com/spam/eggs",
                        "commit-id": GIT_REF,
                    },
                },
                {
                    "name": "archive-pkg",
                    "archive": {
                        "url": "https://example.org/a.tar.gz",
                        "hashes": {"sha256": "abcdef"},
                    },
                },
                {
                    "name": "Index_Pkg",
                    "version": "1.0",
                    "index": CUSTOM_PYPI_ENDPOINT,
                    "sdist": {
                        "url": "https://files.example.org/index_pkg-1.0.tar.gz",
                        "hashes": {"sha256": "123456"},
                    },
                },
            ],
            rooted_tmp_path,
        )

        vcs, archive, index = lockfile.dependencies(None, rooted_tmp_path)

        assert isinstance(vcs, VCSPackage)
        assert (vcs.url, vcs.ref) == ("https://github.com/spam/eggs", GIT_REF)
        assert vcs.missing_req_file_checksum is True
        assert vcs.requirement_file == "pylock.toml"

        assert isinstance(archive, URLPackage)
        assert archive.original_url == "https://example.org/a.tar.gz"
        assert archive.checksum == "sha256:abcdef"
        assert archive.insecure is False

        assert isinstance(index, PyPIPackage)
        assert index.package_type == "sdist"
        assert index.version == "1.0"
        assert index.index_url == CUSTOM_PYPI_ENDPOINT
        assert index.url == "https://files.example.org/index_pkg-1.0.tar.gz"
        assert index.name == "index-pkg"  # canonicalized

    def test_archive_wheel_is_reported_as_binary(self, rooted_tmp_path: RootedPath) -> None:
        """An archive URL pointing at a wheel is typed as binary; a source archive is not."""
        lockfile = self._lockfile(
            [
                {
                    "name": "whl-archive",
                    "archive": {
                        "url": "https://example.org/foo-1.0-py3-none-any.whl",
                        "hashes": {"sha256": "aa"},
                    },
                },
                {
                    "name": "src-archive",
                    "archive": {
                        "url": "https://example.org/foo-1.0.tar.gz",
                        "hashes": {"sha256": "bb"},
                    },
                },
            ],
            rooted_tmp_path,
        )

        whl, src = lockfile.dependencies(None, rooted_tmp_path)

        assert whl.package_type == "wheel"
        assert (
            PropertySet.from_properties(
                whl.to_component(build_dependency=False).properties
            ).pip_package_binary
            is True
        )
        assert src.package_type == ""
        assert (
            PropertySet.from_properties(
                src.to_component(build_dependency=False).properties
            ).pip_package_binary
            is False
        )

    def test_index_without_hashes_is_rejected(self, rooted_tmp_path: RootedPath) -> None:
        """A hashless index artifact is rejected."""
        with pytest.raises(PackageRejected, match="without hashes"):
            self._lockfile(
                [
                    {
                        "name": "nohash",
                        "version": "1.0",
                        "sdist": {"url": "https://files.example.org/nohash-1.0.tar.gz"},
                    }
                ],
                rooted_tmp_path,
            )

    def test_source_only_requires_sdist(self, rooted_tmp_path: RootedPath) -> None:
        """A wheels-only index package cannot be fetched in source-only mode."""
        lockfile = self._lockfile(
            [
                {
                    "name": "wheel-only",
                    "version": "1.0",
                    "wheels": [
                        {
                            "url": "https://example.org/wheel_only-1.0-py3-none-any.whl",
                            "hashes": {"sha256": "abcdef"},
                        }
                    ],
                }
            ],
            rooted_tmp_path,
        )
        with pytest.raises(UnsupportedFeature, match="no source distribution"):
            lockfile.dependencies(None, rooted_tmp_path)

    # Only the Linux wheel matches the default filters.
    _BINARY_PACKAGE = {
        "name": "index-pkg",
        "version": "1.0",
        "sdist": {
            "url": "https://files.example.org/index_pkg-1.0.tar.gz",
            "hashes": {"sha256": "aa"},
        },
        "wheels": [
            {
                "url": "https://files.example.org/index_pkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl",
                "hashes": {"sha256": "bb"},
            },
            {
                "url": "https://files.example.org/index_pkg-1.0-cp312-cp312-win_amd64.whl",
                "hashes": {"sha256": "cc"},
            },
        ],
    }

    @pytest.mark.parametrize(
        "binary_filters, expected_types",
        [
            pytest.param(None, ["sdist"], id="source_only"),
            pytest.param(PipBinaryFilters(), ["wheel", "sdist"], id="prefer_binary_plus_fallback"),
            pytest.param(
                PipBinaryFilters(packages="index-pkg"), ["wheel"], id="binary_only_targeted"
            ),
            pytest.param(
                PipBinaryFilters(packages="other-pkg"), ["sdist"], id="untargeted_uses_sdist"
            ),
            pytest.param(
                PipBinaryFilters(platform="no-such-platform"),
                ["sdist"],
                id="no_matching_wheel_falls_back_to_sdist",
            ),
        ],
    )
    def test_wheel_selection(
        self,
        binary_filters: PipBinaryFilters | None,
        expected_types: list[str],
        rooted_tmp_path: RootedPath,
    ) -> None:
        """Binary filters select wheels straight from the lockfile, with sdist fallback."""
        lockfile = self._lockfile([self._BINARY_PACKAGE], rooted_tmp_path)

        deps = lockfile.dependencies(binary_filters, rooted_tmp_path)

        assert [d.package_type for d in deps] == expected_types
        assert not any("win_amd64" in d.url for d in deps)

    def test_binary_only_without_match_is_rejected(self, rooted_tmp_path: RootedPath) -> None:
        """Requesting binary-only for a package with no matching wheel is rejected."""
        lockfile = self._lockfile(
            [
                {
                    "name": "index-pkg",
                    "version": "1.0",
                    "wheels": [
                        {
                            "url": "https://files.example.org/index_pkg-1.0-cp312-cp312-win_amd64.whl",
                            "hashes": {"sha256": "cc"},
                        }
                    ],
                }
            ],
            rooted_tmp_path,
        )
        with pytest.raises(PackageRejected, match="No wheels"):
            lockfile.dependencies(PipBinaryFilters(packages="index-pkg"), rooted_tmp_path)

    def test_rewrite_redirects_vcs_url_to_local_path(self, rooted_tmp_path: RootedPath) -> None:
        """rewrite() turns a VCS source into a hashed local archive source."""
        path = rooted_tmp_path.join_within_root("pylock.toml")
        path.path.write_text(
            'lock-version = "1.0"\n'
            'created-by = "test"\n'
            "[[packages]]\n"
            'name = "gitpython"\n'
            "[packages.vcs]\n"
            'type = "git"\n'
            'url = "https://github.com/gitpython-developers/GitPython.git"\n'
            f'commit-id = "{GIT_REF}"\n'
        )
        archive_path = rooted_tmp_path.join_within_root(
            "deps", "pip", f"GitPython-gitcommit-{GIT_REF}.tar.gz"
        )
        archive_path.path.parent.mkdir(parents=True)
        archive_path.path.write_bytes(b"prefetched archive")
        dependency = VCSPackage(
            name="gitpython",
            requirement_file="pylock.toml",
            missing_req_file_checksum=True,
            package_type="",
            url="https://github.com/gitpython-developers/GitPython.git",
            ref=GIT_REF,
        )
        dependency.path = archive_path.path

        project_file = pip.PylockLockfile.from_file(path).rewrite([dependency])

        assert project_file is not None
        assert (
            f"file://${{output_dir}}/deps/pip/GitPython-gitcommit-{GIT_REF}.tar.gz"
            in project_file.template
        )
        assert "[packages.vcs]" not in project_file.template
        assert "[packages.archive]" in project_file.template

    def test_rewrite_reuses_parsed_document(self, rooted_tmp_path: RootedPath) -> None:
        """rewrite() edits the document parsed by from_file, not a fresh disk read."""
        path = rooted_tmp_path.join_within_root("pylock.toml")
        path.path.write_text(
            'lock-version = "1.0"\n'
            'created-by = "test"\n'
            "[[packages]]\n"
            'name = "gitpython"\n'
            "[packages.vcs]\n"
            'type = "git"\n'
            'url = "https://github.com/gitpython-developers/GitPython.git"\n'
            f'commit-id = "{GIT_REF}"\n'
        )
        lockfile = pip.PylockLockfile.from_file(path)
        archive_path = rooted_tmp_path.join_within_root("prefetched.tar.gz")
        archive_path.path.write_bytes(b"prefetched archive")
        dependency = VCSPackage(
            name="gitpython",
            requirement_file="pylock.toml",
            missing_req_file_checksum=True,
            package_type="",
            url="https://github.com/gitpython-developers/GitPython.git",
            ref=GIT_REF,
        )
        dependency.path = archive_path.path

        with mock.patch.object(
            Path, "read_text", side_effect=AssertionError("rewrite re-read the lockfile from disk")
        ):
            project_file = lockfile.rewrite([dependency])

        assert project_file is not None
        assert f"GitPython-gitcommit-{GIT_REF}.tar.gz" in project_file.template

    def test_rewrite_returns_none_for_index_only_lockfile(
        self, rooted_tmp_path: RootedPath
    ) -> None:
        """Index-only lockfiles need no rewrite (artifacts are found via PIP_FIND_LINKS)."""
        path = rooted_tmp_path.join_within_root("pylock.toml")
        path.path.write_text(
            'lock-version = "1.0"\n'
            'created-by = "test"\n'
            "[[packages]]\n"
            'name = "click"\n'
            'version = "8.1.7"\n'
            "[packages.sdist]\n"
            'url = "https://example.org/click-8.1.7.tar.gz"\n'
            "[packages.sdist.hashes]\n"
            'sha256 = "abc"\n'
        )

        assert pip.PylockLockfile.from_file(path).rewrite([]) is None

    def test_root_directory_package_is_skipped(self, rooted_tmp_path: RootedPath) -> None:
        """The project's own directory entry (path '.') yields no dependency to fetch."""
        lockfile = self._lockfile([{"name": "myproj", "directory": {"path": "."}}], rooted_tmp_path)

        assert lockfile.dependencies(None, rooted_tmp_path) == []

    def test_archive_verifies_all_recorded_hashes(self, rooted_tmp_path: RootedPath) -> None:
        """Every recorded archive hash is kept for verification; the purl keeps one."""
        lockfile = self._lockfile(
            [
                {
                    "name": "arch",
                    "archive": {
                        "url": "https://example.org/a.tar.gz",
                        "hashes": {"sha256": "aa", "sha512": "bb"},
                    },
                }
            ],
            rooted_tmp_path,
        )

        (dep,) = lockfile.dependencies(None, rooted_tmp_path)

        assert isinstance(dep, URLPackage)
        assert dep.checksums_to_match == {
            ChecksumInfo("sha256", "aa"),
            ChecksumInfo("sha512", "bb"),
        }
        assert dep.checksum == "sha256:aa"

    def test_index_downloads_route_through_configured_proxy(
        self, rooted_tmp_path: RootedPath
    ) -> None:
        """Standard-PyPI artifacts go through the proxy with auth; custom indexes don't."""
        lockfile = self._lockfile(
            [
                {
                    "name": "std",
                    "version": "1.0",
                    "sdist": {
                        "url": "https://files.pythonhosted.org/packages/aa/bb/std-1.0.tar.gz",
                        "hashes": {"sha256": "aa"},
                    },
                },
                {
                    "name": "custom",
                    "version": "2.0",
                    "index": CUSTOM_PYPI_ENDPOINT,
                    "sdist": {
                        "url": "https://files.example.org/custom-2.0.tar.gz",
                        "hashes": {"sha256": "bb"},
                    },
                },
            ],
            rooted_tmp_path,
        )
        config = mock.Mock()
        config.pip.proxy_url = "http://proxy.local/pypi"
        config.pip.proxy_login = "user"
        config.pip.proxy_password.get_secret_value.return_value = "pw"

        with mock.patch(
            "hermeto.core.package_managers.python.pip.lockfile.get_config", return_value=config
        ):
            std, custom = lockfile.dependencies(None, rooted_tmp_path)

        # Standard PyPI is proxied with auth.
        assert std.url == "http://proxy.local/pypi/packages/aa/bb/std-1.0.tar.gz"
        assert std.auth_header == "Basic dXNlcjpwdw=="
        assert std.proxy_url == "http://proxy.local/pypi"
        # Custom indexes remain untouched.
        assert custom.url == "https://files.example.org/custom-2.0.tar.gz"
        assert custom.auth_header is None
        assert custom.proxy_url is None

    def test_proxy_applies_to_standard_index_without_trailing_slash(
        self, rooted_tmp_path: RootedPath
    ) -> None:
        """`index` spelled without a trailing slash still counts as standard PyPI."""
        lockfile = self._lockfile(
            [
                {
                    "name": "std",
                    "version": "1.0",
                    # pip may omit the trailing slash.
                    "index": "https://pypi.org/simple",
                    "sdist": {
                        "url": "https://files.pythonhosted.org/packages/aa/bb/std-1.0.tar.gz",
                        "hashes": {"sha256": "aa"},
                    },
                }
            ],
            rooted_tmp_path,
        )
        config = mock.Mock()
        config.pip.proxy_url = "http://proxy.local/pypi"
        config.pip.proxy_login = "user"
        config.pip.proxy_password.get_secret_value.return_value = "pw"

        with mock.patch(
            "hermeto.core.package_managers.python.pip.lockfile.get_config", return_value=config
        ):
            (std,) = lockfile.dependencies(None, rooted_tmp_path)

        assert std.url == "http://proxy.local/pypi/packages/aa/bb/std-1.0.tar.gz"
        assert std.proxy_url == "http://proxy.local/pypi"
