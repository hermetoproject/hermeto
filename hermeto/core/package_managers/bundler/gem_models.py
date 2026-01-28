# SPDX-License-Identifier: GPL-3.0-only
import logging
from functools import cached_property
from pathlib import Path
from typing import Annotated
from urllib.parse import urljoin, urlparse

import pydantic
from packageurl import PackageURL
from typing_extensions import Self

from hermeto.core.config import get_config
from hermeto.core.constants import Mode
from hermeto.core.errors import NotAGitRepo
from hermeto.core.package_managers.general import download_binary_file, get_vcs_qualifiers
from hermeto.core.rooted_path import PathOutsideRoot, RootedPath
from hermeto.core.scm import GitRepo

AcceptedUrl = Annotated[
    pydantic.HttpUrl,
    pydantic.UrlConstraints(allowed_schemes=["https"]),
]

AcceptedGitRef = Annotated[
    pydantic.StrictStr,
    pydantic.StringConstraints(pattern=r"^[a-fA-F0-9]{40}$"),
]

log = logging.getLogger(__name__)


class _GemMetadata(pydantic.BaseModel):
    """
    Base class for gem metadata.

    Attributes:
        name:       The name of the gem.
        version:    The version of the gem.
    """

    name: str
    version: str

    def download_to(self, deps_dir: RootedPath) -> None:  # noqa: ARG002
        """Download gem to the specified directory."""
        return None


class GemDependency(_GemMetadata):
    """
    Represents a gem dependency.

    Attributes:
        source:     The source URL of the gem as stated in 'remote' field from Gemfile.lock.
        checksum:   The checksum of the gem.
    """

    source: str
    checksum: str | None = None

    @cached_property
    def purl(self) -> str:
        """Get PURL for this dependency."""
        purl = PackageURL(type="gem", name=self.name, version=self.version)
        return purl.to_string()

    @cached_property
    def remote_location(self) -> str:
        """Return remote location to download this gem from."""
        return urljoin(self.source, f"downloads/{self.name}-{self.version}.gem")

    def download_to(self, deps_dir: RootedPath) -> None:
        """Download represented gem to specified file system location."""
        fs_location = deps_dir.join_within_root(Path(f"{self.name}-{self.version}.gem"))
        log.info("Downloading gem %s", self.name)
        download_binary_file(self.remote_location, fs_location)


class GemPlatformSpecificDependency(GemDependency):
    """
    Represents a gem dependency built for a specific platform.

    Attributes:
        platform:     Platform for which the dependency was built.
    """

    platform: str

    @property
    def remote_location(self) -> str:
        """Return remote location to download this gem from."""
        return urljoin(self.source, f"downloads/{self.name}-{self.version}-{self.platform}.gem")

    def download_to(self, deps_dir: RootedPath) -> None:
        """Download represented gem to specified file system location."""
        fs_location = deps_dir.join_within_root(
            Path(f"{self.name}-{self.version}-{self.platform}.gem")
        )
        log.info(
            "Downloading platform-specific gem %s-%s-%s", self.name, self.version, self.platform
        )
        # A combination of Ruby v.3.0.7 and some Bundler dependencies results in
        # -gnu suffix being dropped from some platforms. This was observed on
        # sqlite3-aarch-linux-gnu. We discourage using outdated platforms
        # for building dependencies and cnsider this to be a limitation of Ruby.
        download_binary_file(self.remote_location, fs_location)


class GitDependency(_GemMetadata):
    """
    Represents a git dependency.

    Attributes:
        url:        The URL of the git repository.
        branch:     The branch to checkout.
        ref:        Commit hash.
    """

    url: AcceptedUrl
    branch: str | None = None
    ref: AcceptedGitRef

    @cached_property
    def purl(self) -> str:
        """Get PURL for this dependency."""
        qualifiers = {"vcs_url": f"git+{str(self.url)}@{self.ref}"}
        purl = PackageURL(type="gem", name=self.name, version=self.version, qualifiers=qualifiers)
        return purl.to_string()

    @cached_property
    def repo_name(self) -> str:
        """Extract the repository name from the URL."""
        parse_result = urlparse(str(self.url))
        return Path(parse_result.path).stem

    def download_to(self, deps_dir: RootedPath) -> None:
        """Download git repository to the output directory with a specific name."""
        short_ref_length = 12
        short_ref = self.ref[:short_ref_length]

        git_repo_path = deps_dir.join_within_root(f"{self.repo_name}-{short_ref}")
        if git_repo_path.path.exists():
            log.info("Skipping existing git repository %s", self.url)
            return

        git_repo_path.path.mkdir(parents=True)

        log.info("Cloning git repository %s", self.url)
        repo = GitRepo.clone_from(
            url=str(self.url),
            to_path=git_repo_path.path,
            env={"GIT_TERMINAL_PROMPT": "0"},
        )

        if self.branch is not None:
            repo.git.checkout(self.branch)

        repo.git.reset("--hard", self.ref)


class PathDependency(_GemMetadata):
    """
    Represents a path dependency.

    Attributes:
        root:       The root of the package.
        subpath:    Subpath from the package root.
    """

    root: RootedPath
    subpath: str

    @pydantic.model_validator(mode="after")
    def validate_subpath(self) -> Self:
        """Validate that the subpath is within the package root."""
        try:
            self.root.join_within_root(self.subpath)
        except PathOutsideRoot as e:
            raise ValueError("PATH dependencies should be within the package root") from e

        return self

    @cached_property
    def purl(self) -> str:
        """Get PURL for this dependency."""
        try:
            qualifiers = get_vcs_qualifiers(self.root.path)
        except NotAGitRepo:
            if get_config().mode == Mode.STRICT:
                raise
            qualifiers = None

        purl = PackageURL(
            type="gem",
            name=self.name,
            version=self.version,
            qualifiers=qualifiers,
            subpath=self.subpath,
        )
        return purl.to_string()
