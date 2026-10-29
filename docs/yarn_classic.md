# [Yarn Classic (Yarn v1)][]

- [Overview](#overview)
  - [Supported dependencies types](#supported-dependencies-types)
- [Fetching dependencies for Yarn Classic projects](#fetching-dependencies-for-yarn-classic-projects)
- [Prerequisites for an offline build](#prerequisites-for-an-offline-build)
- [Limitations and caveats](#limitations-and-caveats)
  - [Yarn version selection](#yarn-version-selection)
  - [Yarn Zero-Installs are not supported](#yarn-zero-installs-are-not-supported)
  - [Handling of yarn-specific config files](#handling-of-yarn-specific-config-files)
  - [Variables set during fetch phase](#variables-set-during-fetch-phase)

This document outlines the differences between Yarn and Yarn Classic support.
Please refer to the [Yarn documentation][] for common aspects of Package Manager
(PM) behavior. `Yarn` and `Yarn Classic` will be used interchangeably in this
document, any other versions of Yarn will be explicitly mentioned.

## Overview

Hermeto requires Node.js to prefetch Yarn Classic dependencies. It downloads the
[selected Yarn release](#yarn-version-selection) from the npm registry, caches
it, and invokes its JavaScript entry point through `node`. Corepack and a
globally installed Yarn are not required for prefetch. Yarn itself is used by
Hermeto to prepare the dependencies for an offline build.

Hermeto expects to find well-formed `package.json` and `yarn.lock` checked in
into a repository and will not continue if any of the files are missing.
`yarn.lock` must be up to date and all file or path dependencies must be
confined to the project repository.

Prefetching dependencies for Yarn in Hermeto is done using Yarn's
[offline mirror feature][]. The project must be configured to use the offline mirror
feature. Refer to
[Prerequisites for an offline build](#prerequisites-for-an-offline-build)
for details.

### Supported dependencies types

Yarn Classic PM is capable of processing the following types of dependencies

- packages from registries
- packages from git repos
- packages from http/https URLs
- packages from local paths
- local workspace packages
- local link packages

## Fetching dependencies for Yarn Classic projects

The process of fetching dependencies for Yarn Classic is identical to the Yarn
backend. Hermeto automatically detects Yarn Classic (i.e. v1) projects and so
the logic is all conveniently hidden behind a single `yarn` CLI backend option.

For complete example of how to pre-fetch dependencies for see [Yarn documentation][].

## Prerequisites for an offline build

The offline build environment must provide Yarn Classic. Use the same version
that Hermeto selected for prefetch. Hermeto's cached Yarn executable is not
included in the request output.

A project that is to be hermetically built must be configured to use an offline
mirror. This means that Yarn will store compressed archives on the file system
in a mirror directory and will install them from there later without network
access.

The actual build process will use Yarn directly, thus a project must be
configured to use offline mirror either by providing a `.yarnrc` file or by
setting up several environment variables. In case when `.yarnrc` is preferred
it must contain the following lines

```ini
yarn-offline-mirror <absolute path to the request output directory>
yarn-offline-mirror-pruning false
```

It can be either directly written to or `yarn config` could be used

```bash
yarn config set yarn-offline-mirror <absolute path to the request output directory>
yarn config set yarn-offline-mirror-pruning false
```

In case when environment variables approach is preferred the following
variables must be set

```bash
YARN_YARN_OFFLINE_MIRROR=<absolute path to the request output directory>
YARN_YARN_OFFLINE_MIRROR_PRUNING=false
```

Hermeto provides a helper that [generates these variables][] and places them
into a file. Sourcing this file is enough to set them.

## Limitations and caveats

### Yarn version selection

For a project with a Yarn Classic `yarn.lock`, Hermeto reads `packageManager`
from `package.json`. It must name `yarn` and specify an exact supported version:

```json
{
  "packageManager": "yarn@1.22.19"
}
```

Supported Classic versions are `>=1.22.0,<2.0.0`. Prereleases of those boundaries,
such as `1.22.0-rc.1` and `2.0.0-rc.1`, are also rejected.

| `packageManager` | Prefetch behavior |
| --- | --- |
| Absent | Uses the fixed default, Yarn `1.22.22`. |
| `yarn@1.22.19` | Downloads and uses Yarn `1.22.19`. |
| `yarn@1.21.0` | Rejects the unsupported Classic version. |
| `yarn@3.6.2` | Rejects the mismatch with the Classic lockfile. |
| `npm@10.0.0`, `yarn@latest`, or `yarn@^1.22.0` | Rejects the package manager or non-exact version. |

An optional `+<algorithm>.<hex-digest>` suffix pins a checksum for the Yarn
tarball. Hermeto verifies it against the downloaded tarball before extraction.
Hermeto also checks that the executable reports the selected release version.

The `yarn-path` setting in `.yarnrc` remains ignored during prefetch. It does not
override `packageManager` or the default version.

**Compatibility change:** Earlier versions of Hermeto ignored `packageManager`
for Classic projects. A Classic project with `"packageManager": "yarn@3.6.2"`
now fails prefetch. Set the field to a supported Classic version, remove it to
use the default, or migrate the project and its lockfile to a supported Berry
version.

### Yarn Zero-Installs are not supported

Yarn Classic's [Plug'n'Play][] feature is not supported. Any package that uses it
will be rejected. For further details please refer to [Yarn v3 documentation][].

### Handling of yarn-specific config files

Yarn Classic allows a user to provide additional configuration via [.yarnrc][]
and [.npmrc][]. **Hermeto ignores these settings during prefetch phase**.
However a `.yarnrc` could be used for setting up an offline mirror
(see [Prerequisites for an offline build](#prerequisites-for-an-offline-build)).
These settings will be applied during a build phase.

### Variables set during fetch phase

The following variables are set for Yarn in the fetch phase

- `YARN_IGNORE_PATH` is set to "true" so that `yarn-path` cannot replace the
  executable selected by Hermeto
- `YARN_IGNORE_SCRIPTS` is set to "true" which prevents execution of any scripts
  defined in `package.json` or in any dependency
- `YARN_YARN_OFFLINE_MIRROR` is set to point to `deps/yarn-classic` which is
  relative to output directory and will hold fetched dependencies
- `YARN_YARN_OFFLINE_MIRROR_PRUNING` is set to "false" which prevents Yarn from
  attempting to ensure that dependencies are up to date

Once fetch phase is completed Hermeto will need to generate an
[environment file][] with variables pointing to the mirror and instructing
Yarn not to prune it

```shell
YARN_YARN_OFFLINE_MIRROR=<request output directory>
YARN_YARN_OFFLINE_MIRROR_PRUNING=false
```

Sourcing this file will prime Yarn for an offline build.

[.npmrc]: https://classic.yarnpkg.com/en/docs/cli/cache#toc-change-the-cache-path-for-yarn
[.yarnrc]: https://classic.yarnpkg.com/lang/en/docs/yarnrc
[environment file]: usage.md#generate-environment-variables
[generates these variables]: usage.md#generate-environment-variables
[offline mirror feature]: https://classic.yarnpkg.com/blog/2016/11/24/offline-mirror
[Plug'n'Play]: https://classic.yarnpkg.com/en/docs/pnp
[Yarn Classic (Yarn v1)]: https://classic.yarnpkg.com
[Yarn documentation]: https://hermetoproject.github.io/hermeto/yarn
[Yarn v3 documentation]: https://github.com/hermetoproject/hermeto/blob/main/docs/yarn.md#dealing-with-yarn-zero-installs
