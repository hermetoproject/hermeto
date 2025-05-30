# [yarn][]

- [Hermeto's Yarn support scope](#hermetos-yarn-support-scope)
  - [Supported Yarn versions](#supported-yarn-versions)
  - [Supported Yarn protocols/locators](#supported-yarn-protocolslocators)
  - [Dealing with .yarnrc.yml](#dealing-with-yarnrcyml)
  - [Dealing with Yarn Zero-Installs](#dealing-with-yarn-zero-installs)
  - [Dealing with plugins](#dealing-with-plugins)
- [Specifying packages to process](#specifying-packages-to-process)
  - [Controlling Yarn's behavior](#controlling-yarns-behavior)
  - [Downloading dependencies](#downloading-dependencies)
  - [Known pitfalls](#known-pitfalls)
- [Using fetched dependencies](#using-fetched-dependencies)
  - [Building your project using the pre-fetched Yarn dependency cache](#building-your-project-using-the-pre-fetched-yarn-dependency-cache)

## Hermeto's Yarn support scope

### Supported Yarn versions

Hermeto currently supports Yarn versions 1, 3 and 4. Version 1 is referred to as
"Yarn Classic" and is covered in Hermeto's [Yarn Classic][] doc. **This**
document describes Yarn v3 and v4 support.

### Supported Yarn protocols/locators

Hermeto currently supports all standard [Yarn protocols][] except for

- [Exec protocol][]
- [Git/GitHub protocol][]

Due to the nature of how the two protocols above work, mainly related to
potentially executing arbitrary code, adding support for them with future
releases of Hermeto is unlikely. For further details on Yarn protocols and their
practical `package.json` examples, please head to the official Yarn
documentation on protocols linked earlier in this section.

### Dealing with .yarnrc.yml

Hermeto parses the project's `.yarnrc.yml` file and analyzes configuration
settings. Before hermeto proceeds with the actual dependency fetching, it
verifies whether all [configuration settings][] that set a path to a resource
don't point outside of the source repository, so in order to avoid any issues
reported by Hermeto in this regard make sure all your project resource
references are bound by the repository. Part of the analysis of the repository's
`.yarnrc.yml` file is detection of plugin usage which is further explained in
[Dealing with plugins](#dealing-with-plugins).

### Dealing with Yarn Zero-Installs

Yarn's [PnP Zero-Installs][] are unsupported due to the potentially
[unplugged dependencies][] checked into the repository which simply make it
impossible for the Yarn cache to be checked for integrity using Yarn's standard
tooling (i.e. `yarn install --check-cache`).

> **NOTE**
>
> The same applies to dealing with the `node_modules` top level directory which,
> if checked into the repository, can also serve the Zero-Install purpose. If
> you need further information on which dependency linking mode is used, have a
> look at the [nodeLinker][] and on the [PnP][] approach in general.

Also note that we may reconsider our initial decision when it comes to
Zero-Installs provided the input repository doesn't rely on any dependencies
which may include install scripts leading to their unpacking in a form of
`.yarn/unplugged` entries.

### Dealing with plugins

Due to the nature of plugins (which can potentially execute arbitrary code, by
e.g. adding new protocol resolvers), **all** plugins except for the official
ones (see "Default Plugins" in the [Yarn API docs][]) are disabled during the
dependency prefetch stage to ensure no other changes apart from downloading
dependencies took action.

For Yarn v3, even the official plugins are disabled, with the exception of
[exec][].

> **NOTE**
>
> hermeto doesn't taint your project files, so any plugins you set will be
> enabled normally in your build environment, the only problem that can arise is
> if any of your specified plugins adds a new protocol which hermeto doesn't
> know about in which case the dependency pre-fetch stage will fail with an
> error.

## Specifying packages to process

A package is a file or directory that is described by a [package.json][] file
(also called a manifest).

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
  // "yarn" tells Hermeto to process Yarn packages
  "type": "yarn",
  // path to the package (relative to the --source directory)
  // defaults to "."
  "path": ".",
}
```

or more simply by just invoking `hermeto fetch-deps yarn`.

For complete example of how to pre-fetch dependencies, see
[Pre-fetch dependencies][].

### Controlling Yarn's behavior

Hermeto instructs Yarn to download dependencies explicitly declared in
`package.json`. The dependencies are then further managed in a `yarn.lock` file
that Yarn CLI manages automatically and creates it if missing. However,
**Hermeto will refuse to process your repository if the file is missing**, so be
sure to check that file into the repository. Also make sure that the file is up
to date for which you can use [yarn install][].

### Downloading dependencies

If Yarn is configured to operate in the [PnP][] mode (the default in Yarn v3 or
v4) Yarn will store all dependencies as [ZIP archives][].

Once the source repository analysis and verification described in the earlier
sections of this document has been completed, then it's essentially just a
matter of hermeto internally invoking `yarn install --mode=skip-build` to fetch
all dependencies (including transitive dependencies).

### Known pitfalls

If your repository isn't in a pristine state (i.e. you tried to run `yarn
install` previously on your own without Hermeto) what may happen is that Hermeto
will assume the repository makes use of
[Zero-Installs](#dealing-with-yarn-zero-installs). The workaround here is
simple, just run `yarn cache clean` and hermeto will then process your
repository as normal.

## Using fetched dependencies

See also [usage.md][]for a complete example of Hermeto usage.

Hermeto downloads the Yarn dependencies into the `deps/yarn/` subpath of the
output directory (see the snippet below).

```text
hermeto-output/deps/yarn
└── cache
    ├── abbrev-npm-1.1.1-3659247eab-8.zip
    ├── agent-base-npm-6.0.2-428f325a93-8.zip
    ├── agentkeepalive-npm-4.3.0-ac3d8e6807-8.zip
    ├── aggregate-error-npm-3.1.0-415a406f4e-8.zip
    ├── ansi-regex-npm-3.0.1-01f44078a3-8.zip
...
```

### Building your project using the pre-fetched Yarn dependency cache

In order to use the hermeto pre-fetched Yarn dependency cache obtained from the
previous step several environment variables need to be set in your build
environment. See [Generate environment variables][] for more details on how
these can be generated by hermeto automatically in a form of a environment file
that can sourced as part of your container build recipe. Here's a snippet of the
most important variables hermeto needs to be set in the build environment along
with explanation

```shell
# Point Yarn to our pre-populated global cache
YARN_GLOBAL_FOLDER=<hermeto_output_dir>/deps/yarn

# Yarn must not rely solely on the global cache (the pre-fetched one) because
# it'll likely only be available (i.e. mounted) during the (container) build
# time, but not runtime. We specifically want Yarn to copy those dependencies
# from the global cache to the project's local cache
YARN_ENABLE_GLOBAL_CACHE=false

# Must be set to true, otherwise Yarn will not make use of the pre-populated
# global cache we're pointing it at with YARN_GLOBAL_FOLDER at build time.
YARN_ENABLE_MIRROR=true

# Must be false otherwise 'yarn install' will fail to populate the project's
# local cache (pointed to by the 'cacheFolder' setting) from the global cache
# (the pre-fetched one).
YARN_ENABLE_IMMUTABLE_CACHE=false
```

[Generate environment variables]: usage.md#generate-environment-variables-yarn
[Pre-fetch dependencies]: usage.md#pre-fetch-dependencies-yarn
[usage.md]: usage.md

[configuration settings]: https://yarnpkg.com/configuration/yarnrc
[Exec protocol]: https://yarnpkg.com/protocol/exec
[exec]: https://v3.yarnpkg.com/features/plugins#official-plugins
[Git/GitHub protocol]: https://yarnpkg.com/protocol/git
[nodeLinker]: https://yarnpkg.com/configuration/yarnrc#nodeLinker
[package.json]: https://yarnpkg.com/configuration/manifest
[PnP Zero-Installs]: https://yarnpkg.com/features/caching#zero-installs
[PnP]: https://yarnpkg.com/features/pnp
[unplugged dependencies]: https://yarnpkg.com/advanced/lexicon#unplugged-package
[Yarn API docs]: https://yarnpkg.com/api
[Yarn Classic]: https://hermetoproject.github.io/hermeto/yarn_classic
[yarn install]: https://yarnpkg.com/getting-started/usage/#installing-all-the-dependencies
[Yarn protocols]: https://yarnpkg.com/protocols
[yarn]: https://yarnpkg.com
[ZIP archives]: https://yarnpkg.com/features/pnp/#packages-are-stored-inside-zip-archives-how-can-i-access-their-files
