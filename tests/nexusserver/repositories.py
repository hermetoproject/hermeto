# SPDX-License-Identifier: GPL-3.0-only
"""Repository configuration for Nexus server."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GroupRepositoryConfig:
    """Base configuration for a group repository."""

    name: str
    member_names: list[str]
    format: str
    strict_content_type_validation: bool = True

    def to_api_payload(self) -> dict[str, Any]:
        """Convert to a Nexus group repository creation payload."""
        return {
            "name": self.name,
            "online": True,
            "storage": {
                "blobStoreName": "default",
                "strictContentTypeValidation": (self.strict_content_type_validation),
            },
            "group": {
                "memberNames": self.member_names,
            },
        }


@dataclass
class ProxyRepositoryConfig:
    """Base configuration for a proxy repository."""

    name: str
    remote_url: str
    format: str
    strict_content_type_validation: bool = True

    def to_api_payload(self) -> dict[str, Any]:
        """Convert to Nexus REST API payload for creating a proxy repository.

        See: https://sonatype.github.io/sonatype-documentation/api/nexus-repository/latest/nexus-repository-api.json
        """
        return {
            "name": self.name,
            "online": True,
            "storage": {
                "blobStoreName": "default",
                "strictContentTypeValidation": self.strict_content_type_validation,
            },
            "proxy": {
                "remoteUrl": self.remote_url,
                "contentMaxAge": 1440,
                "metadataMaxAge": 1440,
            },
            "negativeCache": {
                "enabled": True,
                "timeToLive": 1440,
            },
            "httpClient": {
                "blocked": False,
                "autoBlock": True,
            },
        }


@dataclass
class HostedRepositoryConfig:
    """Base configuration for a hosted repository."""

    name: str
    format: str
    strict_content_type_validation: bool = True

    def to_api_payload(self) -> dict[str, Any]:
        """Convert to a Nexus hosted repository creation payload."""
        return {
            "name": self.name,
            "online": True,
            "storage": {
                "blobStoreName": "default",
                "strictContentTypeValidation": (self.strict_content_type_validation),
                "writePolicy": "ALLOW",
            },
        }


@dataclass
class PypiGroupConfig(GroupRepositoryConfig):
    """Configuration for a PyPI group repository."""

    format: str = field(default="pypi", init=False)


@dataclass
class PypiHostedConfig(HostedRepositoryConfig):
    """Configuration for a PyPI hosted repository."""

    format: str = field(default="pypi", init=False)


@dataclass
class BundlerProxyConfig(ProxyRepositoryConfig):
    """Configuration for a bundler (rubygems) proxy repository."""

    format: str = field(default="rubygems", init=False)


@dataclass
class NpmProxyConfig(ProxyRepositoryConfig):
    """Configuration for an npm proxy repository."""

    format: str = field(default="npm", init=False)


@dataclass
class PypiProxyConfig(ProxyRepositoryConfig):
    """Configuration for a PyPI proxy repository."""

    format: str = field(default="pypi", init=False)


@dataclass
class YumProxyConfig(ProxyRepositoryConfig):
    """Configuration for a Yum proxy repository."""

    format: str = field(default="yum", init=False)


@dataclass
class GomodProxyConfig(ProxyRepositoryConfig):
    """Configuration for a Go proxy repository."""

    format: str = field(default="go", init=False)


@dataclass
class CargoProxyConfig(ProxyRepositoryConfig):
    """Configuration for a Cargo proxy repository."""

    format: str = field(default="cargo", init=False)

    def to_api_payload(self) -> dict[str, Any]:
        result = super().to_api_payload()
        result |= {"cargo": {"requireAuthentication": True}}
        return result


DEFAULT_PROXY_REPOSITORIES: list[ProxyRepositoryConfig] = [
    BundlerProxyConfig(name="rubygems-proxy", remote_url="https://rubygems.org"),
    NpmProxyConfig(name="npm-proxy", remote_url="https://registry.npmjs.org"),
    PypiProxyConfig(name="pypi-proxy", remote_url="https://pypi.org"),
    GomodProxyConfig(name="go-proxy", remote_url="https://proxy.golang.org"),
    CargoProxyConfig(name="cargo-proxy", remote_url="https://index.crates.io"),
    YumProxyConfig(
        name="yum-proxy",
        remote_url="https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi9/9",
    ),
]
DEFAULT_HOSTED_REPOSITORIES: list[HostedRepositoryConfig] = [PypiHostedConfig(name="pypi-hosted")]

DEFAULT_GROUP_REPOSITORIES: list[GroupRepositoryConfig] = [
    PypiGroupConfig(
        name="pypi-group",
        member_names=["pypi-hosted", "pypi-proxy"],
    ),
]
