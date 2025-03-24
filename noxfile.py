"""
Flexible test automation with Python for this project.

To run all sessions, run the following command:
$ nox

To run a specific session, run the following command:
$ nox -s <session-name>

To run a session with additional arguments, run the following command:
$ nox -s <session-name> -- <additional-arguments>

To list all available sessions, run the following command:
$ nox -l
"""

import os
from pathlib import Path
from typing import Any

import nox
import tomlkit
from nox.sessions import Session

# default sessions to run (sorted alphabetically)
nox.options.sessions = ["bandit", "black", "flake8", "isort", "mypy", "python"]

# reuse virtual environment for all sessions
nox.options.reuse_venv = "always"

# use venv as the default virtual environment backend
nox.options.default_venv_backend = "venv"


def install_requirements(session: Session) -> None:
    """Install requirements for all sessions."""
    session.install("--no-deps", "-r", "requirements-extras.txt")


def parse_supported_python_versions() -> list[str]:
    """Parse supported Python versions from pyproject.toml."""
    pyproject_path = Path("pyproject.toml")
    pyproject: dict[str, Any] = tomlkit.parse(pyproject_path.read_text())
    classifiers: list[str] = pyproject["project"]["classifiers"]

    result = []
    for c in classifiers:
        if c.startswith("Programming Language :: Python :: 3") and "." in c:
            result.append(c.split("::")[-1].strip())

    return result


@nox.session()
def bandit(session: Session) -> None:
    """Run bandit."""
    install_requirements(session)
    cmd = "bandit -c pyproject.toml -r hermeto noxfile.py"
    session.run(*cmd.split(), *session.posargs, silent=True)


@nox.session()
def black(session: Session) -> None:
    """Run black."""
    install_requirements(session)
    cmd = "black --check --diff hermeto tests noxfile.py"
    session.run(*cmd.split(), *session.posargs, silent=True)


@nox.session()
def flake8(session: Session) -> None:
    """Run flake8."""
    install_requirements(session)
    cmd = "flake8 hermeto tests noxfile.py"
    session.run(*cmd.split(), *session.posargs, silent=True)


@nox.session()
def isort(session: Session) -> None:
    """Run isort."""
    install_requirements(session)
    cmd = "isort --check --diff --color hermeto tests noxfile.py"
    session.run(*cmd.split(), *session.posargs, silent=True)


@nox.session()
def mypy(session: Session) -> None:
    """Run mypy."""
    install_requirements(session)
    cmd = "mypy --install-types --non-interactive hermeto tests noxfile.py"
    session.run(*cmd.split(), *session.posargs, silent=True)


@nox.session(name="python", python=parse_supported_python_versions())
def unit_tests(session: Session) -> None:
    """Run unit tests and generate coverage report."""
    install_requirements(session)
    # install the application package
    session.install(".")
    # disable color output in GitHub Actions
    env = {"TERM": "dumb"} if os.getenv("CI") == "true" else None
    cmd = "pytest --log-level=DEBUG --cov=hermeto --cov-config=pyproject.toml --cov-report=term --cov-report=html --cov-report=xml --no-cov-on-fail tests/unit"
    session.run(*cmd.split(), *session.posargs, env=env)


def _run_integration_tests(session: Session, env: dict[str, str]) -> None:
    install_requirements(session)
    netrc = "machine 127.0.0.1 login cachi2-user password cachi2-pass"
    default_env = {"HERMETO_TEST_NETRC_CONTENT": os.getenv("HERMETO_TEST_NETRC_CONTENT", netrc)}
    default_env.update(env)
    cmd = "pytest --log-cli-level=WARNING tests/integration"
    session.run(*cmd.split(), *session.posargs, env=default_env)


@nox.session(name="integration-tests")
def integration_tests(session: Session) -> None:
    """Run integration tests only for the affected code base in the current branch."""
    _run_integration_tests(session, {})


@nox.session(name="all-integration-tests")
def all_integration_tests(session: Session) -> None:
    """Run all integration tests that are available."""
    _run_integration_tests(
        session,
        {
            "HERMETO_RUN_ALL_INTEGRATION_TESTS": "true",
            "HERMETO_TEST_LOCAL_PYPISERVER": "true",
            "HERMETO_TEST_LOCAL_DNF_SERVER": "true",
        },
    )


@nox.session(name="generate-test-data")
def generate_test_data(session: Session) -> None:
    """Run all integration tests that are available and update SBOMs."""
    _run_integration_tests(
        session,
        {
            "HERMETO_RUN_ALL_INTEGRATION_TESTS": "true",
            "HERMETO_GENERATE_TEST_DATA": "true",
        },
    )


@nox.session(name="pip-compile")
def pip_compile(session: Session) -> None:
    """Update requirements.txt and requirements-extras.txt files."""
    PWD = os.environ["PWD"]
    uv_pip_compile_cmd = (
        "pip install uv && "
        # requirements.txt
        "uv pip compile --generate-hashes --output-file=requirements.txt --python=3.9 --refresh pyproject.toml && "
        # requirements-extras.txt
        "uv pip compile --all-extras --generate-hashes --output-file=requirements-extras.txt --python=3.9 --refresh pyproject.toml"
    )
    cmd = [
        "podman",
        "run",
        "--rm",
        "--volume",
        f"{PWD}:/hermeto:rw,Z",
        "--workdir",
        "/hermeto",
        "docker.io/library/python:3.9-alpine",
        "sh",
        "-c",
        uv_pip_compile_cmd,
    ]
    session.run(*cmd, external=True)
