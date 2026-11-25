#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Configure a Nexus Repository Server for integration tests."""

import logging
import subprocess
from typing import Any

import requests
import typer
from requests.auth import HTTPBasicAuth
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_delay,
    wait_fixed,
)

if __package__:
    from .repositories import (
        DEFAULT_GROUP_REPOSITORIES,
        DEFAULT_HOSTED_REPOSITORIES,
        DEFAULT_PROXY_REPOSITORIES,
        GroupRepositoryConfig,
        HostedRepositoryConfig,
        ProxyRepositoryConfig,
    )
else:
    from repositories import (  # type: ignore[import-not-found,no-redef]
        DEFAULT_GROUP_REPOSITORIES,
        DEFAULT_HOSTED_REPOSITORIES,
        DEFAULT_PROXY_REPOSITORIES,
        GroupRepositoryConfig,
        HostedRepositoryConfig,
        ProxyRepositoryConfig,
    )

log = logging.getLogger(__name__)

app = typer.Typer()

DEFAULT_SUBPROCESS_TIMEOUT = 300
DEFAULT_NEXUS_HOST = "127.0.0.1"
DEFAULT_NEXUS_PORT = 8082
DEFAULT_NEXUS_TLS_PORT = 8443
DEFAULT_NEXUS_MTLS_PORT = 8444
DEFAULT_NEXUS_ADMIN_PASSWORD = "admin123"  # noqa: S105
DEFAULT_NEXUS_CONTAINER_NAME = "hermeto-nexus"
DEFAULT_NEXUS_STARTUP_TIMEOUT = 300
DEFAULT_HTTP_TIMEOUT = 10

INITIAL_PASSWORD_TIMEOUT = 60
RETRY_WAIT_SECONDS = 5


class NexusClient:
    """HTTP client for interacting with Nexus REST API."""

    def __init__(
        self,
        base_url: str,
        username: str = "admin",
        password: str = "",
        http_timeout: int = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        """Initialize the Nexus REST API client."""
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.http_timeout = http_timeout
        self.session = requests.Session()

    def __enter__(self) -> "NexusClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    @property
    def auth(self) -> HTTPBasicAuth:
        """Return HTTP basic auth credentials."""
        return HTTPBasicAuth(self.username, self.password)

    def wait_for_ready(self, timeout: int) -> None:
        """Poll the status endpoint until Nexus is ready or timeout is reached."""
        log.info("Waiting for Nexus to be ready (timeout: %ds)...", timeout)
        status_url = f"{self.base_url}/service/rest/v1/status/writable"

        @retry(
            stop=stop_after_delay(timeout),
            wait=wait_fixed(RETRY_WAIT_SECONDS),
            retry=retry_if_exception_type((requests.exceptions.RequestException, RuntimeError)),
            before_sleep=lambda retry_state: log.debug(
                "Waiting... (%ds elapsed)", int(retry_state.seconds_since_start or 0)
            ),
            reraise=True,
        )
        def poll_status() -> None:
            response = self.session.get(status_url, timeout=self.http_timeout)
            if response.status_code != 200:
                raise RuntimeError(f"Status endpoint returned {response.status_code}")

        try:
            poll_status()
            log.info("Nexus is ready")
        except (requests.exceptions.RequestException, RuntimeError, RetryError) as e:
            raise RuntimeError(f"Nexus failed to become ready within {timeout}s") from e

    def change_admin_password(self, new_password: str) -> None:
        """Change the admin user password."""
        log.info("Changing admin password...")
        url = f"{self.base_url}/service/rest/v1/security/users/admin/change-password"
        response = self.session.put(
            url,
            auth=self.auth,
            headers={"Content-Type": "text/plain"},
            data=new_password,
            timeout=self.http_timeout,
        )
        response.raise_for_status()
        self.password = new_password
        log.info("Admin password changed successfully")

    def accept_eula(self) -> None:
        """Accept the End User License Agreement.

        The EULA API requires fetching the disclaimer text first, then posting
        it back with accepted=true.
        """
        log.info("Accepting EULA...")
        url = f"{self.base_url}/service/rest/v1/system/eula"

        get_response = self.session.get(url, auth=self.auth, timeout=self.http_timeout)
        get_response.raise_for_status()

        eula_data = get_response.json()
        if eula_data.get("accepted"):
            log.info("EULA already accepted")
            return

        eula_data["accepted"] = True
        post_response = self.session.post(
            url,
            auth=self.auth,
            json=eula_data,
            timeout=self.http_timeout,
        )
        post_response.raise_for_status()
        log.info("EULA accepted")

    def enable_anonymous_access(self) -> None:
        """Enable anonymous access to repositories."""
        log.info("Enabling anonymous access...")
        url = f"{self.base_url}/service/rest/v1/security/anonymous"
        payload = {
            "enabled": "true",
            "userId": "anonymous",
            "realmName": "NexusAuthorizingRealm",
        }
        response = self.session.put(url, auth=self.auth, json=payload, timeout=self.http_timeout)
        response.raise_for_status()
        log.info("Anonymous access enabled")

    def list_repositories(self) -> list[dict[str, Any]]:
        """List all repositories."""
        url = f"{self.base_url}/service/rest/v1/repositories"
        response = self.session.get(url, auth=self.auth, timeout=self.http_timeout)
        response.raise_for_status()
        return response.json()

    def delete_repository(self, name: str) -> None:
        """Delete a repository by name."""
        log.info("Deleting repository '%s'...", name)
        url = f"{self.base_url}/service/rest/v1/repositories/{name}"
        response = self.session.delete(url, auth=self.auth, timeout=self.http_timeout)
        response.raise_for_status()
        log.info("Repository '%s' deleted", name)

    def delete_all_repositories(self) -> None:
        """Delete all existing repositories."""
        log.info("Deleting all existing repositories...")
        repos = self.list_repositories()
        for repo in repos:
            self.delete_repository(repo["name"])
        log.info("Deleted %d repositories", len(repos))

    def create_proxy_repository(self, config: ProxyRepositoryConfig) -> None:
        """Create a proxy repository."""
        log.info("Creating %s proxy repository '%s'...", config.format, config.name)
        url = f"{self.base_url}/service/rest/v1/repositories/{config.format}/proxy"
        response = self.session.post(
            url,
            auth=self.auth,
            json=config.to_api_payload(),
            timeout=self.http_timeout,
        )
        response.raise_for_status()
        log.info("%s proxy repository '%s' created", config.format, config.name)

    def create_hosted_repository(self, config: HostedRepositoryConfig) -> None:
        """Create a hosted repository."""
        log.info("Creating %s hosted repository '%s'...", config.format, config.name)
        url = f"{self.base_url}/service/rest/v1/repositories/{config.format}/hosted"
        response = self.session.post(
            url,
            auth=self.auth,
            json=config.to_api_payload(),
            timeout=self.http_timeout,
        )
        response.raise_for_status()
        log.info("%s hosted repository '%s' created", config.format, config.name)

    def create_group_repository(self, config: GroupRepositoryConfig) -> None:
        """Create a group repository."""
        log.info("Creating %s group repository '%s'...", config.format, config.name)
        url = f"{self.base_url}/service/rest/v1/repositories/{config.format}/group"
        response = self.session.post(
            url,
            auth=self.auth,
            json=config.to_api_payload(),
            timeout=self.http_timeout,
        )
        response.raise_for_status()
        log.info(
            "%s group repository '%s' created",
            config.format,
            config.name,
        )


def get_initial_password(
    container_name: str = DEFAULT_NEXUS_CONTAINER_NAME,
    subprocess_timeout: int = DEFAULT_SUBPROCESS_TIMEOUT,
) -> str:
    """Read the initial admin password from the container, retrying until available."""

    @retry(
        stop=stop_after_delay(INITIAL_PASSWORD_TIMEOUT),
        wait=wait_fixed(RETRY_WAIT_SECONDS),
        retry=retry_if_exception_type(FileNotFoundError),
        reraise=True,
    )
    def read_password() -> str:
        result = subprocess.run(
            [  # noqa: S607
                "podman",
                "exec",
                container_name,
                "cat",
                "/nexus-data/admin.password",
            ],
            capture_output=True,
            text=True,
            timeout=subprocess_timeout,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise FileNotFoundError("admin.password not yet available")
        return result.stdout.strip()

    return read_password()


def initialize_nexus(
    host: str = DEFAULT_NEXUS_HOST,
    port: int = DEFAULT_NEXUS_PORT,
    admin_password: str = DEFAULT_NEXUS_ADMIN_PASSWORD,
    container_name: str = DEFAULT_NEXUS_CONTAINER_NAME,
    startup_timeout: int = DEFAULT_NEXUS_STARTUP_TIMEOUT,
    subprocess_timeout: int = DEFAULT_SUBPROCESS_TIMEOUT,
    http_timeout: int = DEFAULT_HTTP_TIMEOUT,
    proxy_repos: list[ProxyRepositoryConfig] | None = None,
    hosted_repos: list[HostedRepositoryConfig] | None = None,
    group_repos: list[GroupRepositoryConfig] | None = None,
) -> NexusClient:
    """Configure a running Nexus instance and create proxy repositories."""
    base_url = f"http://{host}:{port}"
    client = NexusClient(base_url, http_timeout=http_timeout)

    client.wait_for_ready(startup_timeout)

    log.info("Reading initial admin password...")
    initial_password = get_initial_password(container_name, subprocess_timeout)
    client.password = initial_password
    log.info("Initial password retrieved")

    client.change_admin_password(admin_password)
    client.accept_eula()
    client.enable_anonymous_access()
    client.delete_all_repositories()

    proxies = DEFAULT_PROXY_REPOSITORIES if proxy_repos is None else proxy_repos
    hosted = DEFAULT_HOSTED_REPOSITORIES if hosted_repos is None else hosted_repos
    groups = DEFAULT_GROUP_REPOSITORIES if group_repos is None else group_repos

    for prox_repo_config in proxies:
        client.create_proxy_repository(prox_repo_config)

    for host_repo_config in hosted:
        client.create_hosted_repository(host_repo_config)

    for group_repo_config in groups:
        client.create_group_repository(group_repo_config)

    return client


@app.command()
def main(
    host: str = typer.Option(
        DEFAULT_NEXUS_HOST,
        "--host",
        envvar="NEXUS_HOST",
        help=f"Host address (default: {DEFAULT_NEXUS_HOST})",
    ),
    port: int = typer.Option(
        DEFAULT_NEXUS_PORT,
        "--port",
        envvar="NEXUS_PORT",
        help=f"Host port (default: {DEFAULT_NEXUS_PORT})",
    ),
    admin_password: str = typer.Option(
        DEFAULT_NEXUS_ADMIN_PASSWORD,
        "--admin-password",
        envvar="NEXUS_ADMIN_PASSWORD",
        help=f"New admin password (default: {DEFAULT_NEXUS_ADMIN_PASSWORD})",
    ),
    container_name: str = typer.Option(
        DEFAULT_NEXUS_CONTAINER_NAME,
        "--container-name",
        envvar="NEXUS_CONTAINER_NAME",
        help=f"Container name (default: {DEFAULT_NEXUS_CONTAINER_NAME})",
    ),
    startup_timeout: int = typer.Option(
        DEFAULT_NEXUS_STARTUP_TIMEOUT,
        "--startup-timeout",
        envvar="NEXUS_STARTUP_TIMEOUT",
        help=f"Startup timeout in seconds (default: {DEFAULT_NEXUS_STARTUP_TIMEOUT})",
    ),
    http_timeout: int = typer.Option(
        DEFAULT_HTTP_TIMEOUT,
        "--http-timeout",
        envvar="NEXUS_HTTP_TIMEOUT",
        help=f"Timeout in seconds for HTTP requests (default: {DEFAULT_HTTP_TIMEOUT})",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        envvar="NEXUS_LOG_LEVEL",
        help="Log level (default: INFO)",
    ),
) -> None:
    """Initialize a Nexus Repository Server started via podman-compose."""
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        client = initialize_nexus(
            host=host,
            port=port,
            admin_password=admin_password,
            container_name=container_name,
            startup_timeout=startup_timeout,
            http_timeout=http_timeout,
        )
    except Exception:
        log.exception("Nexus initialization failed")
        raise typer.Exit(1)

    print("\n" + "=" * 80)
    print("Nexus Repository Server is ready!")
    print(f"  URL: {client.base_url}")
    print(f"  Admin credentials: admin / {admin_password}")
    for proxy_repo in DEFAULT_PROXY_REPOSITORIES:
        print(f"  {proxy_repo.format} proxy: {client.base_url}/repository/{proxy_repo.name}/")

    for hosted_repo in DEFAULT_HOSTED_REPOSITORIES:
        print(f"  {hosted_repo.format} hosted: {client.base_url}/repository/{hosted_repo.name}/")

    for group_repo in DEFAULT_GROUP_REPOSITORIES:
        print(f"  {group_repo.format} group: {client.base_url}/repository/{group_repo.name}/")
    print("=" * 80)

    client.close()


if __name__ == "__main__":
    app()
