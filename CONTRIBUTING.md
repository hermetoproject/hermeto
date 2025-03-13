# Contributing to Cachi2

## Table of contents

* [How to start a contribution](#how-to-start-a-contribution)
  * [How we deal with larger features](#how-we-deal-with-larger-features)
  * [Cachi2's ethos](#cachi2s-ethos)
* [Development](#development)
  * [Virtual environment](#virtual-environment)
  * [Developer flags](#developer-flags)
  * [Coding standards](#coding-standards)
  * [Pull request guidelines](#pull-request-guidelines)
  * [Error message guidelines](#error-message-guidelines)
  * [Test guidelines](#test-guidelines)
  * [Comment guidelines](#comment-guidelines)
  * [Running unit tests](#running-unit-tests)
  * [Running integration tests](#running-integration-tests)
  * [Adding new dependencies to the project](#adding-new-dependencies-to-the-project)
* [Release schedule](#release-schedule)

## How to start a contribution

The team always encourages early communication for all types of contributions. Found a bug or see something that could be improved? Open an issue. Want to address something bigger like adding new package manager support or overhauling the entire project? Open an issue or start a Discussion on Github. This way, we can give you guidance and avoid your work being wasted on an implementation which does not fit the project's scope and goal.

Alternatively, submit a pull request with one of the following

* A high-level design of the feature, highlighting goals and key decision points.
* A proof-of-concept implementation.
* In case the change is trivial, you can start with a draft or even provide a PR with the final implementation.

When working on a pull request please make sure that you have followed [pull
request guidelines](#pull-request-guidelines). Please consult
[Development](#development) section, it contains a lot of helpful information
which will make contributing fast and pleasant process.

### How we deal with larger features

Implementing a larger feature (such as adding a new package manager) is usually a very long and detailed effort. This type of work does not fit well into a single pull request; after several comment threads it becomes almost unmanageable (for you) and very hard to review (for us). For that reason, we request that larger features be split into a series of pull requests. Once approved, these pull requests will be merged into "main", but the new feature will be marked as experimental, and will retain this mark until it meets code quality standards and all necessary changes are merged.

This has several implications

* Experimental features are not fully endorsed by the maintainers, and maintainers will not provide support.
* Experimental features are not production-ready and should never be used in production.
* Always expect that an experimental feature can be fully dropped from this project without any prior notice.
* A feature toggle is needed to allow users to opt-in. This is currently being handled by the `dev-package-managers` flag.
* All SBOMs produced when an experimental feature is used will be marked as such.

If, for some reason, you feel this proposed workflow does not fit the feature you're contributing, please reach out to the maintainers so we can provide an alternative.

#### Making experimental features production-ready

When a feature's development has reached a stable point, you can propose making it an official part of the project. This signals to users that the feature is production-ready. To communicate this intent to the maintainers, open a pull request containing an Architecture Decision Record (ADR) with an outline of the implementation, and a clear statement of all decisions which were made (as well as their rationale).

Once maintainers are confident that they have enough information to maintain the new feature as officially supported they will accept it and help with moving it out from under experimental flag.

### Cachi2's Ethos

Whenever adding a new feature to Cachi2, it is important to keep these fundamental aspects in mind

1. Report prefetched dependencies as accurately as possible

    Cachi2's primary goal is to prefetch content and enable hermetic builds. But hermetic builds are only useful if they end up providing a more accurate SBOM than a non-hermetic build would. Cachi2 strives to download only what's explicitly declared in a project's source code, and accurately report it in the resulting SBOM.

2. Avoid arbitrary code execution

    Some package manager implementations rely on third-party tools to gather data or even for fetching dependencies. This brings the risk of arbitrary code execution, which opens the door for random things to be part of the prefetched content. This undermines the accuracy of the SBOM, and must be avoided at all costs.

3. Always perform checksum validation

    The content provided to the build will only be safe if all of the downloaded packages have their checksums verified. In case a mismatch is found, the entire request must be failed, since the prefetched content is tainted and is potentially malicious. There are two types of checksums: server-provided and user-provided. Cachi2 prefers but does not require the latter. Every dependency which does not have a user-provided checksum verified, must be clearly marked as such in the resulting SBOM (e.g. see 'pip' support). All dependencies must have at least one checksum in order to be considered validated.

4. Favor reproducibility

    Always use fully resolved lockfiles or similar input files to determine what content needs to be download for a specific project (e.g. npm's `package-lock.json`, a `pip-compile` generated `requirements.txt`, etc). Resolving the dependencies during the prefetch will prevent its behavior from being deterministic—in other words, the same repository and the same commit hash should always result in identical prefetch results.

## Development

### Virtual environment

Set up a virtual environment that has everything you will need for development:

```shell
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements-extras.txt
venv/bin/pip install -e .
```

This installs the Cachi2 CLI in [editable mode](https://setuptools.pypa.io/en/latest/userguide/development_mode.html),
which means changes to the source code will reflect in the behavior of the CLI without the need for reinstalling.

You may need to install Python 3.9 in case you want to test your changes against Python 3.9 locally
before submitting a pull request.

```shell
dnf install python3.9
```

The CLI also depends on the following non-Python dependencies:

```shell
dnf install golang-bin git
```

### Developer flags

* `--dev-package-managers` (hidden): enables in-development package manager(s)
  for test. Please refer to other existing package managers to see how they're
  enabled and wired to the CLI.

  Invoke it as `cachi2 fetch-deps --dev-package-managers FOO`

  More explicitly

  * `--dev-package-managers` is a *flag for* `fetch-deps`
  * `FOO` is an *argument to* `fetch-deps` (i.e. the language to fetch for)

### Coding standards

Cachi2's codebase conforms to standards enforced by a collection of formatters, linters and other code checkers:

* [black](https://black.readthedocs.io/en/stable/) (with a line-length of 100) for consistent formatting
* [isort](https://pycqa.github.io/isort/) to keep imports sorted
* [flake8](https://flake8.pycqa.org/en/latest/) to (de-)lint the code and ~~politely~~ ask for docstrings
* [mypy](https://mypy.readthedocs.io/en/stable/) for type-checking. Please include type annotations for new code.
* [pytest](https://docs.pytest.org/en/7.1.x/) to run unit tests and report coverage stats. Please aim for (near) full
  coverage of new code.

Options for all the tools are configured in [pyproject.toml](./pyproject.toml).

Run all the checks that your pull request will be subjected to:

```shell
nox
```

### Pull request guidelines

Observe the following guidelines when submitting a pull request for review

* Write clear and informative *commit messages*. If you want to provide further context, use the PR's description 
* [Sign off on](https://developercertificate.org) all commits
* Please use the PR's description to provide further explanation of the pull request's title
* Split changes into multiple commits such that each commit addresses a clear and concise problem
* Avoid PRs which are too large — split the work into multiple PRs if necessary
* Amend existing commits rather than add new commits to fix issues introduced in the same pull request
* Keep your branch up-to-date using `rebase` — we don't use merge commits
* Verify the coding standards by running the configured linters
* Ensure that every single commit passes CI — this is mandatory
* Feel free to use Github comments to clarify implementation points and consider adding the same comments to the code
* Feel free to use diagrams, sample code, or links to specific parts of external documentation — they are highly encouraged
* Please respond to inline comments made in pull requests: it makes it easier to track what has been done and
  what has not and speeds up review process. "Done" is often enough to indicate that you have reacted to
  a comment
* For trivial reviewers' requests it is ok to implement it, respond with "Done" and resolve the thread.

### Error message guidelines

We try to keep error messages friendly and actionable.

* If there is a known solution, the error message should politely suggest the solution
  * Include a link to the documentation when suitable
* If there is no known solution, suggest where to look for help
* If retrying is a possible solution, suggest retrying and where to look for help if the issue persists

The error classes aim to encourage these guidelines. See the [errors.py](cachi2/core/errors.py) module.

### Comment guidelines

In general, consider adding comments to the code whenever there exists any context which is not obvious from the code alone. When writing a comment do not repeat how a piece of code works, do explain why this is needed.

If your code was inspired by any third-party sources, consider adding a comment with a link to these sources.

### Test guidelines

When extending an existing feature, please add a new test case instead of modifying any existing ones. Large test scenarios with many branching paths are very hard to understand and to maintain. It is ok to copy and paste large parts of an existing test if needed for a new scenario. It is also fine to add a new parameter group to an existing test, as long as the test function remains unchanged.

### Running unit tests

Run all unit tests (but no other checks):

```shell
nox -s python-3.9
```

For finer control over which tests get executed, e.g. to run all tests in a specific file, run:

```shell
nox -s python-3.9 -- tests/unit/test_cli.py
```

Even better, run it stepwise (exit on first failure, re-start from the failed test next time):

```shell
nox -s python-3.9 -- tests/unit/test_cli.py --stepwise
```

You can also run a single test class or a single test method:

```shell
nox -s python-3.9 -- tests/unit/test_cli.py::TestGenerateEnv
nox -s python-3.9 -- tests/unit/test_cli.py::TestGenerateEnv::test_invalid_format
nox -s python-3.9 -- tests/unit/extras/test_envfile.py::test_cannot_determine_format
```

In short, nox passes all arguments to the right of `--` directly to pytest.

#### Generating new test data

Whenever a particular package manager supported version is bumped it is considered good practice
to also re-generate the mocked unit test data using that version of package manager (make sure you
have it available in path). You do that by executing the corresponding script under
`hack/mock-unittest-data`. Note that there may be times the data has to be re-generated even
without bumping the backend version, e.g. we added support for a particular feature of the package
manager which we didn't add at the time of the initial support release.

Example:
```shell
hack/mock-unittest-data/gomod.sh
```

To generate new data (output, dependencies checksums, vendor checksums) and run integration tests with them:

```shell
nox -s generate-test-data
```

Generate data for test cases matching a pytest pattern:

```shell
nox -s generate-test-data -- -k test_e2e_gomod
```

### Running integration tests

Build Cachi2 image (localhost/cachi2:latest) and run most integration tests:

```shell
nox -s integration-tests
```

Build Cachi2 image (localhost/cachi2:latest) and run **all** integration tests:

```shell
nox -s all-integration-tests
```

Note: while developing, you can run the PyPI server with `tests/pypiserver/start.sh &`
and DNF server with `tests/dnfserver/start.sh &` to speed up the tests.

To run integration-tests with custom image, specify the HERMETO\_IMAGE environment variable. Examples:

```shell
HERMETO_IMAGE=quay.io/konflux-ci/cachi2:{tag} nox -s integration-tests
HERMETO_IMAGE=localhost/cachi2:latest nox -s  integration-tests
```

### Adding new dependencies to the project

Sometimes when working on adding a new feature you may need to add a new dependency to the project.
Usually, one commonly goes about it by adding the dependency to one of the ``requirements`` files
or the more modern and standardized ``pyproject.toml`` file.
In our case, dependencies must always be added to the ``pyproject.toml`` file as the
``requirements`` files are generated by the ``pip-compile`` tool to not only pin versions of all
dependencies but also to resolve and pin transitive dependencies. Since our ``pip-compile``
environment is tied to Python 3.9, we have a Makefile target that runs the tool in a container
image so you don't have to install another Python version locally just because of this. To
re-generate the set of dependencies, run the following in the repository and commit the changes:

```shell
nox -s pip-compile
```

## Release schedule

This project follows a weekly release schedule, with planned releases every Tuesday. If there is no
significant content to ship on a given release day, we may skip it. Urgent bug and security fixes
are released promptly as needed at the team’s discretion.
Should we encounter any CI issues on the day of a release, we either release immediately after the
issue is resolved or skip the release based on the nature of the changes (urgent vs regular).
