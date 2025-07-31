import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Union

log = logging.getLogger(__name__)


class ContainerEngine(ABC):
    """Abstract base class for container engines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Get the name of the container engine."""

    def run_cmd(self, cmd: Union[list[str], str], **subprocess_kwargs: Any) -> tuple[str, int]:
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


class PodmanEngine(ContainerEngine):
    """Podman engine."""

    @property
    def name(self) -> str:
        """Get the name of the container engine."""
        return "podman"
