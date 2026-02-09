"""Repository configuration for Nexus server."""

from dataclasses import dataclass, field
from typing import Any


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
class NpmProxyConfig(ProxyRepositoryConfig):
    """Configuration for an npm proxy repository."""

    format: str = field(default="npm", init=False)


DEFAULT_REPOSITORIES: list[ProxyRepositoryConfig] = [
    NpmProxyConfig(name="npm-proxy", remote_url="https://registry.npmjs.org"),
]
