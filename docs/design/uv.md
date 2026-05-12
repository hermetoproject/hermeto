# Draft Proposal: Adding uv support to Hermeto

## Background

[uv](https://docs.astral.sh/uv/) is a Python package and project manager written
in Rust. It replaces the traditional Python packaging files (`setup.py`,
`requirements.txt`, `setup.cfg`, `Pipfile`) with a `pyproject.toml` + `uv.lock` model.

A typical uv-managed project has the following structure:

```
├── .python-version
├── pyproject.toml
├── uv.lock
└── src/
    └── main.py
```

Where `pyproject.toml` declares dependencies and project configuration, and
`uv.lock` is the universal lockfile that pins resolved dependencies for all
platforms.

### Glossary
- **pyproject.toml**: standard Python tool-agnostic project specification file, introduced by [PEP 518](https://peps.python.org/pep-0518/) and extended by [pyproject.toml specification](https://packaging.python.org/en/latest/specifications/pyproject-toml/#pyproject-toml-spec). Declares project metadata, dependencies, build system, and tool-specific settings under namespaced tables (`[tool.uv]`).
- **uv.lock**: universal cross-platform lockfile produced by `uv lock`.
- **uv.toml**: a uv-only configuration file.
- **source**: where uv resolved a package from — registry, git, url, path,
  editable, directory, or virtual.
- **marker**: [PEP 508](https://peps.python.org/pep-0508/#environment-markers)
  environment marker constraining a dependency to specific platforms / Python
  versions.


## Specifying dependencies

uv supports several types of [dependency
sources](https://docs.astral.sh/uv/concepts/projects/dependencies/). The
examples below show the different types of dependencies and how they appear in
`pyproject.toml`.

<details>
  <summary>default registry (PyPI)</summary>


  ```toml
  [project]
  dependencies = [
      "requests>=2.28",
      "flask==3.0.0",
  ]
  ```
</details>

<details>
  <summary>git</summary>

  ```toml
  [project]
  dependencies = ["flask"]

  [tool.uv.sources]
   # pin to a commit
  flask = { git = "https://github.com/pallets/flask", rev = "7ef2946f" }
  # pin to a tag
  flask = { git = "https://github.com/pallets/flask", tag = "3.0.0" }
  # pin to a branch
  flask = { git = "https://github.com/pallets/flask", branch = "main" }
  # subdirectory (if package isn't in repo root)
  flask = { git = "https://github.com/pallets/flask", subdirectory = "libs/flask" }
  ```
</details>

<details>
  <summary>url</summary>

  ```toml
  [tool.uv.sources]
  httpx = { url = "https://files.pythonhosted.org/.../httpx-0.28.1.tar.gz" }
  ```
</details>

<details>
  <summary>path / editable / directory</summary>

  ```toml
  [tool.uv.sources]
  my-lib = { path = "./libs/my-lib", editable = true }
  ```
</details>
<details>
  <summary>workspace member</summary>

```toml
  [project]
  dependencies = ["my-lib"]

  [tool.uv.sources]
  my-lib = { workspace = true }
```
</details>


<details>
  <summary>platform-specific source</summary>

```toml
  [project]
  dependencies = ["httpx"]

  [tool.uv.sources]
  # only use this source on macOS, fall back to PyPI on other platforms
  httpx = { git = "https://github.com/encode/httpx", tag = "0.27.2", marker = "sys_platform == 'darwin'" }
```
</details>

<details>
  <summary>multiple sources</summary>

```toml
  [project]
  dependencies = ["httpx"]

  [tool.uv.sources]
  # different source per platform
  httpx = [
    { git = "https://github.com/encode/httpx", tag = "0.27.2", marker = "sys_platform == 'darwin'" },
    { git = "https://github.com/encode/httpx", tag = "0.24.1", marker = "sys_platform == 'linux'" },
  ]
```
</details>

### uv.lock

The `uv.lock` file follows the TOML format. Each resolved dependency is stored
as a `[[package]]` entry. The format is versioned; this design targets
`version = 1` and all its revisions.

> Each version of uv only understands one lockfile format version, and major
> version changes are completely incompatible. Changes in Revision are forward and backward compatible. See [uv source][uv-lockfile-version].


[uv-lockfile-version]: https://github.com/astral-sh/uv/blob/main/crates/uv-resolver/src/lock/mod.rs

<details>
  <summary>registry package</summary>

  ```toml
  [[package]]
  name = "anyio"
  version = "4.13.0"
  source = { registry = "https://pypi.org/simple" }
  sdist = { url = "...", hash = "sha256:...", size = 99999, upload-time = "..."}
  wheels = [
      { url = "...", hash = "sha256:...", size = 99999, upload-time = "..."},
  ]
  ```
</details>

<details>
  <summary>git package</summary>

  ```toml
  [[package]]
  name = "flask"
  version = "3.0.0"
  source = { git = "https://github.com/pallets/flask?rev=7ef2946f#7ef2946f..." }
  ```
</details>

<details>
  <summary>url package</summary>

  ```toml
  [[package]]
  name = "httpx"
  version = "0.28.1"
  source = { url = "https://files.pythonhosted.org/.../httpx-0.28.1.tar.gz" }
  sdist = { hash = "sha256:..." }
  ```
</details>



### Other dependency types

uv supports [optional
dependencies](https://docs.astral.sh/uv/concepts/projects/dependencies/#optional-dependencies)
(extras) and [dependency
groups](https://docs.astral.sh/uv/concepts/projects/dependencies/#dependency-groups)
(PEP 735), including a default `dev` group.

```toml
[project.optional-dependencies]
plot = ["matplotlib"]

[dependency-groups]
dev = ["pytest", "ruff"]
```

These are resolved into `uv.lock` the same way as regular dependencies, with
markers/group metadata indicating where they belong.


## uv.toml

Both carry identical configuration, the difference is nesting.
`pyproject.toml` requires the `[tool.uv]` prefix since it can be a shared file for multiple tools;
`uv.toml` is uv-only so the prefix is dropped.

```toml
# pyproject.toml          # uv.toml
[tool.uv]                 # (no prefix)
resolution = "lowest"     resolution = "lowest"

[[tool.uv.index]]         [[index]]
url = "https://..."       url = "https://..."
```

> **Note:** `[tool.uv.sources]` cannot go in `uv.toml`. It is project
> metadata, not configuration, and requires a `[project]` table to be
> meaningful. uv enforces this explicitly:
> *"The `sources` field is not allowed in a `uv.toml` file. `sources` is only
> applicable in the context of a project, and should be placed in a
> `pyproject.toml` file instead."*
>See: https://github.com/astral-sh/uv/issues/11632




## Workspaces
> TODO:

## Binary filters

> TODO:

## Markers

uv.lock is universal — it contains entries for all platforms and Python
versions the project supports. Each dependency edge can carry a [PEP 508
marker](https://peps.python.org/pep-0508/#environment-markers) such as
`sys_platform == 'win32'`, restricting it to specific environments.

> TODO: confirm final approach for marker evaluation. Current proposal:
> introduce `evaluate_markers: bool = False` field on `UvPackageInput` to keep
> the default behaviour fetch everything.




## Building distributions

uv supports two related build concepts:

- **Build frontend** — `uv build` itself, which invokes the build backend
  per [PEP 517](https://peps.python.org/pep-0517/).
- **Build backend** —
  uv supports all build backends (as specified by PEP 517), but also provides a native build backend (uv_build).

`uv build` produces a source distribution (`.tar.gz`) followed by a binary
distribution (`.whl`) from that sdist, placing both in `dist/` by default.
Subcommands `--sdist` / `--wheel` limit the output to one format. See
[Building distributions](https://docs.astral.sh/uv/concepts/projects/build/#building-distributions).

### PEP 517 build isolation

uv uses build isolation by default. When a package is built from sdist, uv
creates an isolated environment and installs the packages listed in that
sdist's `[build-system].requires` (e.g. `setuptools`, `hatchling`,
`flit-core`) before invoking the backend. These build-time dependencies are
**not recorded in `uv.lock`**

This is why Hermeto must prefetch build dependencies separately via
`uv export` + `pybuild-deps` (see [Step B](#step-b--build-dependencies)). All
build deps live in `{output_dir}/deps/uv/` and are exposed to uv via
`UV_FIND_LINKS` during the build.

### `uv build`

`uv build` works offline under the same environment Hermeto provides for
`uv sync`:

```
UV_OFFLINE=1
UV_FIND_LINKS={output_dir}/deps/uv
```

`uv build --no-binary` produces both sdist and
wheel without network access, using only the prefetched build dependencies.


Runtime dependencies (`[project.dependencies]`, `[tool.uv.sources]`) are
**not fetched during build**, they are recorded as `Requires-Dist` entries
in the resulting wheel's METADATA for downstream consumers (build-frontends) to install.

> **Note on reproducibility for distributed wheels**: when a project has
> `[tool.uv.sources]` overrides (git/url), these are stripped from the
> published wheel's metadata by default. Consumers of the wheel will resolve
> `Requires-Dist: flask` from PyPI, not from the original git source. This
> is uv's intended behavior and a property of the PEP 621 / PyPI ecosystem.

> **TODO**: use uv_build backend to see what it does with the tool.uv.sources table

## Build constraints

Build constraints restrict which versions of build-time dependencies are used
when uv builds a package from source. They are a guard, including a
package here does **not** install it; the constraint only applies if that
package is actually required as a direct or transitive build-time dependency.
([source](https://docs.astral.sh/uv/pip/compile/#adding-build-constraints))

**`pyproject.toml`**
```toml
[tool.uv]
build-constraint-dependencies = ["setuptools>=70", "setuptools!=72.0.0"]
```

**`uv.toml`** (same setting, no `[tool.uv]` prefix)
```toml
build-constraint-dependencies = ["setuptools>=70", "setuptools!=72.0.0"]
```

**`uv.lock`** (recorded under `[manifest]` for lockfile invalidation)
```toml
[manifest]
build-constraints = [{ name = "setuptools", specifier = ">=70,!=72.0.0" }]
```

**Text file** — a `requirements.txt`-like file passed via CLI. The filename is
user-defined; uv does not look for it automatically.
```bash
uv lock --build-constraints build-constraints.txt
uv sync --build-constraints build-constraints.txt
uv build --build-constraint build-constraints.txt   # note: singular flag here
```
The equivalent environment variable is `UV_BUILD_CONSTRAINTS`, which accepts a
space-separated list of file paths.


# uv support in Hermeto

## Approach 1 (rejected): using uv

Let uv handle resolution, download, and installation natively.

This was ruled out because:

1. No uv command exists to fetch without installing. `uv sync` combines
   resolution, download, and installation into a single inseparable step.
  
2. `uv export` → `requirements.txt` → fetch is lossy: no structured download
   URLs, no source-type distinctions, flattened markers etc.



## Approach 2 (preferred): parse uv.lock and fetch directly



## Prefetching

Hermeto's prefetch phase populates `{output_dir}/deps/uv/` as a flat directory
containing every artifact needed for both the runtime and build-from-source phases.
Two sub-steps run in sequence.

### Step A - Runtime artifacts from `uv.lock`

For every `[[package]]` entry in `uv.lock`, dispatch by source kind:

| Source kind | Action |
|-------------|--------|
| `registry` | Download artifact at `sdist.url` and every `wheels[].url` |
| `git` | Clone repo at the resolved commit, archive as tarball |
| `url` | Download artifact at `source.url` |
| `path`, `editable`, `directory`, `virtual` | Skip — already on local filesystem |

### Why rewrite registry URLs instead of configuring a uv flat index

For git and url deps, the `source` field must be rewritten to `path` regardless.
For **registry deps**, an alternative was considered: leave their
`sdist.url` / `wheels[].url` fields alone, and expose the prefetched directory
to uv as a flat index via `tool.uv.index`:

```toml
[[index]]
name = "local"
url = "/path/to/hermeto-output/deps/uv"
format = "flat"
default = true
```


Why this didn't work:

- **Without `--frozen`**, uv re-resolves from `pyproject.toml`, dispatches
  git/url sources to their respective fetchers, hits the network. The flat
  index is never consulted because resolution fails before reaching install.
- **With `--frozen`**, uv uses `uv.lock` as it is, Registry deps still carry
  hardcoded `https://files.pythonhosted.org/...` URLs in `sdist.url` /
  `wheels[].url`, and uv fetches those exactly. The flat index does not
  override lockfile-pinned URLs.

Registry-URL rewriting in the lockfile is therefore required. Once those
URLs point at local files, the flat index becomes redundant and is not
configured.


### Step B — Build dependencies

`uv.lock` does not record `build-system.requires` of the packages being built
from sdist. These build dependencies (e.g. `hatchling`, `setuptools`,
`flit-core`, `setuptools-scm`) must be discovered separately and prefetched:

```
uv export --format requirements-txt > requirements.txt
pybuild-deps compile requirements.txt -o build-requirements.txt
```

Hermeto then downloads every entry in `build-requirements.txt` into the same
`{output_dir}/deps/uv/` directory using its own download mechanism.


**`uv export` regenerates `uv.lock`.** So the flow should be:

1. `uv export` (regenerates lockfile)
2. `pybuild-deps compile`
3. Fetch both runtime and build-time dependencies
4. Lockfile rewrite

After step 4, no uv command that regenerates the lockfile should run.

## Lockfile rewriting (`inject-files`)

`inject-files` modifies `uv.lock` in place to redirect every artifact reference
to a local path inside `{output_dir}/deps/uv/`. `pyproject.toml` is never
modified, uv's `--frozen` flag (see [Build environment](#build-environment))
makes lockfile-only rewriting sufficient.

## Rewrite rules by source kind

### Registry packages

Rewrite `sdist.url` and every entry in `wheels[].url` from
`https://files.pythonhosted.org/...` to `file:///{output_dir}/<filename>`.
Leave `source = { registry = "..." }` and all `hash` fields untouched.

Before:

```toml
[[package]]
name = "anyio"
version = "4.13.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/.../anyio-4.13.0.tar.gz", hash = "sha256:...", size = 231622, upload-time = "2026-03-24T12:59:09.671Z"  }
wheels = [
    { url = "https://files.pythonhosted.org/.../anyio-4.13.0-py3-none-any.whl", hash = "sha256:...", size = 114353, upload-time = "2026-03-24T12:59:08.246Z" },
]
```

After:

```toml
[[package]]
name = "anyio"
version = "4.13.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "file:///{output_dir}/anyio-4.13.0.tar.gz", hash = "sha256:...", size = 231622, upload-time = "2026-03-24T12:59:09.671Z" }
wheels = [
    { url = "file:///{output_dir}/anyio-4.13.0-py3-none-any.whl", hash = "sha256:...", size = 114353, upload-time = "2026-03-24T12:59:08.246Z" },
]
```

### Git packages

Replace the entire `source` field, swap `git = "..."` for a plain `path`.

Before:

```toml
[[package]]
name = "flask"
version = "3.2.0.dev0"
source = { git = "https://github.com/pallets/flask?rev=7ef2946f#7ef2946f..." }
```

After:

```toml
[[package]]
name = "flask"
version = "3.2.0.dev0"
source = { path = "{output_dir}/flask-gitcommit-7ef2946f.tar.gz" }
```

### URL packages

Same pattern, `source = { url = "..." }` becomes `source = { path = "..." }`.
The companion `sdist.hash` field (if present) stays.

Before:

```toml
[[package]]
name = "httpx"
version = "0.28.1"
source = { url = "https://files.pythonhosted.org/.../httpx-0.28.1.tar.gz" }
sdist = { hash = "sha256:..." }
```

After:

```toml
[[package]]
name = "httpx"
version = "0.28.1"
source = { path = "{output_dir}/httpx-0.28.1.tar.gz" }
sdist = { hash = "sha256:..." }
```

### Other source kinds

`path`, `editable`, `directory`, `virtual`, no change. These already point at
local filesystem locations or represent the project itself.

### Format constraints

Two different fields, two different conventions:

| Field | Format | Example |
|-------|--------|---------|
| `source = { path = "..." }` | Plain  path | `"{output_dir}/flask-...tar.gz"` |
| `sdist.url`, `wheels[].url` | URL with `file://` scheme | `"file:///{output_dir}/anyio-...tar.gz"` |



## Build environment

Hermeto injects the following environment variables for the user's build step:

```
UV_OFFLINE=1
UV_FIND_LINKS={output_dir}/deps/uv
```

The build runs with:

```
uv sync --frozen [--no-binary]
```

`--no-binary` is set when binary filters disallow wheels (default mode in
Hermeto). Omitted when wheels were prefetched.

### Why `--frozen` is required

`uv sync` is two operations combined: **resolution + install**. By default,
every invocation re-resolves before installing.

The default flow (`uv sync` without `--frozen`):

1. Read `[project.dependencies]` and `[tool.uv.sources]` from `pyproject.toml`
2. Compare against `uv.lock` — if anything looks out of date, re-resolve
3. Re-resolution re-reads `[tool.uv.sources]`, re-dispatches each dep to its
   declared fetcher (git/url)
4. **Overwrite `uv.lock`** with the new resolution
5. Install

In a Hermeto-prepared project, `pyproject.toml` still declares
`flask = { git = "..." }`. Re-resolution would re-dispatch flask to the git
fetcher and try to clone from network — failing offline, or breaking the
hermetic guarantee if online. The rewrites would also be wiped before install.

`--frozen` decouples the two operations: skip resolution, install only.

With `--frozen`:

1. Read `uv.lock` directly
2. Install every `[[package]]` from its `source` field as recorded
3. No `pyproject.toml` consultation, no re-resolution, no lockfile mutation

This is why lockfile-only rewriting is sufficient: the rewritten
`source = { path = ... }` and `file://` URL entries are trusted and used
directly. `pyproject.toml` is irrelevant at install time.

## Example: hermetic build

A typical user Dockerfile after Hermeto's prefetch + inject-files step:

```dockerfile
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY hermeto-output /hermeto-output
COPY hermeto.env /hermeto.env
COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
COPY README.md /app/README.md
WORKDIR /app

# uv build path — produces sdist/wheel of the project into dist/
RUN . /hermeto.env && uv build --wheel

# uv sync path — installs runtime dependencies into .venv
RUN . /hermeto.env && uv sync --frozen --no-binary
```

`hermeto.env` contains the environment variables Hermeto generates:

```
export UV_OFFLINE=1
export UV_FIND_LINKS=/hermeto-output/deps/uv
```

### Why `--no-binary` on `uv sync`

`--no-binary` tells uv to install from sdists, ignoring the `wheels[]` entries
in `uv.lock`. When Hermeto's binary filter is unset (default), only sdists are
prefetched — the rewritten `wheels[].url` paths in `uv.lock` would point at
files that don't exist, so uv must be told to use `sdist.url` instead.

When the binary filter is configured to prefetch wheels, `--no-binary` can be
omitted; uv will install directly from the prefetched wheels.

### Checksums

Per uv's [`requires_hash`
logic](https://github.com/astral-sh/uv/blob/main/crates/uv-distribution-types/src/index_url.rs):

- **Registry/PyPI**: checksums optional (usually present)
- **URL**: checksum required (uv errors otherwise)
- **Git**: checksum must not be present
- **Path / editable / directory / virtual**: no checksum

## SBOM

> TODO: this section needs deeper research; deprioritized in the proposal.

### Input files

- `pyproject.toml` — root package name/version (via existing `PyProjectTOML`).
- `uv.lock` — primary source for resolved deps: name, version, source,
  hashes, direct deps.


## Out of scope

- private registries (future scope)
- `uv.lock` format versions other than `1`
- workspaces

## References

- uv documentation: https://docs.astral.sh/uv/
- PEP 508 (markers): https://peps.python.org/pep-0508/
- PEP 517 (build system): https://peps.python.org/pep-0517/
- PEP 518: https://peps.python.org/pep-0518/
- PEP 735 (dependency groups): https://peps.python.org/pep-0735/
- pybuild-deps: https://pybuild-deps.readthedocs.io/