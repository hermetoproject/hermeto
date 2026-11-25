# Hermeto Yarn v1 (Yarn Classic) Prefetch Design

## Yarn Overview
[Yarn](https://github.com/yarnpkg/yarn) is a package manager for JavaScript. While Yarn v1.x is no longer actively developed and has been
succeeded by [Yarn v2+ (Berry)](https://github.com/yarnpkg/berry), it [remains widely used](https://npmtrends.com/yarn).

The [official documentation](https://classic.yarnpkg.com/en/docs) contains information about the various features of Yarn, configuration settings, and CLI commands.

## Proposed Hermeto Implementation Overview

### Specifying a Yarn Package in a Hermeto Request
Hermeto users will specify `yarn` as the package manager or package type for all versions of Yarn. In yarn.lock, the presence of the [`__metadata`](https://github.com/yarnpkg/berry/blob/13d5b3041794c33171808fdce635461ff4ab5c4e/packages/yarnpkg-core/sources/Project.ts#L374) field can be used to presume a Yarn v2+ lockfile as can the presence of the string [`yarn lockfile v1`](https://github.com/yarnpkg/berry/blob/13d5b3041794c33171808fdce635461ff4ab5c4e/packages/yarnpkg-core/sources/Project.ts#L434) for a Yarn v1.x lockfile. The request can then be routed to the correct Yarn module in Hermeto.

### Offline Mirror
Prefetching dependencies for Yarn in Hermeto will be done using Yarn's [offline mirror](https://classic.yarnpkg.com/blog/2016/11/24/offline-mirror/)
feature. When a project is configured to use the offline mirror, Yarn will store compressed archives in the mirror directory and can install
cached project dependencies from there later without network access.

The offline mirror also has a setting that controls [whether it removes package archives](https://classic.yarnpkg.com/en/docs/prune-offline-mirror/)
from the mirror directory when they are no longer needed. Hermeto will want to disable automatic pruning of the offline mirror since we support multiple
yarn projects in a single request.

To enable the offline mirror, the following settings need to be applied either via .yarnrc or from environment variables:

    yarn-offline-mirror <absolute path to the request output directory>
    yarn-offline-mirror-pruning false

    YARN_YARN_OFFLINE_MIRROR=<absolute path to the request output directory>
    YARN_YARN_OFFLINE_MIRROR_PRUNING=false

### Selecting and Installing Yarn

Hermeto reads `packageManager` from `package.json` and requires an exact Yarn
version in the supported Classic range `>=1.22.0,<2.0.0`. Prereleases of the
range boundaries are rejected. If the field is absent, Hermeto uses the fixed
default `1.22.22`. A different package manager, a version range, or a Berry
version paired with a Classic lockfile is rejected before downloading Yarn.

The shared Yarn installer downloads the selected release's tarball from the npm
registry and caches the extracted tree. If `packageManager` includes a checksum
suffix, the installer verifies the original tarball before extraction. It
returns the path to `bin/yarn.js`, which is passed explicitly to every Yarn
command and invoked through `node`. Before prefetch, the reported Yarn version
must match the selected release, excluding checksum metadata.

This replaces the previous policy of ignoring `packageManager` and using
Corepack's default Classic release. The `yarn-path` setting remains ignored.
See the [user-facing version selection rules](../yarn_classic.md#yarn-version-selection)
for examples and migration guidance.

### Configuring Yarn (Using Environment Variables)
The behavior of Yarn can be configured through the use of environment variables, configuration files, and CLI options for specific commands.

Environment variables will be preferred by Hermeto when available.

Unfortunately the official Yarn documentation is quite limited or non-existent concerning environment variables.

The environment variables specific to the Yarn package manager are prefixed by `YARN_`. When specified, they are [loaded into the registry object](https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/registries/base-registry.js#L125) and then made available to the rest of the Yarn application [via the Config object](https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/config.js#L231). An [example](https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/config.js#L422) of this can be seen for the `YARN_YARN_OFFLINE_MIRROR_PRUNING` (undocumented) environment variable.

Yarn also has support for including [NPM environment variables](https://docs.npmjs.com/cli/v10/using-npm/config#environment-variables) prefixed with `NPM_CONFIG_`.

### Project Configuration
The [package.json](https://classic.yarnpkg.com/en/docs/package-json) and [yarn.lock](https://classic.yarnpkg.com/en/docs/yarn-lock)
configuration files must be present in order to process the request.

The node_modules directory should be absent. It may not be possible to guard against a user-specified modulesFolder at build-time.

Yarn projects using the [Plug'n'Play (PnP)](https://classic.yarnpkg.com/lang/en/docs/pnp/) feature will not be supported. PnP enables the use of unplugged dependencies and we are unable to verify them.

Any additional configuration settings in the repository via [.yarnrc](https://classic.yarnpkg.com/en/docs/yarnrc) or .npmrc
files should be ignored (at least initially) for the prefetch by specifying [`--no-default-rc`](https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/cli/index.js#L73) for the yarn install command.

We do not currently honor user-defined settings, but this can be investigated and implemented in Hermeto in a follow-up. At minimum, the
limitation should be documented.

### SBOM Generation
The Yarn 1.x CLI doesn't provide enough information to generate an SBOM, so the yarn.lock and package.json files will need to
be parsed in order to gather the necessary data. The code used to process these files will need to be refactored/improved.

#### Key Points
 - The yarn.lock file can be parsed with [pyarn](https://github.com/containerbuildsystem/pyarn)
 - Workspaces will need to be processed separately from package.json since they do not appear in the yarn.lock file
 - Dev dependencies can be determined by checking if a dependency is listed in the `devDependencies` of any `package.json` file, or if it is a transitive dependency of a dev dependency
 - We will need to ensure that there are no collisions in the names of the archive files being added to the offline mirror

#### PURL Examples
[NPM PURL specification](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#npm)

##### Registry Package
```txt
pkg:npm/%40optional-scope/package-name@1.0.0
```

##### URL Package
```txt
pkg:npm/package-name@1.0.0?checksum=sha512:checksum&download_url=https://example.com/package-name/0101010101010101010101010101010101010101.tar.gz
```

##### Git Package

```txt
pkg:npm/package-name@1.0.0?vcs_url=git%2Bhttps://example.com/namespace/package-name.git%400101010101010101010101010101010101010101
```

##### Workspace/File Package

```txt
pkg:npm/package-name@1.0.0?vcs_url=git%2Bhttps://example.com/namespace/package-name.git%400101010101010101010101010101010101010101#subpath
```

### Avoiding Arbitrary Code Execution
Yarn 1.x appears to honor the `--ignore-scripts` option for the install command. It can also be configured via environment variable.
Used https://github.com/chmeliik/js-lifecycle-scripts/tree/yarn to verify.

### Prefetch
The offline mirror in the request output directory will be populated by running the following Yarn command in the project root:

    node <cached-yarn.js> install --non-interactive --frozen-lockfile --disable-pnp --no-default-rc --ignore-engines

Command: [yarn install](https://classic.yarnpkg.com/en/docs/cli/install)

Options:
 - [non-interactive](https://classic.yarnpkg.com/en/docs/cli/install#toc-yarn-install-non-interactive): Disable interactive prompts
 - [frozen-lockfile](https://classic.yarnpkg.com/en/docs/cli/install#toc-yarn-install-frozen-lockfile): Fail if yarn.lock updates are needed
 - [disable-pnp](https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/cli/index.js#L84): Disable Plug'n'Play installation
 - [no-default-rc](https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/cli/index.js#L73): Prevent Yarn from automatically detecting .yarnrc and .npmrc files
 - [ignore-engines](https://classic.yarnpkg.com/en/docs/cli/install#toc-yarn-install-ignore-engines): Ignore the engine node version during the prefetch

 The behavior of Yarn can also be configured using select environment variables. The following ones should be set for the prefetch:

 - YARN_YARN_OFFLINE_MIRROR=\<absolute path to the request output directory\>
 - YARN_YARN_OFFLINE_MIRROR_PRUNING=false
 - [YARN_IGNORE_PATH](https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/cli/index.js#L627)=true (Keep the executable selected by Hermeto; ignore `yarn-path`)
 - [YARN_IGNORE_SCRIPTS](https://github.com/yarnpkg/yarn/blob/7cafa512a777048ce0b666080a24e80aae3d66a9/src/config.js#L415)=true

### Build Configuration
The user build can be configured to use the offline mirror by configuring the following environment variables:

    YARN_YARN_OFFLINE_MIRROR=<request output directory>
    YARN_YARN_OFFLINE_MIRROR_PRUNING=false

## Implementation Scoping
 - Since we're executing Yarn commands directly, process the request in an [isolated temporary directory](https://github.com/hermetoproject/hermeto/blob/6953607b6ef52fd3f0bef7059d2c926767b1022b/hermeto/core/resolver.py#L41) to avoid any potential changes to the repository
 - Determine whether to process the request with yarn v1 or yarn v2+
 - Read the project files
   - Read/Require package.json
   - Read/Require yarn.lock
     - Ensure yarn.lock is formatted for Yarn 1.x
 - Verify the repository
   - Ensure node_modules directory is not present
   - Ensure that Plug'n'Play (PnP) is not being used
 - Gather the SBOM Components
   - Get the main package component data from package.json
   - Get the workspaces from package.json and add a component for each workspace according to the workspace package.json data
   - Parse yarn.lock with pyarn and add components for each
     - Report missing integrity keys from yarn.lock as missing hashes
     - Ensure that there are no collisions between expected archive names in the offline mirror
   - Ensure devDependencies are reported with the `cdx:npm:package:development` property
 - Configure environment for the prefetch
   - Ensure offline mirror is used
   - Ensure offline mirror is not automatically pruned
   - Ensure package scripts are ignored
   - Select and install a supported Classic version, then verify the executable reports that version
 - Do the prefetch
   - Run yarn install
 - Configure environment for the user build
   - Ensure offline mirror is used from the request output
   - Ensure offline mirror is not pruned automatically
 - Add integration (and e2e) tests
 - Add documentation
   - Document that user-specified settings via .yarnrc/.npmrc are ignored during the prefetch
   - Document how to install yarn itself in the user repository

## Out of Scope
 - Allowing user-specified settings during the prefetch via .yarnrc/.npmrc

## References
