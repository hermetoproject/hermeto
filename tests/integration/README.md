# Integration Tests

Build Hermeto image (localhost/hermeto:latest) and run most integration tests:

```shell
nox -s integration-tests
```

Build Hermeto image (localhost/hermeto:latest) and run **all** integration tests:

```shell
nox -s all-integration-tests
```

> [!NOTE]
> While developing, you can run the Nexus server with `tests/nexusserver/run.sh`.

To run integration-tests with custom image, specify the HERMETO\_TEST\_IMAGE environment variable or use the --hermeto-image option. Examples:

```shell
HERMETO_TEST_IMAGE=ghcr.io/hermetoproject/hermeto:{tag} nox -s integration-tests
HERMETO_TEST_IMAGE=localhost/hermeto:latest nox -s integration-tests

nox -s integration-tests -- --hermeto-image=ghcr.io/hermetoproject/hermeto:{tag}
nox -s integration-tests -- --hermeto-image=localhost/hermeto:latest
```

Another way to run integration tests is directly via `pytest`. This approach can be helpful for debugging and gives you more control over which tests are run and how they are executed. Examples:

```shell
HERMETO_TEST_IMAGE=localhost/hermeto:latest pytest [path/to/integration/test]

pytest --hermeto-image=localhost/hermeto:latest [path/to/integration/test]
```

All hermeto integration test CLI options as well as the corresponding environment variables can be listed with `pytest --help`.

To run integration tests in parallel, use the `-n` option + the number of workers. To utilize all CPU cores, use `auto` as the number of workers. Examples:

```shell
nox -s integration-tests -- -n 4
nox -s integration-tests -- -n auto

pytest [path/to/test] -n 4
pytest [path/to/test] -n auto
```

## Generating test data

Any time an existing test scenario is updated the test data needs to be updated
because the existing output SBOMs would not match anymore.
To generate new data and run integration tests with them:

```shell
nox -s generate-test-data
```

Generate data for test cases matching a pytest pattern:

```shell
nox -s generate-test-data -- -k test_e2e_gomod
```

## Adding integration test scenarios

Each package manager backend has a corresponding test file and scenario data
directory:

- `tests/integration/{backend}/test_{backend}.py` - test case definitions
- `tests/integration/{backend}/scenarios/` - test scenario data

A scenario comprises:
- `in/` - input files (lockfiles, application source code, Containerfile, etc.)
- `out/`- expected output files (generated SBOM, intermediary artifacts, etc.)

See [Generating test data](#generating-test-data) on how to generate the
expected outputs for new scenarios.

### Example scenario structure

```
bundler_e2e_ruby33/
├── in/
│   ├── Gemfile
│   ├── Gemfile.lock
│   ├── Containerfile.ruby33
│   └── ... (source files)
└── out/
    ├── bom.json
    └── .build-config.yaml
```

### Best practices

- **Avoid duplication**: When the new scenario is a variant of an existing one
  or any time you can simply re-use existing inputs, do so with the use of
  symlinks in the `in/` directory for everything that can be shared among
  scenarios rather than duplicating data.

- **Keep scenarios minimal**: Strip unnecessary data and metadata from input
  sources. Use tiny packages and keep dependencies (especially the transitive
  ones) at bay - test the behavior you truly need without the added complexity
  of comprehensive real-world projects.

- **Re-use existing patterns**: See existing scenarios for the established
  patterns & conventions.

## Resource tuning

Some end-to-end tests build Rust code inside containers. With many parallel
workers, concurrent `cargo build` processes can saturate the machine. The
following environment variables can help limit resource usage for your own local
use:

* `PYTEST_XDIST_AUTO_NUM_WORKERS` - caps the number of pytest-xdist workers
  when using `-n auto` (defaults to the number of logical CPUs).
* `CARGO_BUILD_JOBS` - limits the number of parallel `rustc` processes cargo
  spawns inside each container build (defaults to all available CPUs).

Integration tests also define several `HERMETO_TEST_*` variables for controlling
the test image, container engine, and other options. Run `pytest --help` inside
the integration test directory for a full list.
