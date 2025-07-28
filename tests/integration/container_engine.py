import logging
import os
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

    def build(self, build_cmd: list[str], tag: str) -> tuple[str, int]:
        """Build container image."""
        return self._run_cmd([self.name, *build_cmd, "--tag", tag])

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
        flags: Optional[list[str]] = None,
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
        flags: Optional[list[str]] = None,
    ) -> tuple[str, int]:
        """Run command on the image."""
        if flags is None:
            flags = []

        image_cmd = [self.name, "run", "--rm", *flags, image] + cmd
        return self._run_cmd(image_cmd)


def get_container_engine() -> ContainerEngine:
    """Get the configured container engine."""
    engine_name = os.getenv("HERMETO_TEST_CONTAINER_ENGINE", "podman").lower()

    if engine_name == "podman":
        return PodmanEngine()

    raise RuntimeError(f"Invalid container engine: {engine_name}")
