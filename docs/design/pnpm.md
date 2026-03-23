# pnpm

## Background

This design document introduces support for the pnpm package manager in Hermeto.
pnpm is a fast, disk space-efficient package manager for JavaScript and TypeScript.
It is a great candidate for Hermeto in addition to `npm` and `yarn berry` since
many projects are slowly migrating from ancient `yarn classic` to a different
package manager, and pnpm's popularity has been growing [exponentially](https://npmtrends.com/pnpm)
in the last couple of years.

## Project structure

A simple pnpm project could have the following structure:

```text
my-pnpm-project
  ├── node_modules
  ├── pnpm-lock.yaml
  ├── package.json
  └── src/
      └── index.js
```

Compared to an npm project, the `package.json` file has an additional `packageManager`
field that specifies the package manager and version used to install the dependencies.

```jsonc
{
  "name": "foo",
  "version": "1.0.0",
  "packageManager": "pnpm@x.y.z"
  // other fields
}
```

## Features

### [Symlinked node_modules structure](https://pnpm.io/symlinked-node-modules-structure)

pnpm uses a unique hard links and symbolic links approach to organize `node_modules`,
which is very different from the traditional flat npm structure. All package files
are stored only once in a global content-addressable store.

Only direct dependencies of a project appear as top-level directories inside
`node_modules`. By using this approach, it is impossible to use modules that are
not specified in the `package.json` file.

### [Package sources](https://pnpm.io/package-sources)

pnpm uses the [npm registry](https://www.npmjs.com) by default, but also supports
other package sources.

- [JSR registry](https://jsr.io) (JavaScript Registry)
- workspace packages
- local file system
- remote tarballs
- git repositories

### [Workspaces](https://pnpm.io/workspaces)

pnpm supports monorepos via the `pnpm-workspace.yaml` file. A simple workspace
could have the following structure:

```text
my-pnpm-workspace
  ├── package.json
  ├── pnpm-lock.yaml
  ├── pnpm-workspace.yaml
  └── packages/
      └── my-pkg/
          └── package.json
```

`pnpm-workspace.yaml` defines the root of the workspace and enables the
inclusion/exclusion of directories from the workspace.

## [Configuration](https://pnpm.io/npmrc)

pnpm uses the same configuration format as npm. All settings must be configured
via `.npmrc` files. Hermeto's npm package manager backend ignores the `.npmrc`
files completely.

Authentication settings can be handled via Hermeto configuration
(YAML or environment variables). See the [Proxy support](#proxy-support) section.
Further support for other configuration options is currently out of scope.

## Lockfile format

The format is versioned. The latest version is 9.0, which was introduced in
[pnpm v9.0.0](https://github.com/pnpm/pnpm/releases/tag/v9.0.0). The pnpm GitHub
organization occasionally maintains the various specifications in the [pnpm/spec](https://github.com/pnpm/spec)
GitHub repository. The latest commit (at the time of writing) from 2024 points to
a briefly described format of the lockfile version 9.0 - [https://github.com/pnpm/spec/blob/master/lockfile/9.0.md](https://github.com/pnpm/spec/blob/master/lockfile/9.0.md).

Hermeto will target the lockfile version **9.0**. Support for older versions is
out of scope for the initial implementation.

The root keys and their order are also defined in the main pnpm source repository:
[sortLockfileKeys.ts](https://github.com/pnpm/pnpm/blob/main/lockfile/fs/src/sortLockfileKeys.ts).

- `lockfileVersion` (ComVer string, not SemVer - `'9.0'`)
- `settings` (metadata about how the lockfile was generated)
- `importers` (dependency declarations - specifiers and resolved versions)
- `packages` (mapping of dependency ID to dependency object)
- `snapshots` (dependency graphs and optional/peer metadata per package)

<details>
<summary>Example of a pnpm-lock.yaml file with a single dependency</summary>

```yaml
lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:
  .:
    dependencies:
      lodash:
        specifier: ^4.17.23
        version: 4.17.23

packages:
  lodash@4.17.23:
    resolution: {integrity: sha512-LgVTMpQtIopCi79SJeDiP0TfWi5CNEc/L/aRdTh3yIvmZXTnheWpKjSZhnvMl8iXbC1tFg9gdHHDMLoV7CnG+w==}

snapshots:
  lodash@4.17.23: {}
```

</details>

## Implementation

A brief overview of the workflow for Hermeto:

1. Perform basic validation of the project files.
2. Parse the `pnpm-lock.yaml` file.
3. Process all packages from the lockfile.
4. Download tarballs and verify their checksums.
5. Generate SBOM components.
6. Patch the lockfile for hermetic builds.

### Parsing

#### 1. Python YAML library

The first option is to read the `pnpm-lock.yaml` file and extract data from
the `packages` section. We must use this approach due to the lockfile patching
required for hermetic builds.

```python
class PnpmLock(UserDict):

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        super().__init__(data)

    @classmethod
    def from_file(cls, path: Path) -> "PnpmLock":
        with path.open("r") as f:
            data = yaml.safe_load(f)
            return cls(path, data)

```

#### 2. pnpm binary

The second option is to run the `pnpm list` command and parse the JSON output from
STDOUT and add the pnpm binary from corepack to the container image.

This approach could be extremely useful for downloading dependencies and generating
SBOM components, though. Each entry from the JSON output has a `name`, `version`,
and a URL. In addition, transitive development, optional, and peer dependencies
are also resolved. Which means less custom parsing logic and less error-prone code.

##### Arbitrary code execution

pnpm provides the capability to integrate directly into the installation process
through the use of special functions known as hooks. These hooks can be defined
within a file named `.pnpmfile.cjs`.

Running any pnpm command in the project directory loads the [pnpmfile](https://github.com/pnpm/pnpm/tree/main/hooks/pnpmfile)
module as part of project context initialization. This happens for all commands
that touch the project (install, add, list, why, etc.), not just install. See the
[pnpmfile](https://pnpm.io/pnpmfile) documentation page for more details.

pnpm loads the pnpmfile from:

- `.pnpmfile.mjs` (default ESM)
- `.pnpmfile.cjs` (default CommonJS)
- `.npmrc` (via `pnpmfile=path/to/custom.cjs` to point elsewhere)

Apparently, there is no CLI option to disable the pnpmfile specifically for this
command. There is a `--ignore-pnpmfile` option with `--ignore-scripts`, but only
for the `pnpm install` command.

We could temporarily hide all these files from the project directory before invoking
the `pnpm list` command.

```python
class PnpmLock(UserDict):

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        super().__init__(data)

    @classmethod
    def from_pnpm_list(cls, dir: Path) -> "PnpmLock":
        cmd = ["pnpm", "list", "--lockfile-only", "--json", "--depth", "Infinity", "--no-color"]
        result = subprocess.run(cmd, cwd=dir, capture_output=True, text=True, check=True)
        path = dir / "pnpm-lock.yaml"
        return cls(path, json.loads(result.stdout))
```

<details>
<summary>Example of JSON output</summary>

```jsonc
[
  {
    "name": "foo",
    "version": "1.0.0",
    "path": "/path/to/foo",
    "private": false,
    "dependencies": {
      "lodash": {
        "from": "lodash",
        "version": "4.17.23",
        "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.23.tgz",
        "path": ".pnpm/lodash@4.17.23/node_modules/lodash"
      }
    },
    "devDependencies": {
      "vue": {
        "from": "vue",
        "version": "3.5.29",
        "resolved": "https://registry.npmjs.org/vue/-/vue-3.5.29.tgz",
        "path": ".pnpm/vue@3.5.29/node_modules/vue",
        "dependencies": {
          "@vue/compiler-dom": {
            "from": "@vue/compiler-dom",
            "version": "3.5.29",
            "resolved": "https://registry.npmjs.org/@vue/compiler-dom/-/compiler-dom-3.5.29.tgz",
            "path": ".pnpm/@vue+compiler-dom@3.5.29/node_modules/@vue/compiler-dom"
          },
          "@vue/compiler-sfc": {
            "from": "@vue/compiler-sfc",
            "version": "3.5.29",
            "resolved": "https://registry.npmjs.org/@vue/compiler-sfc/-/compiler-sfc-3.5.29.tgz",
            "path": ".pnpm/@vue+compiler-sfc@3.5.29/node_modules/@vue/compiler-sfc"
          }
          // more dependencies...
        }
      }
    }
  }
]
```

</details>

### Why not use the node_modules directory instead?

The [npm cache documentation](https://docs.npmjs.com/cli/v9/commands/npm-cache#a-note-about-the-caches-design)
also explicitly states that the cache is not meant to be a "persistent and reliable
data store". See discussion in [hermetoproject/hermeto#1389](https://github.com/hermetoproject/hermeto/issues/1389).

In addition, there are no environment variables that can be used to configure the
location of the node_modules directory.

### Optional dependencies

These packages appear in the `importers` section in the `optionalDependencies`
field and in the `snapshots` section with the `optional: true` flag. Hermeto will
skip them.

```yaml
importers:
  .:
    optionalDependencies:
      lodash:
        specifier: ^4.17.23
        version: 4.17.23

snapshots:
  lodash@4.17.23:
    optional: true
```

### Development dependencies

They appear in the `packages` section as regular dependencies. Hermeto will prefetch
development dependencies and mark them as development dependencies in the SBOM.
The `package.json` file, specifically the `devDependencies` field, can be used to
find all transitive dependencies.

### Peer dependencies

A package declares `peerDependencies` when it expects the host (or another
dependency) to provide a given package, for example, a React plugin expects React.

They appear only as metadata (`peerDependencies` on packages, `transitivePeerDependencies`
in snapshots) and do not have their own entries in the `packages` section. When
a peer is satisfied, the satisfying package appears in `packages` as a regular dependency.
Hermeto will prefetch everything in the `packages` section, so no special handling
is needed for peers.

### SBOM

Components will be generated from the parsed packages. Each package will get
a package URL per the [purl-spec](https://github.com/package-url/purl-spec).

All components will use the `npm` type - `pkg:npm/namespace/name@version`. The namespace
is empty for unscoped packages. Other qualifiers and properties vary depending
on the package source.

The official CycloneDX property [cdx:npm:package:development](https://github.com/CycloneDX/cyclonedx-property-taxonomy/blob/main/cdx/npm.md)
will be set for development dependencies and transitive development dependencies
when detected.

The `resolution.integrity` field in the dependency object is an SRI (Subresource
Integrity) string used to verify the checksum of the tarball. Currently, we don't
have a unified approach to checksum handling in the SBOM. See [hermetoproject/hermeto/issues/852](https://github.com/hermetoproject/hermeto/issues/852).
Let's not add it to the SBOM for now. Removing it later could be considered as
a breaking change.

### npm registry

```yaml
packages:

  # unscoped package
  express@5.2.1:
    resolution: {integrity: sha512-hIS4idWWai69NezIdRt2xFVofaF4j+6INOpJlVOLDO8zXGpUVEVzIYk12UUi2JzjEzWL3IOAxcTubgz9Po0yXw==}
    engines: {node: '>= 18'}

  # scoped package
  '@vue/compiler-core@3.5.30':
    resolution: {integrity: sha512-s3DfdZkcu/qExZ+td75015ljzHc6vE+30cFMGRPROYjqkroYI5NV2X1yAMX9UeyBNWB9MxCfPcsjpLS11nzkkw==}

```

**PURLs:**

- `pkg:npm/express@5.2.1`
- `pkg:npm/vue/compiler-core@3.5.30`

### JSR registry

```yaml
packages:

  '@jsr/hono__hono@4.12.4':
    resolution: {integrity: sha512-fxOSxFkBlmt2dNAvz2vIj8UmihKrkiyjsvU/A8yvHtd9Pt53WBUxsRbpzl8GXodwA0/63gXGxDFDb7XXcPQXbA==, tarball: https://npm.jsr.io/~/11/@jsr/hono__hono/4.12.4.tgz}
```

JSR packages use the `@jsr/scope__name` naming convention in the lockfile.
The double underscore separates the scope and name of the package.

**PURLs:**

- `pkg:npm/hono/hono@4.12.4?repository_url=https://npm.jsr.io`

### workspace packages

Workspace packages appear in the `importers` section. External dependencies appear
in the `packages` section. Workspace packages will be included in the SBOM, but
do not require any action during prefetch.

**NOTE:** This approach is consistent with PURL generation in the yarn berry backend.

```yaml
importers:

  .:
    dependencies:
      another:
        specifier: workspace:*
        version: link:another
      my-pkg:
        specifier: workspace:*
        version: link:packages/my-pkg

  another: {}

  packages/my-pkg:
    dependencies:
      lodash:
        specifier: ^4.17.23
        version: 4.17.23

packages:

  lodash@4.17.23:
    resolution: {integrity: sha512-LgVTMpQtIopCi79SJeDiP0TfWi5CNEc/L/aRdTh3yIvmZXTnheWpKjSZhnvMl8iXbC1tFg9gdHHDMLoV7CnG+w==}
```

The example shows what a `pnpm-lock.yaml` file looks like, but a better approach
would be to parse the `pnpm-workspace.yaml` file for information about the workspace
packages. The `name` and `version` fields could be determined from the respective
`package.json` file in each workspace directory.

**PURLs:**

- `pkg:npm/another@1.0.0?vcs_url=https://local-git-origin-url#another`
- `pkg:npm/my-pkg@1.0.0?vcs_url=https://local-git-origin-url#packages/my-pkg`

### local file system

Local packages will be included in the SBOM, but do not require any action during
prefetch.

**NOTE:** This approach is consistent with PURL generation in the yarn berry backend.

```yaml
packages:

  # mkdir packages && curl https://registry.npmjs.org/lodash/-/lodash-4.17.23.tgz -o packages/my-lodash.tgz
  # pnpm add ./packages/my-lodash.tgz
  lodash@file:packages/my-lodash.tgz:
    resolution: {integrity: sha512-LgVTMpQtIopCi79SJeDiP0TfWi5CNEc/L/aRdTh3yIvmZXTnheWpKjSZhnvMl8iXbC1tFg9gdHHDMLoV7CnG+w==, tarball: file:packages/my-lodash.tgz}
    version: 4.17.23
```

**PURLs:**

- `pkg:npm/lodash@4.17.23?vcs_url=https://local-git-origin-url#packages/my-lodash.tgz`

### remote tarballs

```yaml
packages:

  lodash@https://github.com/lodash/lodash/archive/refs/tags/4.17.23.tar.gz:
    resolution: {integrity: sha512-v1X5AGPDi5tQF4kS4xZYPSZAzdXD76afftS/Dl+Cf/1n+Y82ADipvDe0JWHtDhxyJ3l76aLDKOwxfCv8rem+aw==, tarball: https://github.com/lodash/lodash/archive/refs/tags/4.17.23.tar.gz}
    version: 4.17.23
    engines: {node: '>=4.0.0'}
```

**PURLs:**

- `pkg:npm/lodash@4.17.23?download_url=https://github.com/lodash/lodash/archive/refs/tags/4.17.23.tar.gz`

When adding a remote tarball via the `pnpm add` command, the argument must be a
fetchable URL starting with "http://" or "https://".

### git repositories

```yaml
packages:

  is-positive@https://codeload.github.com/kevva/is-positive/tar.gz/97edff6f525f192a3f83cea1944765f769ae2678:
    resolution: {tarball: https://codeload.github.com/kevva/is-positive/tar.gz/97edff6f525f192a3f83cea1944765f769ae2678}
    version: 3.1.0
    engines: {node: '>=0.10.0'}
```

**PURLs:**

- `pkg:npm/is-positive@3.1.0?vcs_url=git+https://github.com/kevva/is-positive.git#97edff6f525f192a3f83cea1944765f769ae2678`

When adding a git repository via the `pnpm add` command, the argument can be just
an organization and repository name. Optionally, a branch, tag, or commit hash
can be added. The difference would be only in the `importers` section of the
lockfile and the `specifier` field.

```sh
pnpm add org/repo
pnpm add org/repo#branch
pnpm add org/repo#tag
pnpm add org/repo#commit
```

### Offline build

Hermeto will prepare a project for offline build by patching the lockfile with
the local file paths pointing to the output directory.

No environment variables are needed during the build. The build runs `pnpm install`
(or equivalent) with the patched lockfile, and pnpm will read tarballs from the
local paths.

All prefetched packages (npm registry, JSR, git, remote tarballs) are patched to
`file://` paths pointing to the output directory. Workspace and local file dependencies
are not patched since they are not prefetched.

Example of a patched resolution after substitution of `${output_dir}` for `/tmp/hermeto-output`:

```yaml
packages:
  lodash@4.17.23:
    resolution:
      integrity: sha512-LgVTMpQtIopCi79SJeDiP0TfWi5CNEc/L/aRdTh3yIvmZXTnheWpKjSZhnvMl8iXbC1tFg9gdHHDMLoV7CnG+w==
      tarball: file:///tmp/hermeto-output/deps/pnpm/lodash-4.17.23.tgz
```

### Proxy support

Hermeto can use the same proxy configuration as the npm backend
(`config.pnpm.proxy_url`, `config.pnpm.proxy_login`, `config.pnpm.proxy_password`).

### Binary artifacts

Out of scope. See [binary-platform-filtering.md](https://github.com/hermetoproject/hermeto/blob/main/docs/design/binary-platform-filtering.md#javascript--source-based-package-managers)
design document.

### Integration tests

Potential candidates for integration tests:

- regular e2e test with all kinds of package sources
- workspace e2e test
- missing lockfile integration test
- invalid lockfile version integration test
- proxy integration test with npm registry packages
- multiple (independent) projects in one source directory
