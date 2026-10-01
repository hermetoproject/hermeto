# SPDX-License-Identifier: GPL-3.0-only
"""Models for ansible.lock.yaml and ansible.cfg auth helpers."""

from __future__ import annotations

import configparser
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from packageurl import PackageURL
from pydantic import BaseModel, ConfigDict, Field, field_validator

from hermeto.core.checksum import ChecksumInfo
from hermeto.core.errors import PackageManagerError, PackageRejected
from hermeto.core.models.sbom import Component, ExternalReference

log = logging.getLogger(__name__)

DEFAULT_CLIENT_ID = "cloud-services"


class AnsibleCollectionLockEntry(BaseModel):
    """A single collection in ansible.lock.yaml."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    url: str
    checksum: str
    size: int | None = None
    server: str | None = None

    @field_validator("checksum")
    @classmethod
    def _checksum_format(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError(f"Checksum must be in the format 'algorithm:hash' (got {value!r})")
        return value

    @field_validator("url")
    @classmethod
    def _url_must_be_remote(cls, value: str) -> str:
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError(
                f"Collection url must be an http(s) remote URL (got {value!r}). "
                "Regenerate ansible.lock.yaml with ansible-lockfile-prototype --prefer-remote "
                "so Hermeto can fetch and verify artifacts."
            )
        return value

    @property
    def formatted_checksum(self) -> ChecksumInfo:
        """Return checksum as ChecksumInfo."""
        algorithm, digest = self.checksum.split(":", 1)
        return ChecksumInfo(algorithm, digest)

    @property
    def artifact_filename(self) -> str:
        """Filename under deps/ansible/ (prefer URL basename)."""
        path = urlparse(self.url).path
        name = Path(path).name
        if name:
            return name
        namespace, _, coll = self.name.partition(".")
        return f"{namespace}-{coll}-{self.version}.tar.gz"

    def get_sbom_component(self) -> Component:
        """Build an SBOM component for this collection."""
        namespace, _, coll = self.name.partition(".")
        if not namespace or not coll:
            raise PackageRejected(
                f"Invalid collection name {self.name!r}; expected namespace.name",
                solution="Ensure ansible.lock.yaml uses fully-qualified collection names.",
            )
        purl = PackageURL(
            type="ansible",
            namespace=namespace,
            name=coll,
            version=self.version,
            qualifiers={
                "checksum": self.checksum,
                "download_url": self.url,
            },
        ).to_string()
        return Component(
            name=self.name,
            version=self.version,
            purl=purl,
            type="library",
            external_references=[ExternalReference(url=self.url, type="distribution")],
        )


class AnsibleLockfile(BaseModel):
    """ansible.lock.yaml root document."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    lockfile_version: int = Field(alias="lockfileVersion")
    lockfile_vendor: str = Field(alias="lockfileVendor")
    collections: list[AnsibleCollectionLockEntry]
    auth: dict | None = None


@dataclass(frozen=True)
class GalaxyServerAuth:
    """Credentials for one galaxy_server.* section."""

    name: str
    url: str
    auth_url: str | None = None
    token: str | None = None
    token_env: str | None = None
    client_id: str = DEFAULT_CLIENT_ID

    def resolve_secret(self) -> str:
        """Return the configured token / env value."""
        if self.token_env:
            value = os.environ.get(self.token_env)
            if not value:
                raise PackageManagerError(
                    f"Environment variable {self.token_env!r} is not set or empty "
                    f"(required for galaxy server {self.name!r})"
                )
            return value
        if self.token:
            return self.token
        raise PackageManagerError(f"Galaxy server {self.name!r} has no token or token_env")

    def authorization_header(self) -> dict[str, str] | None:
        """Return Authorization headers for artifact downloads, or None if no auth."""
        if not (self.token or self.token_env or self.auth_url):
            return None
        if not (self.token or self.token_env):
            return None
        secret = self.resolve_secret()
        if self.auth_url:
            access = _exchange_refresh_token(self.auth_url, secret, self.client_id)
            return {"Authorization": f"Bearer {access}"}
        return {"Authorization": f"Token {secret}"}


def _exchange_refresh_token(auth_url: str, refresh_token: str, client_id: str) -> str:
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    log.debug("POST %s (SSO token exchange)", auth_url)
    try:
        resp = requests.post(auth_url, data=data, timeout=60)
    except requests.RequestException as exc:
        raise PackageManagerError(f"SSO token exchange failed for {auth_url}: {exc}") from exc
    if not resp.ok:
        raise PackageManagerError(
            f"SSO token exchange HTTP {resp.status_code} for {auth_url}: {resp.text[:200]}"
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise PackageManagerError("SSO token exchange returned non-JSON body") from exc
    access = payload.get("access_token")
    if not access:
        raise PackageManagerError("SSO token exchange response missing access_token")
    return access


def find_ansible_cfg(package_dir: Path, source_root: Path) -> Path | None:
    """Locate ansible.cfg under the package path or source root."""
    for candidate in (package_dir / "ansible.cfg", source_root / "ansible.cfg"):
        if candidate.is_file():
            return candidate.resolve()
    return None


def parse_galaxy_servers(cfg_path: Path) -> dict[str, GalaxyServerAuth]:
    """Parse galaxy_server.* sections from ansible.cfg into auth objects."""
    parser = configparser.ConfigParser()
    read = parser.read(cfg_path)
    if not read:
        raise PackageManagerError(f"Unable to read ansible.cfg: {cfg_path}")

    servers: dict[str, GalaxyServerAuth] = {}
    for section in parser.sections():
        if not section.startswith("galaxy_server."):
            continue
        name = section.removeprefix("galaxy_server.")
        url = parser.get(section, "url", fallback="").strip()
        if not url:
            continue
        servers[name] = GalaxyServerAuth(
            name=name,
            url=url,
            auth_url=parser.get(section, "auth_url", fallback="").strip() or None,
            token=parser.get(section, "token", fallback="").strip() or None,
            token_env=parser.get(section, "token_env", fallback="").strip() or None,
            client_id=(
                parser.get(section, "client_id", fallback="").strip() or DEFAULT_CLIENT_ID
            ),
        )
    return servers
