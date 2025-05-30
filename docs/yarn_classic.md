# [Yarn Classic (Yarn v1)][]

* [Overview](#overview)
  * [Supported dependencies types](#supported-dependencies-types)
* [Fetching dependencies for Yarn Classic projects](#fetching-dependencies-for-yarn-classic-projects)
* [Prerequisites for an offline build](#prerequisites-for-an-offline-build)
* [Limitations and caveats](#limitations-and-caveats)
  * [Yarn version specified anywhere in the package will be ignored by prefetch](#yarn-version-specified-anywhere-in-the-package-will-be-ignored-by-prefetch)
  * [Yarn Zero-Installs are not supported](#yarn-zero-installs-are-not-supported)
  * [Handling of yarn-specific config files](#handling-of-yarn-specific-config-files)
  * [Variables set during fetch phase](#variables-set-during-fetch-phase)

This document outlines the differences between Yarn Classic and Yarn support.
Please refer to the [Yarn documentation][] for common aspects of PMs behavior.
`Yarn` and `Yarn Classic` will be used interchangeably in this document, any
other versions of Yarn will be explicitly mentioned.

## Overview

Yarn Classic package manager (PM) relies on Yarn Classic being installed on the
system where Hermeto is run. If requested to process a package with Yarn Classic
PM it will check for yarn version and will refuse to proceed if necessary
version is missing. Yarn itself is used by Hermeto under the hood to organize
package processing with some tweaks to ensure that the packages are prepared to
be built in isolation.

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

* packages from registries
* packages from git repos
* packages from http/https URLs
* packages from local paths
* local workspace packages
* local link packages

## Fetching dependencies for Yarn Classic projects

The process of fetching dependencies for Yarn Classic is similar to that for any
other package manager. The name of package manager is `yarn_classic`, and it
does not expect any additional arguments.

Hermeto can be run as follows

```shell
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  '<JSON input>'
```

where 'JSON input' is

```jsonc
{
  // "yarn_classic" tells Hermeto to process Yarn packages
  "type": "yarn_classic",
  // path to the package (relative to the --source directory)
  // defaults to "."
  "path": ".",
}
```

or more simply by just invoking `hermeto fetch-deps yarn_classic`

For complete example of how to pre-fetch dependencies, see [Pre-fetch dependencies][].

## Prerequisites for an offline build

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

### Yarn version specified anywhere in the package will be ignored by prefetch

Unlike in the case of Yarn v3 Hermeto will used whichever version is available
system-wide on a system where a package is prefetched. In most practical cases
this will default to the latest stable version of Yarn Classic (which is not
under active development anymore).

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

* `COREPACK_ENABLE_DOWNLOAD_PROMPT` is set to "0" which prevents Corepack from
  showing the URL when it needs to download software
* `COREPACK_ENABLE_PROJECT_SPEC` is set to "0" which prevents Corepack from
  checking if the package manager corresponds to the one defined for the current
  project
* `YARN_IGNORE_PATH` is set to "true" which ignores any Yarn version specified
  by a user and uses Corepack's version instead
* `YARN_IGNORE_SCRIPTS` is set to "true" which prevents execution of any scripts
  defined in `package.json` or in any dependency
* `YARN_YARN_OFFLINE_MIRROR` is set to point to `deps/yarn-classic` which is
  relative to output directory and will hold fetched dependencies
* `YARN_YARN_OFFLINE_MIRROR_PRUNING` is set to "false" which prevents Yarn from
  attempting to ensure that dependencies are up to date

Once fetch phase is completed Hermeto will need to generate an
[environment file][] with variables pointing to the mirror and instructing
Yarn not to prune it

```shell
YARN_YARN_OFFLINE_MIRROR=<request output directory>
YARN_YARN_OFFLINE_MIRROR_PRUNING=false
```

Sourcing this file will prime Yarn for an offline build.

[environment file]: usage.md#generate-environment-variables-general
[generates these variables]: usage.md#generate-environment-variables-general
[Pre-fetch dependencies]: usage.md#pre-fetch-dependencies-general

[.npmrc]: https://classic.yarnpkg.com/en/docs/cli/cache#toc-change-the-cache-path-for-yarn
[.yarnrc]: https://classic.yarnpkg.com/lang/en/docs/yarnrc
[offline mirror feature]: https://classic.yarnpkg.com/blog/2016/11/24/offline-mirror
[Plug'n'Play]: https://classic.yarnpkg.com/en/docs/pnp
[Yarn Classic (Yarn v1)]: https://classic.yarnpkg.com
[Yarn documentation]: https://hermetoproject.github.io/hermeto/yarn
[Yarn v3 documentation]: https://github.com/hermetoproject/hermeto/blob/main/docs/yarn.md#dealing-with-yarn-zero-installs
