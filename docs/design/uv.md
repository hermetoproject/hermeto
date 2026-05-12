# Adding uv support to Hermeto

## Table of Content


- [Adding uv support to Hermeto](#adding-uv-support-to-hermeto)
  - [Table of Content](#table-of-content)
  - [Background](#background)
  - [How dependencies are specified in `pyproject.toml`](#how-dependencies-are-specified-in-pyprojecttoml)
  - [`uv.toml`](#uvtoml)
  - [`uv.lock`](#uvlock)
  - [Other dependency types](#other-dependency-types)
  - [Workspace](#workspace)
  - [Binary filters](#binary-filters)
    - [Cases](#cases)
  - [Markers](#markers)
  - [Build process in uv](#build-process-in-uv)
    - [Build constraints](#build-constraints)
  - [uv support in Hermeto](#uv-support-in-hermeto)
    - [`uv sync` and `uv build`: why both are required](#uv-sync-and-uv-build-why-both-are-required)
    - [Prefetch (fetch-deps)](#prefetch-fetch-deps)
      - [1) Runtime dependencies (from `uv.lock`)](#1-runtime-dependencies-from-uvlock)
      - [2) Build-Time dependencies](#2-build-time-dependencies)
      - [Checksum handling by source kind](#checksum-handling-by-source-kind)
      - [Lockfile validation](#lockfile-validation)
    - [Lockfile rewriting (inject-files)](#lockfile-rewriting-inject-files)
      - [Registry packages](#registry-packages)
      - [Git packages](#git-packages)
      - [URL packages](#url-packages)
      - [Other source kinds](#other-source-kinds)
      - [Path and URL format conventions](#path-and-url-format-conventions)
    - [Build environment (generate-env)](#build-environment-generate-env)
      - [`UV_OFFLINE`](#uv_offline)
      - [`UV_FIND_LINKS`](#uv_find_links)
      - [`UV_FROZEN`](#uv_frozen)
      - [`UV_NO_BINARY`](#uv_no_binary)
      - [`UV_NO_BINARY_PACKAGE`](#uv_no_binary_package)
    - [Example](#example)
    - [SBOM](#sbom)
    - [Out of scope](#out-of-scope)
    - [Appendix: rejected approaches](#appendix-rejected-approaches)
      - [Using uv natively for fetch](#using-uv-natively-for-fetch)
      - [Using a uv flat index instead of URL rewriting](#using-a-uv-flat-index-instead-of-url-rewriting)
    - [References](#references)

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

## How dependencies are specified in `pyproject.toml`

uv allows dependencies to come from
[different sources](https://docs.astral.sh/uv/concepts/projects/dependencies/).
They're declared in
[`[project.dependencies]`](https://docs.astral.sh/uv/concepts/projects/dependencies/),
with
[`[tool.uv.sources]`](https://docs.astral.sh/uv/concepts/projects/dependencies/#dependency-sources)
extending the standard dependency tables with alternative dependency
sources used during development. The examples below show how each type appears in
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
flask = { git = "git+https://github.com/pallets/flask", rev = "954f5684e4841aad84a8eec7ace7b81a0d3f6831" }
  # pin to a tag
  flask = { git = "https://github.com/pallets/flask", tag = "3.0.0" }
  # pin to a branch
  flask = { git = "https://github.com/pallets/flask", branch = "main" }
  # subdirectory (if package isn't in repo root)
  flask = { git = "https://github.com/pallets/flask", subdirectory = "libs/flask" }
  ```
  >uv treats any git-clonable URL the same way regardless of forge - GitHub, GitLab, Bitbucket etc. 

  >The git+ scheme prefix from `pyproject.toml` is stripped during resolution; uv.lock stores a plain URL with the ref type as a query parameter and the resolved commit in the fragment (source = { git = "https://.../?<tag|branch|rev>=<value>#<sha>" }).
</details>

<details>
  <summary>url</summary>

  ```toml
  [tool.uv.sources]
  httpx = { url = "https://files.pythonhosted.org/.../httpx-0.28.1.tar.gz" }
  ```
</details>


<details>
  <summary>path / directory / editable (local filesystem sources)</summary>

   All three are declared with `path = ...` in `pyproject.toml`. They differ
  on two axes: what `path` points at (directory or file artifact), and
  whether `editable = true` is set.
 
  | `path` points at      | `editable = true` | uv source(in uv.lock) for this case |
  |-----------------------|-------------------|-----------------------|
  | A directory           | not set           | `directory`           |
  | A directory           | set               | `editable`            |
  | A `.whl` or `.tar.gz` | not set           | `path`                |
  | A `.whl` or `.tar.gz` | set               | rejected by uv        |
 
  ---
 
  **`directory`** - `path` points at a source tree (a folder containing
  `pyproject.toml`).
  ```toml
  [tool.uv.sources]
  my-lib = { path = "./libs/my-lib" }
  ```
--- 
**`editable`** - `path` points at a source tree, and `editable = true` is
set. uv installs the package so that edits to the source directory take
effect immediately, without reinstall.

```toml
[tool.uv.sources]
my-lib = { path = "./libs/my-lib", editable = true }
```
>**Only valid for directories** 
  ---
  **`path`** - `path` points at a pre-built artifact file (`.whl` or
  `.tar.gz`). No build step runs for wheels; sdists go through PEP 517.
 
  ```toml
  [tool.uv.sources]
  my-lib = { path = "./dist/my_lib-0.1.0-py3-none-any.whl" }
  ```
 
  ---
 
  Workspace members behave like `editable` by default - see
  [Workspace](#workspace).


</details>
<details>
  <summary>Workspace member</summary>

```toml
  [project]
  dependencies = ["my-lib"]

  [tool.uv.sources]
  my-lib = { workspace = true }
```
>For more details see - [Workspaces](#workspace) 
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

## `uv.toml`
`uv.toml` is a uv-only [configuration file](https://docs.astral.sh/uv/concepts/configuration-files/). It holds the same settings
that would otherwise go under the `[tool.uv]` section of `pyproject.toml`,
nothing more. The difference is structural: for example, `pyproject.toml`
requires the `[tool.uv]` prefix since it's a shared file for multiple
tools. `uv.toml` is uv-only, so the prefix is dropped.

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

## `uv.lock`

The `uv.lock` file follows the TOML format.The top of the file holds resolver-wide settings; below that comes an array of `[[package]]` tables, one per locked package. Within each package, the `source` form determines which sub-keys appear.  The format is versioned — this design targets
`version = 1` and all its future revisions.

> Each version of uv only understands one lockfile format version, and major
> version changes are completely incompatible. Changes in Revision are forward and backward compatible. See [uv source][uv-lockfile-version].

[uv-lockfile-version]: https://docs.astral.sh/uv/concepts/resolution/#lockfile-versioning

<details>
  <summary><strong>Top-level fields (before any <code>[[package]]</code>)</strong></summary>

  Written in this fixed order. Each is emitted only when its condition holds.

  - **`version =` 1** - *always present*. Lockfile-format major version.
  - **`revision =` 3** - *present only if `> 0`*. Backwards-compatible sub-version of `version`.
  - **`requires-python =` ">=3.10"** - *always present*. Supported Python version range as a string.
  - **`resolution-markers =` ["python_full_version < '3.11'", ...]** - *present only when the resolution forked*. 
  - **`supported-markers =` ["sys_platform == 'linux'", ...]** - *present only if user-declared in by*`[tool.uv.environments]`(limits the set of environments that uv will consider when resolving dependencies).
  - **`required-markers =` ["sys_platform == 'linux'", ...]** - *present only if user-declared by* [tool.uv.required-environments]`.
  - **`conflicts =` [[{ package = "foo", extra = "a" }, { package = "foo", extra = "b" }]]** - *present only if non-empty*. Declares mutually-exclusive dependency groups.
  - **`[options]`** - *present only if any sub-field differs from its default*. 
      >Sub-keys: `resolution-mode`, `prerelease-mode`, `fork-strategy`, `exclude-newer`, `exclude-newer-span`, `exclude-newer-package`.
  - **`[manifest]`** - *present only if any sub-field is non-empty*. 
      >Sub-keys: `members = ["my-workspace", "pkg-a", "pkg-b"]`, `requirements`, `constraints`, `overrides`, `excludes`, `build-constraints`, `dependency-groups`, `dependency-metadata`.
</details>
<details><summary><strong><code>[[package]]</code> table</strong></summary>

```toml
[[package]]
name = "..."                       # always present
version = "..."                    # always present
source = { ... }                   # always; exactly one of:
                                     #registry  | git       | url       | path
                                     #directory | editable  | virtual

resolution-markers = [...]         # only if this package is locked at multiple versions
dependencies = [...]               # # only if the package has resolved dependencies
sdist = { ... }                    # varies by source type 
wheels = [...]                     # varies by source type 

## One sub-table per extra declared in [project.optional-dependencies].
# <extra-name> is the user-chosen name of the extra (e.g. "plot", "test").
[package.optional-dependencies]    # only if optional dependencies are present in package
<extra-name> = [...]

# One sub-table per dependency group.
# <group-name> is the user-chosen name of the group. uv treats `dev` as the
# default group (synced unless --no-dev or --no-default-groups is passed).
[package.dev-dependencies]         # only if non-empty
<group-name> = [...]

[package.metadata]                 # only for mutable sources (not registry, not git)
requires-dist = [...]              
provides-extras = [...]            

[package.metadata.requires-dev]    # # only if the package declares dependency groups
<group-name> = [...]
```

**How `pyproject.toml` declarations map to `uv.lock` source kinds** :



| `pyproject.toml` declaration                                 | `uv.lock` source kind |
|--------------------------------------------------------------|-----------------------|
| Plain dependency (e.g. `"flask>=3"`)         | `registry`            |
| `{ git = "..." }`                                            | `git`                 |
| `{ url = "..." }`                                            | `url`                 |
| `{ path = "./dir" }`                                         | `directory`           |
| `{ path = "./dir", editable = true }`                        | `editable`            |
|  `{ path = "./<wheel-or-sdist-file>" }`   | `path`                |
| `{ workspace = true }`                                       | `editable`            |
| Root project, with `[build-system]`                          | `editable`            |
| Root project, without `[build-system]`                       | `virtual`             |

*Examples for  source types :*

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
<details>
  <summary>path</summary>

  ```toml
  [[package]]
  name = "example-package"
  version = "1.0.0"
  source = { path = "dist/example_package-1.0.0-py3-none-any.whl" }
  wheels = [
      { filename = "example_package-1.0.0-py3-none-any.whl", hash = "sha256:..." },
  ]
  ```
</details>

<details>
  <summary>directory</summary>

  ```toml
  [[package]]
  name = "example-package"
  version = "1.0.0"
  source = { directory = "packages/example-package" }
  ```
</details>

<details>
  <summary>editable</summary>

  ```toml
  [[package]]
  name = "example-package"
  version = "1.0.0"
  source = { editable = "packages/example-package" }
  ```
</details>

<details>
  <summary>virtual</summary>

  ```toml
  [[package]]
  name = "root-project"
  version = "0.1.0"
  source = { virtual = "." }
  dependencies = [
      { name = "example-package" },
  ]
  ```
</details>


</details>



## Other dependency types
uv supports two distinct mechanisms for grouping dependencies beyond the main
`[project.dependencies]` list. They are **not the same feature** and serve
different purposes:

- **[Optional dependencies](https://docs.astral.sh/uv/concepts/projects/dependencies/#optional-dependencies)** ("extras") - defined under
  `[project.optional-dependencies]`. They are part of
  the published package metadata (recorded as `Provides-Extra` and
  `Requires-Dist` in the built distribution) and are installable by
  downstream consumers via `pip install pkg[extra-name]`.

- **[Dependency groups](https://docs.astral.sh/uv/concepts/projects/dependencies/#dependency-groups)** - defined under the top-level
  `[dependency-groups]` table per [dependency-groups](https://packaging.python.org/en/latest/specifications/dependency-groups/#dependency-groups). They are **not**
  included in any built distribution; they exist only in `pyproject.toml`
  for project-local use (linting, testing, docs, etc.). uv treats the
  `dev` group as a default group and installs it during `uv sync` unless
  `--no-dev` or `--no-default-groups` is passed.

```toml
[project.optional-dependencies]
plot = ["matplotlib"]

[dependency-groups]
dev = ["pytest", "ruff"]
```

Both are resolved into `uv.lock` the same way as regular dependencies, with
marker / group metadata indicating where they belong.



## Workspace

 A workspace in uv is a collection of one or more packages, called `workspace members`, that are managed together. 
 The workspace root is itself a member, and other members are declared explicitly under `[tool.uv.workspace]` using members (required) and exclude (optional) keys, both of which accept glob patterns. All included members share one uv.lock file and common uv commands will affect all included packages within the workspace.

<details>
  <summary>Sample project with workspaces structure</summary>

```
my-app/
├── packages/
│   ├── pkg-a/
│   │   ├── pyproject.toml
│   │   └── src/
│   │       └── pkg_a/
│   │           └── __init__.py
│   └── pkg-b/
│       ├── pyproject.toml
│       ├── uv.lock
│       └── src/
│           └── pkg_b/
│               └── __init__.py
├── pyproject.toml
├── uv.lock
└── src/
    └── my_app/
        └── __init__.py
```
 
Here `my-app` is the workspace root and `pkg-a` is an included member and `pkg-b` is an excluded member.
Whether `pkg-b` is included or excluded isn't visible from the directory
tree, only the root `pyproject.toml`'s `[tool.uv.workspace].exclude`
entry decides that. The nested `pyproject.toml` and `uv.lock` inside
`pkg-b/` just mean it's also a self-contained project on its own.
 
</details>
<details>
  <summary>root <code>pyproject.toml</code> for a project with workspaces</summary>

  ```toml
  [project]
  name = "my-app"
  version = "0.1.0"
  requires-python = ">=3.10"
  dependencies = ["pkg-a", "tqdm>=4,<5"]
 
  [tool.uv.sources]
  pkg-a = { workspace = true }
 
  [tool.uv.workspace]
  members = ["packages/*"]
  exclude = ["packages/pkg-b"]
  ```
 
</details>

<details>
<summary>how workspace members are recorded in <code>uv.lock</code></summary>

Workspaces add two things to the lockfile on top of the format documented above: a top-level `[manifest]` table listing the workspace members, and
a `[[package]]` entry per member:

**1. `[manifest].members`** - lists every included workspace member, including the root. Excluded packages (e.g. those matched by `[tool.uv.workspace].exclude`) are absent.

```toml
[manifest]
members = [
    "my-app",
    "pkg-a",
]
```

**2. Each member gets a `[[package]]` entry with an editable source**.The workspace root points to `"."`, members point to their subfolder relative to the root.

```toml
[[package]]
name = "my-app"
version = "0.1.0"
source = { editable = "." }
dependencies = [
    { name = "pkg-a" },
    { name = "tqdm" },
]

[package.metadata]
requires-dist = [
    { name = "pkg-a", editable = "packages/pkg-a" },
    { name = "tqdm", specifier = ">=4,<5" },
]

[[package]]
name = "pkg-a"
version = "0.1.0"
source = { editable = "packages/pkg-a" }
dependencies = [
    { name = "requests" },
]

[package.metadata]
requires-dist = [{ name = "requests" }]
```
Other dependencies  appear as `[[package]]` entries, workspaces don't change how those are recorded.
</details>
<details>
  <summary>tool.uv.sources cascading</summary>
  Any `tool.uv.sources` defined at the root level applies to all members automatically, unless a specific member overrides it in its own `tool.uv.sources`:
 
  ```toml
  # root pyproject.toml — applies to all members
  [tool.uv.sources]
  pkg-a = { workspace = true }
  requests = { git = "https://github.com/psf/requests", branch = "main" }
  ```
 
  Now every member gets `requests` from git by default. A member can override this:
 
  ```toml
  # packages/pkg-a/pyproject.toml — overrides root for this member only
  [tool.uv.sources]
  requests = { registry = "https://pypi.org/simple" }
  ```
 
  That is member's own `tool.uv.sources` takes priority, root's `tool.uv.sources` is the fallback for everything else.
 
</details>

## Binary filters

By default Hermeto fetches sdists only. The `binary` input field opts specific packages into wheels. Field shape matches Hermeto's existing pip binary filter.
 
`packages` selects which packages may install as wheels. The other fields constrain which `wheels[].url` entries in `uv.lock` qualify, matched by filename tags the same way pip does.
 
 ```json
{
  "type": "uv",
  "binary": {
    "packages": "numpy,scipy",
    "arch": "x86_64",
    "os": "linux",
    "py_version": 312,
    "py_impl": "cp",
    "abi": ":all:",
    "platform": null
  }
}
```

### Cases

Two environment variables control uv's binary/sdist split at build time:

- `UV_NO_BINARY=true` — forces sdist for every package, including any listed in packages.
- `UV_NO_BINARY_PACKAGE=<pkg1> <pkg2> ...` — forces sdist only for the named packages, leaving the rest free to use wheels. 

| `binary` input                  | Prefetched                                                   | Build-time env                                  |
|---------------------------------|--------------------------------------------------------------|-------------------------------------------------|
| absent                          | sdist for every package                                      | `UV_NO_BINARY=true`                             |
| `packages = :all:`              | sdist + matching wheels for every package                    | (neither set)                                   |
| `packages = <list>`             | sdist for non-listed, matching wheels for listed             | `UV_NO_BINARY_PACKAGE = <non-listed deps>`      |
 
 
- `UV_NO_BINARY=true` forces sdist for *every* package, including listed ones — ignoring the filter.
- Setting neither lets uv pick wheels for any package with a `wheels[]` entry in `uv.lock`. Hermeto only prefetched sdists for non-listed packages, so the build fails on a missing wheel.
[`UV_NO_BINARY_PACKAGE`](https://docs.astral.sh/uv/reference/environment/#uv_no_binary_package) forbids wheels per-package. Listing every non-filter package gives the intended split without rewriting `uv.lock`.
 

## Markers

uv.lock is universal — it contains entries for all platforms and Python
versions the project supports. Each dependency edge can carry a [PEP 508
marker](https://packaging.python.org/en/latest/specifications/dependency-specifiers/#environment-markers) such as
`sys_platform == 'win32'`, restricting it to specific environments.

> TODO: confirm final approach for marker evaluation. Current proposal:
> introduce `evaluate_markers: bool = False` field on `UvPackageInput` to keep
> the default behaviour fetch everything.




## Build process in uv
uv builds distributions through the [PEP 517](https://peps.python.org/pep-0517/)
process, which splits the work between two tools:

`build frontend` - the command the user invokes. For uv that's [`uv build`](https://docs.astral.sh/uv/concepts/projects/build/#using-uv-build). `uv build` invokes the declared backend in an isolated environment per PEP 517, producing a source distribution (.tar.gz) and a binary distribution (.whl) in dist/ by default. The --sdist / --wheel flags limit the output to one format.

`build backend` - the tool that actually turns the project's source tree into a wheel or sdist, declared by the project's pyproject.toml [build-system] table (e.g. hatchling, setuptools, flit-core). **uv works with any PEP 517-compliant backend and also ships its own, [uv_build](https://docs.astral.sh/uv/concepts/build-backend/#using-the-uv-build-backend)**.

The build-time dependencies are
**not recorded in `uv.lock`**  ([astral-sh/uv#5190](https://github.com/astral-sh/uv/issues/5190)).This is why Hermeto must prefetch build dependencies separately via
`uv export` + `pybuild-deps` (see [**2) Build dependencies**](#2-build-dependencies)). All build dependencies live in `{output_dir}/deps/uv/` and are exposed to uv via
`UV_FIND_LINKS` during the build.


### Build constraints

Build constraints restrict which versions of build-time dependencies are used
when uv builds a package from source. They are a guard, including a
package here does **not** install it; the constraint only applies if that
package is actually required as a direct or transitive build-time dependency.
([source](https://docs.astral.sh/uv/pip/compile/#adding-build-constraints))

Supported ways of specifying build constraints are listed here

<details>
  <summary><strong><code>pyproject.toml</code></strong></summary>

  ```toml
  [tool.uv]
  build-constraint-dependencies = ["setuptools>=70", "setuptools!=72.0.0"]
  ```
</details>

<details>
  <summary><strong><code>uv.toml</code></strong> (same setting, no <code>[tool.uv]</code> prefix)</summary>

  ```toml
  build-constraint-dependencies = ["setuptools>=70", "setuptools!=72.0.0"]
  ```
</details>

<details>
  <summary><strong><code>uv.lock</code></strong> (recorded under <code>[manifest]</code> for lockfile invalidation)</summary>

  ```toml
  [manifest]
  build-constraints = [{ name = "setuptools", specifier = ">=70,!=72.0.0" }]
  ```
</details>

<details>
  <summary><strong>Text file</strong>` - <code>requirements.txt</code>like file passed via CLI</summary>

  The filename is user-defined; uv does not look for it automatically.

  ```bash
  uv lock --build-constraints build-constraints.txt
  uv sync --build-constraints build-constraints.txt
  uv build --build-constraint build-constraints.txt   # note: singular flag here
  ```
</details>



The equivalent environment variable is `UV_BUILD_CONSTRAINTS`, which accepts a
space-separated list of file paths.

## uv support in Hermeto

For uv, Hermeto's three phases do the following:

- **[fetch-deps](#prefetch-fetch-deps)** - download every artifact listed in `uv.lock`, plus the
 build dependencies `uv.lock` does not record, into
  `{output_dir}/deps/uv/`.
- **[inject-files](#lockfile-rewriting-inject-files)** - rewrite every artifact URL in `uv.lock` to point into
  `{output_dir}/deps/uv/` instead of the package origin
- **[generate-env](#build-environment-generate-env)** - produce an environment file with the variables the
  user's build step needs to run offline against `{output_dir}/deps/uv/`.

The rejected alternative  is in the [Appendix](#appendix-rejected-approach-using-uv-natively).

### `uv sync` and `uv build`: why both are required

Hermeto has to provide data sufficient for running two uv commands:

- `uv build` produces the project's own wheel/sdist. It does **not** install
  the project's runtime dependencies into any environment.
- `uv sync` installs the runtime dependencies from `uv.lock` into
  the target environment.

The two commands read from different inputs:

| Command     | Reads from                                    | Hermeto's role                                                          |
|-------------|-----------------------------------------------|-------------------------------------------------------------------------|
| `uv sync`   | URLs recorded in `uv.lock`                    | Rewrites those URLs to `file://` paths |
| `uv build`  | Indexes configured in the build environment   | Sets `UV_FIND_LINKS={output_dir}/deps/uv`  |

`uv sync` cannot read from `UV_FIND_LINKS` for locked runtime URL's. Even when the variable is set,
it fetches each URL recorded in `uv.lock` exactly as written. `uv build`
can read from `UV_FIND_LINKS`, and does not look at `uv.lock`.

### Prefetch (fetch-deps)

Prefetch populates `{output_dir}/deps/uv/` as a flat directory containing
every artifact needed by `uv sync` and `uv build`. 

#### 1) Runtime dependencies (from `uv.lock`)

For every `[[package]]` entry in `uv.lock`, dispatch by source kind:

| Source kind                                | Action                                                              |
|--------------------------------------------|---------------------------------------------------------------------|
| `registry`                                 | Download artifact at `sdist.url` and `wheels[].url`.          |
| `git`                                      | Clone repo at the resolved commit, archive as tarball.              |
| `url`                                      | Download artifact at `source.url`.                                  |
| `path`, `editable`, `directory`, `virtual` | Skip - already on local filesystem.                                 |

#### 2) Build-Time dependencies

When uv installs a package from an sdist, it builds a wheel using the PEP 517
build backend (`setuptools`, `hatchling`, `flit-core`, `setuptools-scm`,
etc.) declared in that sdist's `[build-system].requires`. 


[`pybuild-deps`](https://pybuild-deps.readthedocs.io/) is the tool Hermeto
uses to enumerate build-time dependencies. It does not support `uv.lock` as an input format - it reads
`requirements.txt`. The intermediate step is to convert `uv.lock` to
`requirements.txt` via `uv export`, then run `pybuild-deps compile`:

```
uv export --format requirements-txt > requirements.txt
pybuild-deps compile requirements.txt -o build-requirements.txt
```

Entries in `build-requirements.txt` are then downloaded into
`{output_dir}/deps/uv/` using Hermeto's own download mechanism.

**Ordering caveat**: `uv export` regenerates `uv.lock`. The lockfile rewrite
must therefore happen last:

1. `uv export` (regenerates lockfile).
2. `pybuild-deps compile`.
3. Fetch runtime and build-time artifacts.
4. Lockfile rewrite (inject-files).

After step 3, no uv command that regenerates the lockfile may run.

#### Checksum handling by source kind
Hermeto should verify checksums at download time when the lockfile records them.
Per uv's
[`requires_hash`](https://github.com/astral-sh/uv/blob/1fbbfd5acd44d5dec3a1503ca0a508d2dac71ddb/crates/uv-resolver/src/lock/mod.rs#L4336)
logic:

| Source kind                                | Checksum                       |
|--------------------------------------------|--------------------------------|
| `registry` (PyPI)                          | Optional (usually present).    |
| `url`                                      | Required (uv errors otherwise).|
| `git`                                      | Must not be present.           |
| `path` (file artifact) |Required (uv errors if missing) |
| `editable`, `directory`, `virtual` | None |

#### Lockfile validation

Before any artifact download, Hermeto verifies that `uv.lock` is in sync with `pyproject.toml`:

```
uv lock --check --no-cache
```

`--check` asks uv to validate the lockfile without modifying it; uv exits non-zero on mismatch (e.g. a dependency added to `pyproject.toml` but not yet locked). This is the same validation uv applies internally when `--locked` is passed to other commands. See [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/#checking-if-the-lockfile-is-up-to-date).

`--no-cache` prevents uv from populating its cache as a side effect of resolution. uv still uses a temporary directory during the operation, but that directory is cleaned up on exit.

**Container image requirement**: the `uv` binary must be available in Hermeto's container image.

### Lockfile rewriting (inject-files)

`inject-files` needs to modify `uv.lock` in place to redirect every artifact
reference to a local path inside `{output_dir}/deps/uv/`. `pyproject.toml`
is never modified - `--frozen` (see [Build environment
(generate-env)](#build-environment-generate-env)) makes lockfile-only
rewriting sufficient.

**Rewrites necessary for different types of entries are listed below.**

#### Registry packages

Rewrite `sdist.url` and every entry in `wheels[].url` from
`https://files.pythonhosted.org/...` to `file:///{output_dir}/<filename>`.

Before:

```toml
[[package]]
name = "anyio"
version = "4.13.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/.../anyio-4.13.0.tar.gz", hash = "sha256:...", size = 231622, upload-time = "2026-03-24T12:59:09.671Z" }
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

#### Git packages

Replace the entire `source` field; swap `git = "..."` for a plain `path`.

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

#### URL packages

Same pattern: `source = { url = "..." }` becomes `source = { path = "..." }`.


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

#### Other source kinds

Already existing `path`, `editable`, `directory`, `virtual` dependencies have no change.

#### Path and URL format conventions

Two fields, two conventions:

| Field                          | Format                    | Example                                      |
|--------------------------------|---------------------------|----------------------------------------------|
| `source = { path = "..." }`    | Plain path                | `"{output_dir}/flask-...tar.gz"`             |
| `sdist.url`, `wheels[].url`    | URL with `file://` scheme | `"file:///{output_dir}/anyio-...tar.gz"`     |

### Build environment (generate-env)

Hermeto should set the following environment variables for the user's build step:

```
UV_OFFLINE=true
UV_FIND_LINKS={output_dir}/deps/uv
UV_FROZEN=true
UV_NO_BINARY=true            # unset when `binary` filter is configured
UV_NO_BINARY_PACKAGE=...     # set when `binary.packages` is an explicit list
```

The build runs with:

```
uv build
uv sync
```

#### `UV_OFFLINE`

[`UV_OFFLINE=1`](https://docs.astral.sh/uv/reference/environment/#uv_offline) disables all network access for both `uv build` and `uv sync`.

#### `UV_FIND_LINKS` 

[`UV_FIND_LINKS={output_dir}/deps/uv`](https://docs.astral.sh/uv/reference/environment/#uv_find_links) points uv at the local directory where Hermeto prefetched all dependencies.

#### `UV_FROZEN` 
`uv sync` combines resolution + install. By default, every invocation re-resolves before installing.

[`UV_FROZEN=1`](https://docs.astral.sh/uv/reference/environment/#uv_frozen) decouples the two operations: skip resolution, install only.

With frozen mode:

1. Read `uv.lock` directly.
2. Install every `[[package]]` from its `source` field as recorded.
3. No re-resolution from `pyproject.toml`.

#### `UV_NO_BINARY`

[`UV_NO_BINARY=1`](https://docs.astral.sh/uv/reference/environment/#uv_no_binary) tells uv to install from sdists, ignoring the `wheels[]` entries in `uv.lock`.
> Set unless a `binary` filter is configured; see [Binary filters](#binary-filters) for the conditional logic.
 
#### `UV_NO_BINARY_PACKAGE`
 
[`UV_NO_BINARY_PACKAGE=<pkg1> <pkg2> …`](https://docs.astral.sh/uv/reference/environment/#uv_no_binary_package) tells uv to install from sdist only for the listed packages. Set when `binary.packages` is an explicit list; see [Binary filters](#binary-filters) for the conditional logic.

### Example

A typical user Dockerfile after changes:

```dockerfile
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY hermeto-output /hermeto-output
COPY hermeto.env /hermeto.env
COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
COPY README.md /app/README.md
WORKDIR /app


RUN . /hermeto.env && uv build 
RUN . /hermeto.env && uv sync 
```

`hermeto.env` contains the variables Hermeto generates:

```
export UV_OFFLINE=1
export UV_FIND_LINKS=/hermeto-output/deps/uv
export UV_FROZEN=1
export UV_NO_BINARY=1
```

### SBOM

> **TODO**: deeper research needed; deprioritized in the proposal.

### Out of scope

- Private registries (future scope).
- `uv.lock` format versions other than `1`.


### Appendix: rejected approaches
#### Using uv natively for fetch
Letting uv handle resolution, download, and installation natively was ruled
out for two reasons:

1. No uv command exists to fetch without installing. `uv sync` combines
   resolution, download, and installation into a single inseparable step.
2. `uv export` → `requirements.txt` → fetch is lossy: no structured
   download URLs, no source-type distinctions, flattened markers, etc.

#### Using a uv flat index instead of URL rewriting

A flat index is uv's term for a directory of `.whl` / `.tar.gz` files
exposed via `[[tool.uv.index]]` with `format = "flat"`. An alternative to
URL rewriting was considered: leave registry packages' `sdist.url` /
`wheels[].url` fields alone, and expose `{output_dir}/deps/uv/` as a flat
index:

​```toml
[[index]]
name = "local"
url = "/path/to/hermeto-output/deps/uv"
format = "flat"
default = true
​```

This does not work:

- **Without `--frozen`**: uv re-resolves from `pyproject.toml`, dispatches
  git/url sources to their respective fetchers, and hits the network.
  Resolution fails before the index is consulted.
- **With `--frozen`**: uv reads `uv.lock` directly. Registry entries still
  carry hardcoded `https://files.pythonhosted.org/...` URLs in `sdist.url`
  / `wheels[].url`, and uv fetches those exactly. The flat index does not
  override lockfile-pinned URLs.

Registry-URL rewriting in the lockfile is therefore required.   

### References

- uv documentation: https://docs.astral.sh/uv/
- PEP 508 (markers): https://peps.python.org/pep-0508/
- PEP 517 (build system): https://peps.python.org/pep-0517/
- PEP 518: https://peps.python.org/pep-0518/
- PEP 735 (dependency groups): https://peps.python.org/pep-0735/
- pybuild-deps: https://pybuild-deps.readthedocs.io/