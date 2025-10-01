import json
import logging
import os
import secrets
import subprocess
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Generator, Optional, Union

from hermeto.core.type_aliases import StrPath

log = logging.getLogger(__name__)


class ContainerEngine(ABC):
    """Abstract base class for container engines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Get the name of the container engine."""

    def _run_cmd(self, cmd: Union[list[str], str], **subprocess_kwargs: Any) -> tuple[str, int]:
        """
        Run command via subprocess.

        :param cmd: command to be executed
        :param subprocess_kwargs: passthrough kwargs to subprocess.run
        :return: Command output and exitcode
        :rtype: Tuple
        """
        log.info("Run command: %s.", cmd)

        # redirect stderr to stdout for easier evaluation/handling of a single stream
        defaults = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "encoding": "utf-8",
            "text": True,
        }

        opts: dict[str, Any] = defaults | subprocess_kwargs
        process = subprocess.run(cmd, **opts)

        return process.stdout, process.returncode

    def build(
        self, context_dir: StrPath = ".", flags: Optional[list[str]] = None
    ) -> tuple[str, int]:
        """Build container image."""
        if flags is None:
            flags = []

        return self._run_cmd([self.name, "build", *flags, str(context_dir)])

    def pull(self, image: str, flags: Optional[list[str]] = None) -> tuple[str, int]:
        """Pull container image."""
        if flags is None:
            flags = []

        return self._run_cmd([self.name, "pull", *flags, image])

    def rmi(self, image: str, flags: Optional[list[str]] = None) -> tuple[str, int]:
        """Remove container image."""
        if flags is None:
            flags = []

        return self._run_cmd([self.name, "rmi", "--force", *flags, image])

    @abstractmethod
    def run(
        self,
        image: str,
        cmd: list[str],
        entrypoint: Optional[str] = None,
        flags: Optional[list[str]] = None,
        subprocess_kwargs: Optional[dict[str, Any]] = None,
    ) -> tuple[str, int]:
        """Run command on the image."""


class PodmanEngine(ContainerEngine):
    """Podman engine."""

    @property
    def name(self) -> str:
        """Get the name of the container engine."""
        return "podman"

    def run(
        self,
        image: str,
        cmd: list[str],
        entrypoint: Optional[str] = None,
        flags: Optional[list[str]] = None,
        subprocess_kwargs: Optional[dict[str, Any]] = None,
    ) -> tuple[str, int]:
        """Run command on the image."""
        if flags is None:
            flags = []

        if subprocess_kwargs is None:
            subprocess_kwargs = {}

        if entrypoint:
            flags.append(f"--entrypoint={entrypoint}")

        image_cmd = [self.name, "run", "--rm", *flags, image] + cmd
        return self._run_cmd(image_cmd, **subprocess_kwargs)


class BuildahEngine(ContainerEngine):
    """Buildah engine."""

    @property
    def name(self) -> str:
        """Get the name of the container engine."""
        return "buildah"

    @contextmanager
    def _configure_buildah_container(self, image: str) -> Generator[str, None, None]:
        """Configure buildah container.

        Buildah requires a container to be explicitly created from an image before it can be used.
        The built container then can be referenced by the name given when creating it.

        :param image: The image to create a container from.
        :return: The container name.
        """
        container_name = f"hermeto-test-container-{secrets.token_hex(6)}"
        from_cmd = ["buildah", "from", "--name", container_name, image]
        stdout, exit_code = self._run_cmd(from_cmd)

        if exit_code != 0:
            raise RuntimeError(f"Failed to create buildah container from image {image}\n{stdout}")

        try:
            yield container_name
        finally:
            self._run_cmd([self.name, "rm", container_name])

    def _generate_cmd(
        self,
        container_name: str,
        image: str,
        cmd: list[str],
        entrypoint: Optional[str] = None,
        flags: Optional[list[str]] = None,
    ) -> list[str]:
        """Generate container run command.

        Buildah does not automatically use the image's configured CMD or ENTRYPOINT when running a command.
        This means that the run command needs to be generated manually considering the image's configuration
        and the flags present in the original run command.

        :param container_name: The container name to run the command on.
        :param image: The image on which the command will be run.
        :param cmd: The original command provided.
        :param entrypoint: The entrypoint to be used for the image.
        :param flags: The flags present in the run command.
        :return: The generated command.
        """
        if flags is None:
            flags = []

        image_cmd, image_entrypoint = self._get_image_config(image)

        # fallback to the image's default cmd if a custom one is not provided
        cmd = cmd or image_cmd

        # if an entrypoint flag is provided, use it
        if entrypoint:
            return ["run", *flags, container_name, "--", entrypoint, *cmd]

        # if the image has an entrypoint, prepend it to the command
        if image_entrypoint:
            return ["run", *flags, container_name, "--", *image_entrypoint, *cmd]

        # if no entrypoint is provided, use only the cmd
        return ["run", *flags, container_name, "--", *cmd]

    def _get_image_config(self, image: str) -> tuple[list[str], list[str]]:
        """Parse entrypoint and cmd from image's JSON configuration."""
        output, exit_code = self._run_cmd(["buildah", "inspect", image])

        if exit_code != 0:
            raise RuntimeError(f"Failed to inspect image {image}.")

        parsed_output = json.loads(output)
        docker_config = parsed_output.get("Docker", {}).get("config", {})
        cmd = docker_config.get("Cmd", [])
        entrypoint = docker_config.get("Entrypoint", [])

        return cmd, entrypoint

    def run(
        self,
        image: str,
        cmd: list[str],
        entrypoint: Optional[str] = None,
        flags: Optional[list[str]] = None,
        subprocess_kwargs: Optional[dict[str, Any]] = None,
    ) -> tuple[str, int]:
        """Run command using buildah."""
        with self._configure_buildah_container(image) as container_name:
            generated_cmd = self._generate_cmd(container_name, image, cmd, entrypoint, flags)

            return self._run_cmd([self.name, *generated_cmd])


def get_container_engine() -> ContainerEngine:
    """Get the configured container engine."""
    engine_name = os.getenv("HERMETO_TEST_CONTAINER_ENGINE", "podman").lower()

    if engine_name == "podman":
        return PodmanEngine()

    if engine_name == "buildah":
        return BuildahEngine()

    raise RuntimeError(f"Invalid container engine: {engine_name}")
