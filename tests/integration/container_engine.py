import json
import logging
import os
import secrets
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Optional, Union

log = logging.getLogger(__name__)

StrPath = Union[str, os.PathLike[str]]


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
        forced_options = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "encoding": "utf-8",
            "text": True,
        }

        subprocess_kwargs.update(forced_options)
        process = subprocess.run(cmd, **subprocess_kwargs)

        return process.stdout, process.returncode

    def build(
        self, context_dir: StrPath = ".", flags: Optional[list[str]] = None
    ) -> tuple[str, int]:
        """Build container image."""
        if flags is None:
            flags = []

        return self._run_cmd([self.name, "build", *flags, str(context_dir)])

    def pull(self, image: str) -> tuple[str, int]:
        """Pull container image."""
        return self._run_cmd([self.name, "pull", image])

    def rmi(self, image: str) -> tuple[str, int]:
        """Remove container image."""
        return self._run_cmd([self.name, "rmi", "--force", image])

    @abstractmethod
    def run(
        self,
        image: str,
        cmd: list[str],
        podman_flags: Optional[list[str]] = None,
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
        podman_flags: Optional[list[str]] = None,
    ) -> tuple[str, int]:
        """Run command on the image."""
        if podman_flags is None:
            podman_flags = []

        image_cmd = [self.name, "run", "--rm", *podman_flags, image] + cmd
        return self._run_cmd(image_cmd)


class BuildahEngine(ContainerEngine):
    """Buildah engine."""

    @property
    def name(self) -> str:
        """Get the name of the container engine."""
        return "buildah"

    def _configure_buildah_container(self, image: str) -> str:
        """Configure buildah container.

        Buildah requires a container to be explicitly created from an image before it can be used.
        The built container then can be referenced by the name given when creating it.

        :param image: The image to create a container from.
        :return: The container name.
        """
        container_name = f"hermeto-test-container-{secrets.token_hex(6)}"
        from_cmd = ["buildah", "from", "--name", container_name, image]
        _, exit_code = self._run_cmd(from_cmd)

        if exit_code != 0:
            raise RuntimeError(f"Failed to create buildah container from image {image}.")

        return container_name

    def _generate_cmd(
        self, container_name: str, image: str, cmd: list[str], flags: Optional[list[str]]
    ) -> list[str]:
        """Generate container run command.

        Buildah does not automatically use the image's configured CMD or ENTRYPOINT when running a command.
        This means that the run command needs to be generated manually considering the image's configuration
        and the flags present in the original run command.

        :param container_name: The container name to run the command on.
        :param image: The image on which the command will be run.
        :param cmd: The original command provided.
        :param flags: The flags present in the run command.
        :return: The generated command.
        """
        filtered_flags, entrypoint = self._filter_entrypoint_flags(flags)
        image_cmd, image_entrypoint = self._get_image_config(image)

        # fallback to the image's default cmd if a custom one is not provided
        cmd = cmd or image_cmd

        # if an entrypoint flag is provided, use it
        if entrypoint:
            return ["run", *filtered_flags, container_name, "--", entrypoint, *cmd]

        # if the image has an entrypoint, prepend it to the command
        if image_entrypoint:
            return ["run", *filtered_flags, container_name, "--", *image_entrypoint, *cmd]

        return ["run", *filtered_flags, container_name, "--", *cmd]

    def _filter_entrypoint_flags(
        self, flags: Optional[list[str]]
    ) -> tuple[list[str], Optional[str]]:
        """Remove and return any entrypoint flags.

        Since buildah does not support the --entrypoint flag, we need to extract it so that
        the run command can be properly formatted later on.

        :param flags: The flags to treat.
        :return: The filtered flags and the entrypoint value.
        """
        if flags is None:
            return [], None

        entrypoint_index = next(
            (i for i, flag in enumerate(flags) if flag.startswith("--entrypoint")), None
        )

        if entrypoint_index is None:
            return flags, None

        entrypoint_flag = flags[entrypoint_index]

        # entrypoint flag is in the format --entrypoint=<entrypoint>
        if "=" in entrypoint_flag:
            entrypoint_value = entrypoint_flag.split("=")[1]
            return flags[:entrypoint_index] + flags[entrypoint_index + 1 :], entrypoint_value
        # entrypoint flag is in the format --entrypoint <entrypoint>
        elif entrypoint_index + 1 < len(flags):
            entrypoint_value = flags[entrypoint_index + 1]
            return flags[:entrypoint_index] + flags[entrypoint_index + 2 :], entrypoint_value
        # entrypoint flag exists but no value is provided
        else:
            raise RuntimeError(
                f"Unable to parse the run command flags: {flags}."
                "Check if the --entrypoint flag defines a value."
            )

    def _get_image_config(self, image: str) -> tuple[list[str], list[str]]:
        """Get the configured entrypoint and cmd of an image."""
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
        flags: Optional[list[str]] = None,
    ) -> tuple[str, int]:
        """Run command using buildah."""
        container_name = self._configure_buildah_container(image)

        try:
            generated_cmd = self._generate_cmd(container_name, image, cmd, flags)

            return self._run_cmd([self.name, *generated_cmd])
        finally:
            self._run_cmd([self.name, "rm", container_name])


def get_container_engine() -> ContainerEngine:
    """Get the configured container engine."""
    engine_name = os.getenv("HERMETO_TEST_CONTAINER_ENGINE", "podman").lower()

    if engine_name == "podman":
        return PodmanEngine()

    if engine_name == "buildah":
        return BuildahEngine()

    raise RuntimeError(f"Invalid container engine: {engine_name}")
