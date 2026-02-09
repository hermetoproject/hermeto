#!/usr/bin/env python3
"""Start and initialize a Nexus Repository Server for integration tests."""

import contextlib
import logging
import signal
import subprocess
import time
from types import FrameType
from typing import Any, NoReturn

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
    from .repositories import DEFAULT_REPOSITORIES, ProxyRepositoryConfig
else:
    from repositories import (  # type: ignore[import-not-found,no-redef]
        DEFAULT_REPOSITORIES,
        ProxyRepositoryConfig,
    )

log = logging.getLogger(__name__)

app = typer.Typer()

DEFAULT_SUBPROCESS_TIMEOUT = 300
DEFAULT_NEXUS_IMAGE = "docker.io/sonatype/nexus3:latest"
DEFAULT_NEXUS_HOST = "127.0.0.1"
DEFAULT_NEXUS_PORT = 8082
DEFAULT_NEXUS_ADMIN_PASSWORD = "admin123"  # noqa: S105
DEFAULT_NEXUS_CONTAINER_NAME = "hermeto-nexus"
DEFAULT_NEXUS_VOLUME_NAME = "hermeto-nexus-data"
DEFAULT_NEXUS_STARTUP_TIMEOUT = 300
DEFAULT_HTTP_TIMEOUT = 10

INITIAL_PASSWORD_TIMEOUT = 60
RETRY_WAIT_SECONDS = 5


class NexusContainer:
    """Manages the Nexus container lifecycle."""

    def __init__(
        self,
        image: str,
        container_name: str,
        volume_name: str,
        port: int,
        subprocess_timeout: int = DEFAULT_SUBPROCESS_TIMEOUT,
    ) -> None:
        """Initialize the Nexus container manager."""
        self.image = image
        self.container_name = container_name
        self.volume_name = volume_name
        self.port = port
        self.subprocess_timeout = subprocess_timeout

    def start(self) -> None:
        """Start the Nexus container."""
        log.info("Starting Nexus container '%s'...", self.container_name)

        subprocess.run(
            ["podman", "volume", "rm", "-f", self.volume_name],  # noqa: S607
            capture_output=True,
            timeout=self.subprocess_timeout,
        )

        subprocess.run(
            ["podman", "volume", "create", self.volume_name],  # noqa: S607
            check=True,
            capture_output=True,
            timeout=self.subprocess_timeout,
        )

        subprocess.run(
            [  # noqa: S607
                "podman",
                "run",
                "--rm",
                "--replace",
                "--name",
                self.container_name,
                "-d",
                "-p",
                f"{self.port}:8081",
                "-v",
                f"{self.volume_name}:/nexus-data",
                self.image,
            ],
            check=True,
            timeout=self.subprocess_timeout,
        )
        log.info("Nexus container started on port %d", self.port)

    def is_running(self) -> bool:
        """Check if the container is currently running."""
        result = subprocess.run(
            ["podman", "inspect", "--format", "{{.State.Running}}", self.container_name],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=self.subprocess_timeout,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def get_initial_password(self) -> str:
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
                    self.container_name,
                    "cat",
                    "/nexus-data/admin.password",
                ],
                capture_output=True,
                text=True,
                timeout=self.subprocess_timeout,
            )
            if result.returncode != 0 or not result.stdout.strip():
                raise FileNotFoundError("admin.password not yet available")
            return result.stdout.strip()

        return read_password()

    def __enter__(self) -> "NexusContainer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Remove the container and volume."""
        log.info("Cleaning up...")

        commands: list[tuple[list[str], str]] = [
            (["podman", "stop", "--time", "0", self.container_name], "stop container"),
            (["podman", "rm", "-f", self.container_name], "remove container"),
            (["podman", "volume", "rm", "-f", self.volume_name], "remove volume"),
        ]

        for cmd, description in commands:
            result = subprocess.run(  # noqa: S607
                cmd, capture_output=True, text=True, timeout=self.subprocess_timeout
            )
            if result.returncode != 0:
                log.warning(
                    "Failed to %s: %s",
                    description,
                    result.stderr.strip() or f"exit code {result.returncode}",
                )

        log.info("Cleanup complete")


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
            "enabled": True,
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


def initialize_nexus(
    image: str = DEFAULT_NEXUS_IMAGE,
    host: str = DEFAULT_NEXUS_HOST,
    port: int = DEFAULT_NEXUS_PORT,
    admin_password: str = DEFAULT_NEXUS_ADMIN_PASSWORD,
    container_name: str = DEFAULT_NEXUS_CONTAINER_NAME,
    volume_name: str = DEFAULT_NEXUS_VOLUME_NAME,
    startup_timeout: int = DEFAULT_NEXUS_STARTUP_TIMEOUT,
    subprocess_timeout: int = DEFAULT_SUBPROCESS_TIMEOUT,
    http_timeout: int = DEFAULT_HTTP_TIMEOUT,
    repositories: list[ProxyRepositoryConfig] | None = None,
) -> tuple[NexusContainer, NexusClient]:
    """Start a Nexus container, configure it, and create proxy repositories."""
    with contextlib.ExitStack() as stack:
        container = stack.enter_context(
            NexusContainer(
                image=image,
                container_name=container_name,
                volume_name=volume_name,
                port=port,
                subprocess_timeout=subprocess_timeout,
            )
        )
        container.start()

        if not container.is_running():
            raise RuntimeError(f"Container '{container.container_name}' failed to start")

        base_url = f"http://{host}:{port}"
        client = stack.enter_context(NexusClient(base_url, http_timeout=http_timeout))
        client.wait_for_ready(startup_timeout)

        log.info("Reading initial admin password...")
        initial_password = container.get_initial_password()
        client.password = initial_password
        log.info("Initial password retrieved")

        client.change_admin_password(admin_password)
        client.accept_eula()
        client.enable_anonymous_access()
        client.delete_all_repositories()

        repos = repositories if repositories is not None else DEFAULT_REPOSITORIES
        for repo_config in repos:
            client.create_proxy_repository(repo_config)

        # Success — transfer ownership to the caller
        stack.pop_all()
        return container, client


@app.command()
def main(
    image: str = typer.Option(
        DEFAULT_NEXUS_IMAGE,
        "--image",
        envvar="NEXUS_IMAGE",
        help=f"Container image (default: {DEFAULT_NEXUS_IMAGE})",
    ),
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
    volume_name: str = typer.Option(
        DEFAULT_NEXUS_VOLUME_NAME,
        "--volume-name",
        envvar="NEXUS_VOLUME_NAME",
        help=f"Volume name (default: {DEFAULT_NEXUS_VOLUME_NAME})",
    ),
    startup_timeout: int = typer.Option(
        DEFAULT_NEXUS_STARTUP_TIMEOUT,
        "--startup-timeout",
        envvar="NEXUS_STARTUP_TIMEOUT",
        help=f"Startup timeout in seconds (default: {DEFAULT_NEXUS_STARTUP_TIMEOUT})",
    ),
    subprocess_timeout: int = typer.Option(
        DEFAULT_SUBPROCESS_TIMEOUT,
        "--subprocess-timeout",
        envvar="NEXUS_SUBPROCESS_TIMEOUT",
        help=f"Timeout in seconds for individual commands (default: {DEFAULT_SUBPROCESS_TIMEOUT})",
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
) -> NoReturn:
    """Start and initialize a Nexus Repository Server."""
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        container, client = initialize_nexus(
            image=image,
            host=host,
            port=port,
            admin_password=admin_password,
            container_name=container_name,
            volume_name=volume_name,
            startup_timeout=startup_timeout,
            subprocess_timeout=subprocess_timeout,
            http_timeout=http_timeout,
        )
    except Exception:
        log.exception("Nexus initialization failed")
        raise typer.Exit(1)

    _shutdown = False

    def cleanup_on_exit(signum: int, frame: FrameType | None) -> None:
        nonlocal _shutdown
        _shutdown = True

    signal.signal(signal.SIGINT, cleanup_on_exit)
    signal.signal(signal.SIGTERM, cleanup_on_exit)

    print("\n" + "=" * 80)
    print("Nexus Repository Server is ready!")
    print(f"  URL: {client.base_url}")
    print(f"  Admin credentials: admin / {admin_password}")
    for repo in DEFAULT_REPOSITORIES:
        print(f"  {repo.format} proxy: {client.base_url}/repository/{repo.name}/")
    print("=" * 80)
    print("\nPress Ctrl+C to stop and cleanup...")

    with container, client:
        while not _shutdown:
            time.sleep(1)

    raise typer.Exit(0)


if __name__ == "__main__":
    app()
