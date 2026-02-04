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
import re
from pathlib import Path

import nox
from nox.sessions import Session

# default sessions to run (sorted alphabetically)
nox.options.sessions = ["lint", "python"]

# reuse virtual environment for all sessions
nox.options.reuse_venv = "always"

# use venv as the default virtual environment backend
nox.options.default_venv_backend = "venv"

# do not download missing Python interpreter
nox.options.download_python = "never"


def install_requirements(session: Session) -> None:
    """Install requirements for all sessions."""
    session.install("--no-deps", "-r", "requirements-extras.txt")


def parse_supported_python_versions() -> list[str]:
    """Parse supported Python versions from pyproject.toml."""
    pyproject = Path("pyproject.toml").read_text()
    versions = re.findall(r'"Programming Language :: Python :: (3\.\d+)"', pyproject)

    return versions


@nox.session()
def lint(session: Session) -> None:
    """Run linters."""
    exc = None
    install_requirements(session)
    cmds = [
        "ruff check hermeto tests noxfile.py",
        "ruff format --check --diff hermeto tests noxfile.py",
        "mypy --install-types --non-interactive hermeto tests noxfile.py",
    ]

    for cmd in cmds:
        try:
            session.run(*cmd.split(), *session.posargs, silent=True)
        except Exception as e:
            exc = e
    if exc:
        raise exc


@nox.session(name="ruff-fix")
def ruff_fix(session: Session) -> None:
    """Run ruff with auto-fix for linting and formatting."""
    exc = None
    install_requirements(session)
    cmds = [
        "ruff check --fix hermeto tests noxfile.py",
        "ruff format hermeto tests noxfile.py",
    ]

    for cmd in cmds:
        try:
            session.run(*cmd.split(), *session.posargs, silent=True)
        except Exception as e:
            exc = e
    if exc:
        raise exc


@nox.session(name="python", python=parse_supported_python_versions())
def unit_tests(session: Session) -> None:
    """Run unit tests and generate coverage report."""
    install_requirements(session)
    # install the application package
    session.install(".")
    # disable color output in GitHub Actions
    env = {"TERM": "dumb"} if os.getenv("CI") == "true" else None
    cmd = "pytest --log-level=DEBUG -W ignore::DeprecationWarning tests/unit"

    if not session.posargs:
        # enable coverage when no pytest positional arguments are passed through
        cmd += " --cov=hermeto --cov-config=pyproject.toml --cov-report=term --cov-report=html --cov-report=xml --no-cov-on-fail"

    session.run(*cmd.split(), *session.posargs, env=env)


def _run_integration_tests(session: Session, env: dict[str, str]) -> None:
    install_requirements(session)
    netrc = "machine 127.0.0.1 login cachi2-user password cachi2-pass"
    default_env = {"HERMETO_TEST_NETRC_CONTENT": os.getenv("HERMETO_TEST_NETRC_CONTENT", netrc)}
    default_env.update(env)
    cmd = "pytest --log-cli-level=WARNING -W ignore::DeprecationWarning tests/integration"
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
        "uv pip compile --generate-hashes --output-file=requirements.txt --python=3.10 --refresh --no-strip-markers pyproject.toml && "
        # requirements-extras.txt
        "uv pip compile --all-extras --generate-hashes --output-file=requirements-extras.txt --python=3.10 --refresh --no-strip-markers pyproject.toml"
    )
    cmd = [
        "podman",
        "run",
        "--rm",
        "--volume",
        f"{PWD}:/hermeto:rw,Z",
        "--workdir",
        "/hermeto",
        "mirror.gcr.io/library/python:3.10-alpine",
        "sh",
        "-c",
        uv_pip_compile_cmd,
    ]
    session.run(*cmd, external=True)
