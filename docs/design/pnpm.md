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

pnpm projects have a `package.json` file with an additional `packageManager` field
that specifies the package manager and version used to install the dependencies.

```json
{
  "name": "foo",
  "version": "1.0.0",
  "packageManager": "pnpm@x.y.z"
}
```

## Features

### [Symlinked node_modules structure](https://pnpm.io/symlinked-node-modules-structure)

pnpm uses a unique hard links and symbolic links approach to organize `node_modules`,
which is very different from the traditional flat npm structure. All package files
are stored internally only once in the local file system.

Only direct dependencies of a project appear as top-level directories inside
`node_modules`. By using this approach, it is impossible to use modules that are
not specified in the `package.json` file.

### [Package sources](https://pnpm.io/package-sources)

pnpm uses the [npm registry](https://www.npmjs.com) by default, but also supports
other package sources:

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

pnpm uses the same configuration format as npm. All settings can be configured
via `.npmrc` files. Naturally, respective environment variables for each configuration
option take precedence over the `.npmrc` file.

Authentication settings can be handled via Hermeto configuration
(YAML or environment variables). See the [Proxy support](#proxy-support) section.
Further support for other configuration options is out of scope for now.

## Lockfile format

The format is versioned. The latest version is 9.0, which was introduced in
[pnpm v9.0.0](https://github.com/pnpm/pnpm/releases/tag/v9.0.0). The pnpm GitHub
organization occasionally maintains the various specifications in the [pnpm/spec](https://github.com/pnpm/spec)
GitHub repository. The latest commit (at the time of writing) from 2024 points to
a briefly described format of the lockfile version 9.0 - [https://github.com/pnpm/spec/blob/master/lockfile/9.0.md](https://github.com/pnpm/spec/blob/master/lockfile/9.0.md).

Hermeto will target the lockfile version 9.0. The lockfile version does not seem
to be directly tied to the pnpm version. pnpm major versions 10 or 11 work with
version 9.0.

The root keys and their order are defined in the main pnpm source repository:
[sortLockfileKeys.ts](https://github.com/pnpm/pnpm/blob/main/lockfile/fs/src/sortLockfileKeys.ts).

- `lockfileVersion` (ComVer string, not SemVer - `'9.0'`)
- `settings` (metadata about how the lockfile was generated)
- `patchedDependencies` (mapping of package selectors to patch file metadata)
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
2. Parse the `pnpm-lock.yaml` file and extract data from the `packages` section.
3. Process all packages from the lockfile.
4. Asynchronously download tarballs and verify their checksums.
5. Generate SBOM components from the parsed data.
6. Patch the lockfile for hermetic builds.

The parsing of the lockfile will be the core of the implementation. Each non-registry
package should have already resolved its URL. For registry packages, the URL must
be constructed from the package scope, name, and version (`https://registry.npmjs.org/{scope}/-/{name}-{version}.tgz`).

### Why not use the pnpm binary?

Hermeto could run the [pnpm list](https://pnpm.io/cli/list) command and parse the
JSON output from STDOUT.

```shell
pnpm list --lockfile-only --json --depth Infinity --no-color
```

This approach could be useful for resolving the dependency tree and generating
SBOM components. Each entry from the JSON output has a name, version, and resolved
URL. Transitive development, optional, and peer dependencies are included in that
tree, which reduces custom parsing and surface area for errors.

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
          }
          // more dependencies...
        }
      }
    }
  }
]
```

</details>

However, there are several reasons why using the pnpm binary outweighs its benefits:

1. `pnpm list` output JSON does not include the `resolution.integrity` (SRI) values
stored in the lockfile. Hermeto needs them to verify downloaded tarballs.

2. JSON output size - on a large workspace, `pnpm list` with full depth can produce
enormous JSON (from local testing, a lockfile with tens of thousands of lines expands
to millions of lines of output). This can lead to buffer overflows and other performance
issues.

3. Extra dependency in our image that the maintainers would have to work around.

4. Running any pnpm command exposes the risk of arbitrary code execution in the
form of pnpmfile hooks or scripts. These can be avoided by environment variables,
but still, there is a risk that pnpm could introduce something else in the future.

5. Hermeto has to parse the `pnpm-lock.yaml` anyway to substitute the resolved URLs
with local files for offline build.

### Why not use the node_modules directory instead?

`node_modules` is whatever `pnpm install` produced. Optional and platform-specific
packages differ, and the real files live under `.pnpm` with symlinks in `node_modules`,
so walking the directory does not give a straightforward portable output of tarballs.

There are no supported environment variables that relocate `node_modules` to a stable
path Hermeto could rely on.

### Optional dependencies

These packages appear in the `importers` section in the `optionalDependencies`
field and in the `snapshots` section with the `optional: true` flag.

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

They appear in the `packages` section as regular dependencies.

They can be determined by checking if a dependency is listed in the `devDependencies`
field of a package.json file, though. Transitive development dependencies can be
determined by parsing the lockfile `snapshots` section and applying the breadth-first
search algorithm.

```json
  "devDependencies": {
    "vue": "^3.5.29"
  }
```

```yaml
...
snapshots:
  vue@3.5.30:
    dependencies:
      '@vue/compiler-dom': 3.5.30
      '@vue/compiler-sfc': 3.5.30
      '@vue/runtime-dom': 3.5.30
      '@vue/server-renderer': 3.5.30(vue@3.5.30)
      '@vue/shared': 3.5.30
```

### Peer dependencies

A package declares `peerDependencies` when it expects the host (or another
dependency) to provide a given package, for example, a React plugin expects React.

They appear only as metadata (`peerDependencies` on packages, `transitivePeerDependencies`
in snapshots) and do not have their own entries in the `packages` section. When
a peer is satisfied, the satisfying package appears in `packages` as a regular dependency.
Hermeto will prefetch everything in the `packages` section, so no special handling
is needed for peers.

### Patched dependencies

Patched dependencies allow applying custom modifications (patches) to a specific
version of a dependency without forking the package or waiting for an upstream fix.
They are declared in the top-level patchedDependencies key of the `pnpm-lock.yaml`
file.

```yaml
patchedDependencies:
  lodash@4.17.23:
    hash: 5877a18891ec19fc2a2e4eaf39284abc77fe7f8a910708f424aac3434b0813aa
    path: patches/lodash-4.17.23.patch
```

CycloneDX specification defines patches for a component as a list of `patch` objects.
This approach is already used for the yarn berry backend. See [cyclonedx.org/docs](https://cyclonedx.org/docs/1.6/json/#metadata_tools_oneOf_i0_components_items_pedigree_patches).

SPDX specification defines a similar way to report patches as `externalRefs` object.
See [spdx-spec/v2.3](https://spdx.github.io/spdx-spec/v2.3/how-to-use/#k17-linking-to-a-code-fix-for-a-security-issue)

### SBOM

Components will be generated from the parsed packages. Each package will get
a package URL per the [purl-spec](https://github.com/package-url/purl-spec/blob/main/types-doc/npm-definition.md).

All components will use the `npm` type - `pkg:npm/namespace/name@version`. The namespace
is empty for unscoped packages. Other qualifiers and properties vary depending
on the package source. The PURLs are automatically encoded by the `python-packageurl`
library using `to_string()` method on package URL objects without any additional
configuration.

The official CycloneDX property [cdx:npm:package:development](https://github.com/CycloneDX/cyclonedx-property-taxonomy/blob/main/cdx/npm.md)
will be set for development dependencies and transitive development dependencies
when detected.

The `resolution.integrity` field in the dependency object is an SRI (Subresource
Integrity) string used to verify the checksum of the tarball. Currently, Hermeto
does not have a unified approach to checksum handling in the SBOM. See [hermetoproject/hermeto/issues/852](https://github.com/hermetoproject/hermeto/issues/852).
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
- `pkg:npm/vue/compiler-core@3.5.30` (scoped package)

### JSR registry

```yaml
packages:
  '@jsr/hono__hono@4.12.4':
    resolution: {integrity: sha512-fxOSxFkBlmt2dNAvz2vIj8UmihKrkiyjsvU/A8yvHtd9Pt53WBUxsRbpzl8GXodwA0/63gXGxDFDb7XXcPQXbA==, tarball: https://npm.jsr.io/~/11/@jsr/hono__hono/4.12.4.tgz}
```

JSR packages use the `@jsr/scope__name` naming convention in the lockfile.
The double underscore separates the scope and name of the package.

**PURLs:**

- `pkg:npm/hono/hono@4.12.4?repository_url=https://npm.jsr.io` (JSR prefix dropped)

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
fetchable URL starting with *http://* or *https://*.

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
(`config.pnpm.proxy_url`, `config.pnpm.proxy_login`, `config.pnpm.proxy_password`)
and download packages through the NPM registry proxy.

### Binary artifacts

Out of scope. See [binary-platform-filtering.md](https://github.com/hermetoproject/hermeto/blob/main/docs/design/binary-platform-filtering.md#javascript--source-based-package-managers)
design document.

### Integration tests

Potential candidates for integration tests:

- regular e2e test with all kinds of package sources
- workspace e2e test
- missing lockfile integration test
- unsupported lockfile version integration test
- proxy integration test with npm registry packages
- multiple (independent) projects in one source directory
