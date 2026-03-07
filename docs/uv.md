# [uv][]

This document proposes adding uv support to Hermeto as requested in
[issue #1142][].

- [Implementation phases](#implementation-phases)
- [Non-goals](#non-goals)
- [Hermeto's uv support scope](#hermetos-uv-support-scope)
  - [Supported uv.lock source types](#supported-uvlock-source-types)
  - [Dealing with uv workspaces](#dealing-with-uv-workspaces)
  - [Dealing with dev dependencies](#dealing-with-dev-dependencies)
  - [Dealing with git dependencies](#dealing-with-git-dependencies)
  - [Dealing with private indexes](#dealing-with-private-indexes)
- [Specifying packages to process](#specifying-packages-to-process)
  - [Controlling artifact selection](#controlling-artifact-selection)
  - [Environment marker evaluation](#environment-marker-evaluation)
  - [Downloading dependencies](#downloading-dependencies)
  - [Known pitfalls](#known-pitfalls)
- [Using fetched dependencies](#using-fetched-dependencies)
  - [Building your project using pre-fetched uv dependencies](#building-your-project-using-pre-fetched-uv-dependencies)
- [Full example walkthrough](#example)

## Implementation phases

uv provides a `uv pip` sub-command that is fully compatible with the standard
`pip` interface. Since Hermeto already supports pip, supporting `uv pip` projects
requires minimal additional work and is the natural first phase of uv support —
users can already point Hermeto at a `requirements.txt` generated or managed via
`uv pip compile` without any new backend code.

The more significant work — and the focus of this document — is Phase 2: native
support for uv's own lockfile format, `uv.lock`. The two phases are:

- **Phase 1 — `uv pip` compatibility**: projects that use `uv pip` with a
  standard `requirements.txt` are already handled by Hermeto's existing pip
  backend. No new backend is required. Users should follow the [pip][]
  documentation.

- **Phase 2 — `uv.lock` support**: a dedicated uv backend for projects using
  uv's native `uv.lock` lockfile. This phase introduces a new `"type": "uv"`
  input, a `uv.lock` parser, platform-aware artifact selection, and environment
  marker evaluation. The remainder of this document describes Phase 2.

## Non-goals

The uv backend will **not**:

- perform dependency resolution — `uv.lock` is treated as the authoritative,
  pre-resolved source of truth
- invoke the `uv` CLI at any point during the fetch process
- support uv workspaces in the initial implementation — this is out-of-scope
  and will be tracked separately
- support `git`-sourced dependencies — see
  [Dealing with git dependencies](#dealing-with-git-dependencies)
- build or compile packages from source

## Hermeto's uv support scope

[uv][] is a fast Python package and project manager written in Rust. It produces
a `uv.lock` lockfile that captures the fully resolved dependency graph for a
project, including all transitive dependencies and their available distribution
artifacts for every supported platform, all with verified SHA256 hashes.

Hermeto supports uv projects by reading `uv.lock` directly, selecting the
correct artifact for the target build environment, downloading and verifying all
artifacts, and storing them for use in an offline, hermetic build. **Hermeto
never invokes the `uv` CLI during the fetch process.** All parsing is done by
Hermeto's own TOML reader operating directly on `uv.lock`. This eliminates the
risk of arbitrary code execution via uv plugins or hooks and ensures the
generated SBOM accurately reflects only what Hermeto itself fetched.

The source repository **must** contain a committed, up-to-date `uv.lock` file.
Hermeto will refuse to process a project if the lockfile is absent or its
declared `requires-python` constraint is incompatible with the target
environment. To generate or update the lockfile:

```shell
uv lock
```

Commit the resulting `uv.lock` to version control before running Hermeto.

### Supported uv.lock source types

`uv.lock` can record dependencies originating from several source types.
Hermeto supports a subset of these:

| Source type | Supported | Notes |
|---|---|---|
| `registry` (PyPI or custom index) | ✅ | Artifacts fetched directly from the URL recorded in `uv.lock` |
| `url` (direct URL artifact) | ✅ | A `sha256` hash must be present in the lockfile entry |
| `path` (local path dependency) | ✅ | Path must resolve within the repository root; see [Known pitfalls](#known-pitfalls) |
| `git` | ❌ | See [Dealing with git dependencies](#dealing-with-git-dependencies) |

### Dealing with uv workspaces

uv [workspaces][] produce a single `uv.lock` at the workspace root covering all
member packages. Hermeto's initial uv support targets single-package projects.
Workspace support — where multiple member `pyproject.toml` files share one
lockfile — is out-of-scope for the initial implementation and will be tracked
in a follow-on issue.

If your project uses uv workspaces, you will need to wait for workspace support
or restructure your project as a single-package project before using Hermeto.

### Dealing with dev dependencies

Dev dependencies are those declared under `[tool.uv.dev-dependencies]` or
inside `[dependency-groups]` in `pyproject.toml`. By default Hermeto **skips**
dev dependencies entirely — they are not downloaded and not recorded in the
SBOM. This matches the expectation that Hermeto prepares dependencies for
production container builds, not development environments.

To include dev dependencies, pass `"include_dev": "true"` in the JSON input
(see [Specifying packages to process](#specifying-packages-to-process)).

### Dealing with git dependencies

`uv.lock` can record dependencies sourced directly from Git repositories. Due
to the nature of how git sources work — requiring Hermeto to clone a repository
at a specific ref — and the associated risks of fetching content that may not
be verifiable by a lockfile hash, **Hermeto does not support git-sourced
packages**.

If `uv.lock` contains a `git` source entry, Hermeto will raise an error
identifying the offending package. To resolve this, replace the git dependency
with either:

- A released version published to PyPI.
- A direct URL (`url` source) pointing to a source archive with an explicit
  hash, for example:

  ```toml
  # pyproject.toml
  dependencies = [
    "mylib @ https://github.com/example/mylib/archive/refs/tags/v1.2.3.tar.gz"
  ]
  ```

  Then re-run `uv lock` to regenerate `uv.lock` with the direct URL source and
  its hash.

This limitation is consistent with Hermeto's treatment of the equivalent
[Git/GitHub protocol][] in the Yarn backend.

### Dealing with private indexes

If your project uses a private PyPI index configured via `[[tool.uv.index]]` in
`pyproject.toml`, the artifact URLs recorded in `uv.lock` will point to that
private index. Hermeto fetches from those URLs as declared — it does not
re-resolve or reroute them. Ensure the network environment in which Hermeto runs
has access to the private index, or mirror the required packages to a registry
that is reachable from your build environment before running Hermeto.

## Specifying packages to process

Hermeto can be run as follows:

```shell
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  '<JSON input>'
```

where `'JSON input'` is:

```js
{
  // "uv" tells Hermeto to process a uv project using uv.lock
  "type": "uv",
  // path to the package (relative to the --source directory)
  // defaults to "."
  "path": ".",
  // include dev dependencies in the fetch and SBOM
  // defaults to "false"
  "include_dev": "false",
  // allow pre-fetching of binary wheel artifacts
  // defaults to "false" — only sdists are fetched by default
  "allow_binary": "false"
}
```

or more simply by just invoking `hermeto fetch-deps uv`.

> **NOTE**
>
> If your project uses `uv pip` with a standard `requirements.txt` rather than
> `uv.lock`, use `"type": "pip"` instead — the existing pip backend covers this
> case already. See [Implementation phases](#implementation-phases).

For a complete example, see [Example: Pre-fetch dependencies](#pre-fetch-dependencies).

### Controlling artifact selection

`uv.lock` records every platform variant of every artifact — sdists and wheels
for all supported operating systems, CPU architectures, and Python versions — in
a single lockfile. When Hermeto processes `uv.lock` it must decide which
artifact to download for each package.

**By default (`"allow_binary": "false"`)** Hermeto downloads only the sdist for
each package. If a package does not distribute a sdist (some projects distribute
wheels only), Hermeto will raise an error. See
[Dependency does not distribute sources](#dependency-does-not-distribute-sources)
for workarounds.

**When `"allow_binary": "true"`** Hermeto selects the most compatible wheel for
the target build environment using [PEP 425][] platform tag matching
(`packaging.tags`). This logic reuses the existing pip backend's wheel
compatibility implementation. The selection proceeds as follows:

1. Parse the wheel filename of every wheel listed in `uv.lock` for this package
   using `packaging.utils.parse_wheel_filename()` to obtain its tag set.
2. Determine the set of compatible tags for the target Python interpreter using
   `packaging.tags.sys_tags()`.
3. Select the highest-priority compatible wheel (most specific tag match).
4. If no compatible wheel is found, fall back to the sdist.
5. If no compatible wheel exists **and** no sdist is present, raise an error.

Pure Python wheels (tagged `py3-none-any` or `py2.py3-none-any`) are always
compatible regardless of platform.

### Environment marker evaluation

`uv.lock` dependency edges may carry [PEP 508][] environment marker expressions,
for example:

```toml
dependencies = [
  { name = "colorama", marker = "sys_platform == 'win32'" },
  { name = "importlib-metadata", marker = "python_version < '3.10'" },
]
```

Hermeto evaluates these markers against the target build environment using
`packaging.markers.Marker`. Packages whose markers evaluate to `False` for the
target environment are excluded entirely — they are not downloaded and not
recorded in the SBOM. This ensures the SBOM accurately reflects only what will
be present in the final production build.

### Downloading dependencies

Once `uv.lock` has been parsed, markers evaluated, and artifacts selected,
Hermeto downloads each artifact from the URL recorded in `uv.lock` and verifies
its SHA256 hash before storing it. **If the hash of a downloaded artifact does
not match the hash in `uv.lock`, Hermeto aborts immediately.** The build is
rejected rather than allowed to proceed with unverified content.

All artifact URLs must use HTTPS. `file://` and `git+ssh://` scheme URLs are
rejected unless the source type is explicitly listed as supported above.

### Known pitfalls

#### Path dependencies outside the repository root

`uv.lock` may reference local path dependencies (packages located on disk
relative to the project). Hermeto restricts path dependencies to paths that
resolve within the repository root passed via `--source`. Any path dependency
that resolves to a location outside the source root will cause Hermeto to raise
an error.

#### Lockfile version mismatch

`uv.lock` includes a `version` field at the top of the file. If Hermeto
encounters a lockfile version higher than its parser was written to handle, it
raises an error rather than silently producing an incomplete result. Upgrade
Hermeto to a version whose parser supports the newer lockfile format.

#### Dependency does not distribute sources

Some packages do not publish sdists to PyPI and distribute only wheels (for
example, packages with compiled extensions that the maintainer does not publish
sources for). When `"allow_binary": "false"` and no sdist is available for a
package, Hermeto will raise an error.

Possible workarounds:

- Enable wheel fetching with `"allow_binary": "true"` in the JSON input.
- Find the source repository for the project and obtain the source archive for
  the pinned release tag. Specify the dependency via a direct URL in
  `pyproject.toml`:

  ```toml
  dependencies = [
    "mypackage @ https://github.com/example/mypackage/archive/refs/tags/v1.0.0.tar.gz"
  ]
  ```

  Re-run `uv lock` so that `uv.lock` records the direct URL source with its
  hash, then point Hermeto at the updated lockfile.

## Using fetched dependencies

See the [Example](#example) for a complete walkthrough of Hermeto usage.

Hermeto downloads uv artifacts into the `deps/uv/` subpath of the output
directory:

```text
hermeto-output/deps/uv/
├── numpy-2.2.3.tar.gz
├── requests-2.32.3-py3-none-any.whl
├── certifi-2024.12.14-py3-none-any.whl
└── ...
```

### Building your project using pre-fetched uv dependencies

To make `uv sync` use the pre-fetched artifacts instead of reaching out to the
network, several environment variables must be set in your build environment.
Hermeto generates these automatically:

```shell
hermeto generate-env ./hermeto-output -o ./hermeto.env --for-output-dir /tmp/hermeto-output
```

The generated environment file will look similar to:

```shell
export UV_FIND_LINKS=/tmp/hermeto-output/deps/uv
export UV_NO_INDEX=true
```

`UV_FIND_LINKS` points uv at the directory of pre-fetched artifacts.
`UV_NO_INDEX` instructs uv not to query any package index, ensuring the build
is fully offline.

## Example

Let's build a basic uv project hermetically.

```shell
git clone https://github.com/hermetoproject/doc-examples.git --branch=uv-basic
```

then `cd` into the `doc-examples` directory.

### Pre-fetch dependencies

```shell
hermeto fetch-deps \
  --source ./doc-examples \
  --output ./hermeto-output \
  '{"type": "uv", "path": ".", "allow_binary": "true"}'
```

Or more simply, using shorthand notation (sdist-only):

```shell
hermeto fetch-deps --source ./doc-examples --output ./hermeto-output uv
```

### Generate environment variables

```shell
hermeto generate-env ./hermeto-output -o ./hermeto.env --for-output-dir /tmp/hermeto-output
```

Inspect the generated file:

```shell
$ cat ./hermeto.env
export UV_FIND_LINKS=/tmp/hermeto-output/deps/uv
export UV_NO_INDEX=true
```

### Build the application image

```dockerfile
FROM python:3.12-slim

COPY doc-examples/ /src/doc-examples
WORKDIR /src/doc-examples

RUN pip install uv

RUN . /tmp/hermeto.env && uv sync --frozen --no-build

CMD ["uv", "run", "python", "-m", "myapp"]
```

Build the image while mounting the Hermeto output and environment file:

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag my-uv-app
```

[Git/GitHub protocol]: https://yarnpkg.com/protocol/git
[issue #1142]: https://github.com/hermetoproject/hermeto/issues/1142
[PEP 425]: https://peps.python.org/pep-0425/
[PEP 508]: https://peps.python.org/pep-0508/
[pip]: pip.md
[uv]: https://docs.astral.sh/uv/
[workspaces]: https://docs.astral.sh/uv/concepts/workspaces/
