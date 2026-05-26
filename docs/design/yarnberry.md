# Berry (Yarn 2+) Design Document

## Overview

[Berry](https://github.com/yarnpkg/berry) (commonly known as Yarnberry or Yarn 2+) is a modern
rewrite of the Yarn package manager for JavaScript/Node.js. Where Yarn 1 is mostly compatible with
npm, Yarnberry goes off to explore a brave new world — for example, it doesn't even create a
`node_modules` directory by default, instead preferring the [Plug'n'Play][pnp] approach (but
this is configurable).

Yarnberry's architecture is based around plugins. The core doesn't do much by itself — it's the
plugins who resolve, fetch and install your dependencies. Yarnberry comes with a set of
[pre-installed plugins](https://github.com/yarnpkg/berry/tree/master/packages), which make up the
base `yarn` command. But you are free to add plugins from various sources. This has severe
implications for Hermeto — see [Dealing with Plugins](#dealing-with-plugins).

*The [architecture][architecture] documentation is also worth a
read.*

### Developer Workflow

1. **Prerequisites**: A version of Node.js with [Corepack][corepack]
   enabled, or a global Yarn installation. Projects typically commit the Yarnberry binary into the
   repo via `yarn init -2`, which stores it in `.yarn/releases/` and configures it in `.yarnrc.yml`.
   The version is also saved in `package.json` under the `packageManager` field.

2. **Adding dependencies**: Dependencies are declared in `package.json` and added via `yarn add`.
   The resolved dependency tree is recorded in `yarn.lock`.

3. **Dependency management**: Developers use `yarn install` to install dependencies (either into
   `.yarn/cache` via Plug'n'Play or into `node_modules`), `yarn up` to upgrade, and `yarn remove`
   to remove dependencies.

4. **Build process**: Yarnberry supports [lifecycle scripts](https://v3.yarnpkg.com/advanced/lifecycle-scripts)
   and can compile native addons via node-gyp. The `--mode=skip-build` flag can skip compilation
   during install.

### How the Package Manager Works

#### Features

##### [Plug'n'Play][pnp]

In npm or Yarn 1, installing dependencies means extracting them to the `node_modules/` tree.

Yarnberry, by default, keeps dependencies as zip files in `.yarn/cache` inside your project
directory, and gives node the ability to `require` them directly. This mechanism relies on an
auto-generated [.pnp.cjs](https://github.com/chmeliik/berryscary/blob/everything-everywhere-all-at-once/.pnp.cjs)
file.

Not every project on npm supports PnP natively. Yarnberry maintains its own patches for some of
them, for example
[typescript](https://v3.yarnpkg.com/getting-started/qa#why-is-typescript-patched-even-if-i-dont-use-plugnplay).
When you add typescript as a dependency, Yarnberry creates two entries
[in the lockfile](https://github.com/chmeliik/berryscary/blob/c424d96e1e36542e52985aee716e1b12881c24fb/yarn.lock#L1320-L1338)
and in the cache — for both the unpatched and patched versions.

##### [Offline Cache](https://v3.yarnpkg.com/features/offline-cache)

Yarnberry is [designed to work offline by default](https://v3.yarnpkg.com/features/offline-cache).
There is a local cache (`.yarn/cache`) and a global mirror (`~/.local/share/yarn/berry/cache`).

If `.yarn/cache` is checked into the repo, Hermeto doesn't even have to do anything. If it's not,
Hermeto can populate the *global* cache (not the local one,
[here's why][global-cache-commit]).
The container build can mount the cache, set `YARN_GLOBAL_FOLDER` to the right path and offline
installs work.

##### [Zero-Installs][zero-installs]

A combination of the two features above. If you check in both `.yarn/cache` and `.pnp.cjs`, then
the application is *already installed* as soon as you clone the repo. You don't even need to
`yarn install`, it just works. According to the Yarnberry docs, checking in `.yarn/cache` is a good
idea (while checking in `node_modules` is very much the opposite).

Users of zero-installs should have an easy time making hermetic builds work. They most likely won't
even bother with Hermeto (so SBOM generation will be at Syft's mercy). That said, we will still
need some support for those who do decide to use zero-installs with Hermeto (see the
[security implications](https://v3.yarnpkg.com/features/zero-installs#does-it-have-security-implications)).

##### [Plugins][plugins]

Yarnberry distinguishes between three types of plugins:

- the builtin plugins that make up the base `yarn` command
- [official plugins](https://v3.yarnpkg.com/features/plugins#official-plugins)
- [contrib plugins](https://v3.yarnpkg.com/features/plugins#contrib-plugins)

You can add any plugin with `yarn plugin import …`, which will add the plugin
[directly into the repo](https://github.com/chmeliik/berryscary/tree/main/.yarn/plugins/%40yarnpkg)
and configure it
[in .yarnrc.yml](https://github.com/chmeliik/berryscary/blob/3cad13a72a9367c806d3c8d7ee8c6107528ee184/.yarnrc.yml#L1-L5).

When it comes to the contrib plugins, the Yarnberry documentation highlights:
*No guarantees are made as to plugin quality, compatibility, or lack of malicious code. As with all
third-party dependencies, you should review them yourself before including them in your project.*

It's not just the contrib plugins, either. The official
[exec](https://github.com/yarnpkg/berry/tree/master/packages/plugin-exec) plugin allows you to run
arbitrary code to generate a package. Even "safe" plugins are a challenge: they can add new
resolvers and fetchers, which can store their own locator formats in the lockfile (such as the
[exec format](https://github.com/chmeliik/berryscary/blob/3cad13a72a9367c806d3c8d7ee8c6107528ee184/yarn.lock#L120)).
If Hermeto doesn't understand the locator format, it won't be able to produce an accurate SBOM.

To summarize, from Hermeto's point of view, plugins are:

- the gateway to malicious code execution in the prefetch-dependencies task
- the reason why the set of possible [protocols][protocols] is infinite

##### Workspaces

Pretty much the same workspaces you already know from npm and Yarn 1. Could be worth noting that
workspaces can depend on each other (and a "child" workspace can depend on the "parent" workspace,
assuming that doesn't create a cycle).

#### Registry/Repository Model

Yarnberry uses the npm registry by default. Registry configuration is controlled via the
[npmRegistryServer][v3-npmRegistryServer] and
[npmScopes][v3-npmScopes] options in `.yarnrc.yml`.

#### Package Identity and Versioning ([Protocols][protocols])

The set of protocols supported by the base Yarnberry + the exec plugin is documented at
[https://v3.yarnpkg.com/features/protocols][protocols]. The table
is not quite complete — it is missing the supported `https:` protocol and possibly others. Plugins
can add their own protocols.

*Implementation note: plugins can "add" protocols by implementing the
[Resolver](https://github.com/yarnpkg/berry/blob/8d70543e4ec7bb67d94ccaf9fa931c40a1acaeda/packages/yarnpkg-core/sources/Resolver.ts#L16)
interface and indicating whether they support the Descriptor (the unresolved thing in package.json)
and Locator (the resolved thing in yarn.lock). Here's
[how the exec plugin does it](https://github.com/yarnpkg/berry/blob/8d70543e4ec7bb67d94ccaf9fa931c40a1acaeda/packages/plugin-exec/sources/ExecResolver.ts#L13-L25).
Resolvers are also in charge of turning Descriptors into Locators (they decide how things look in
yarn.lock). They do this
[in the getCandidates function](https://github.com/yarnpkg/berry/blob/8d70543e4ec7bb67d94ccaf9fa931c40a1acaeda/packages/plugin-exec/sources/ExecResolver.ts#L60).
Whoever ends up implementing purl generation for Yarnberry will be reading a lot of these.*

Notable protocols:

**Git, GitHub**: Mostly the same as npm, but there are no `gitlab:` or `bitbucket:` shorthands.
Differences include:
- lockfile storage format differs from npm's `git+ssh://<url>#<commit>` — Yarnberry does
  [something](https://github.com/chmeliik/berryscary/blob/c424d96e1e36542e52985aee716e1b12881c24fb/yarn.lock#L248)
  quite [different](https://github.com/chmeliik/berryscary/blob/c424d96e1e36542e52985aee716e1b12881c24fb/yarn.lock#L275)
- the name doesn't have to match the name declared in the git dependency's `package.json`
  (Yarnberry supports aliases for every dependency type, while npm only supports them for registry
  dependencies)
- Yarnberry respects the protocol you specify, whereas npm always tries https first before falling
  back to ssh

**File, Link, Portal**: Three different types of file dependencies:
- `file:<archive>` or `file:<folder>` creates a zipped package in `.yarn/cache`
- `portal:<folder>` does not create a cache entry, your app depends directly on the folder
- `link:<folder>` is like `portal:`, but if the linked package has any dependencies, Yarnberry
  ignores them

Yarnberry reports paths (file, link, portal and others) as relative to a parent locator
([example](https://github.com/chmeliik/berryscary/blob/c424d96e1e36542e52985aee716e1b12881c24fb/yarn.lock#L1233)).
All three are the same as npm's `file:` dependencies for SBOM purposes.

**Workspace**: Workspaces are explicit (compared to workspaces reported as `file:` in npm or not
reported at all in Yarn 1). For SBOM purposes, they're still the same as `file:` deps.

**Patch**: You can patch any type of dependency on the fly via the
[patch:](https://v3.yarnpkg.com/features/protocols#patch) protocol. Yarnberry will patch some
dependencies automatically (e.g. typescript as explained in [Plug'n'Play][pnp]). For any
patched dependency, Yarnberry creates two entries in both `yarn.lock` and `.yarn/cache`:
- [typescript built-in patch](https://github.com/chmeliik/berryscary/commit/7d1727907e28759c9324f33289e841f2fe05e192)
- [left-pad custom patch](https://github.com/chmeliik/berryscary/commit/cf1af13718236ee06635928a153662ed94b29490)
- [github dependency patch](https://github.com/chmeliik/berryscary/commit/c424d96e1e36542e52985aee716e1b12881c24fb)

**[Exec][protocols]**: The exec plugin allows running arbitrary code to generate a package. Either we ban
exec altogether, or we accept arbitrary code execution and figure out SBOM reporting. Banning exec
means using `yarn info` to detect and fail if there's anything with an `exec` dependencies.

#### Configuration Options

See [.yarnrc.yml][yarnrc-ref] for the full reference.

## Design

### Dependency List Generation

#### Dependency List Toolchain

The core tool is `yarn info -AR --json --cache`. This command returns info based on the data in the
lockfile (if the lockfile is missing or broken, the command fails).

#### Dependency List Format

The output format is roughly as follows:

```json
{
  "value": "<the `resolution` locator from yarn.lock>",
  "children": {
    "Version": "<version from yarn.lock>",
    "Cache": {
      "Checksum": "<cacheKey from yarn.lock>/<sha512 checksum>",
      "Path": "<path to zip archive in local or global cache>",
      "Size": "<size in bytes of the zip archive>"
    }
  }
}
```

Whether the reported Path is the local or global one depends on the
[enableGlobalCache](https://v3.yarnpkg.com/configuration/yarnrc#enableGlobalCache) setting.
The command works even if the cache is empty — it reports the path where `yarn install` would place
the dependency.

Example (with `enableGlobalCache: true` and `globalFolder: /tmp/berryscary`):

```json
{
  "value": "ccto-wo-deps@git@github.com:cachito-testing/cachito-npm-without-deps.git#commit=2f0ce1d7b1f8b35572d919428b965285a69583f6",
  "children": {
    "Version": "1.0.0",
    "Cache": {
      "Checksum": "8/3ed9ea417c75a1999925159e67cf04bf2d522967692a55321559ef2b353fa690167b7bc40e989e4ee35e36d095f007f2d0c53faeb55f14d07ec3ece34faba206",
      "Path": "/tmp/berryscary/cache/ccto-wo-deps-git@github.com-e0fce8c89c-8.zip",
      "Size": 638
    }
  }
}
```

**Note**: For non-registry dependencies, the output does not state the actual name of the dependency
(based on `package.json`) anywhere. We'll have to get it from the `package.json` in the zip archive
in the cache. For registry dependencies, the name seems to be accurate (even for aliased registry
dependencies).

#### Checksum Generation

Checksums are provided natively via the `yarn info --cache` output (SHA-512). The
[checksumBehavior][checksumBehavior] option should be set
to `"throw"` to ensure strict checksum validation.

#### Purl Generation

Most of the supported [protocols][protocols] map to similar npm equivalents; their purls should be
the same as the npm ones.

To parse the reported locators, we'll need to know how Yarnberry plugins parse and generate them.
See the implementation note in [Protocols][protocols].

##### The patch: Protocol

Patches should be reported via
[pedigree.patches][cyclonedx-pedigree] in the
SBOM:

```json
"pedigree": {
  "patches": [
    {
      "diff": {
        "url": "git+https://github.com/hermetoproject/integration-tests.git@76b311b7c4594bee833401a1618d3b706ec8c639#.yarn/patches/ccto-wo-deps-git@github.com-e0fce8c89c.patch"
      },
      "type": "unofficial"
    }
  ]
}
```

The path to the patch file can be parsed from the
[locator](https://github.com/chmeliik/berryscary/blob/c424d96e1e36542e52985aee716e1b12881c24fb/yarn.lock#L740).
We may want to set a reasonably large upper limit for the size of the patch file.

##### Alternative Registries

See the [npmRegistryServer][v3-npmRegistryServer] and
[npmScopes][v3-npmScopes] options. When these are present,
add the `repository_url`
[qualifier](https://github.com/package-url/purl-spec/blob/master/PURL-SPECIFICATION.rst#known-qualifiers-keyvalue-pairs)
to the purls for registry dependencies.

### Fetching Content

#### Native vs. Hermeto Fetch

**Q:** Is the offline cache a simpler and more reliable solution than modifying the lockfile?

**A:** Yes.

**Q:** Can we feasibly populate the offline cache without relying on Yarnberry itself?

**A:** No.

*Reminder: why didn't we go with the cache-based approach for npm? Because the
[npm docs](https://docs.npmjs.com/cli/v9/commands/npm-cache#a-note-about-the-caches-design)
explicitly state that the cache is not meant to be a "persistent and reliable data store". And
because, based on our investigation, it doesn't work for git dependencies from hosts other than
GitHub.*

Hermeto will rely on Yarnberry itself to populate the offline cache during prefetch.

#### Prefetch Implementation

Once you've [installed Yarnberry][yarn-install-guide],
[dealt with plugins](#dealing-with-plugins) and the
[user configuration](#dealing-with-user-configuration), prefetching is simple.

We support two separate workflows: [zero-installs][zero-installs] and regular installs. First,
check if the configured `cacheFolder` (default `.yarn/cache`) exists and contains at least one zip
file. If yes, assume that the user intended to use zero-installs. If not, assume a regular workflow.

**For a regular workflow:**

1. Set `$YARN_GLOBAL_FOLDER` to the directory where you want to prefetch dependencies
   (i.e. `{hermeto_output}/deps/yarn`)
2. `yarn install --mode=skip-build`
   - `skip-build` makes sure that Yarnberry won't try to compile any node-gyp C(++) libraries and
     will instead leave that to the build
   - `skip-build` also ensures that the user's preinstall, install and postinstall lifecycle scripts
     will not run (again, leaving them for the build)
3. Set `$YARN_GLOBAL_FOLDER` for the build

**For a zero-installs workflow:**

1. `yarn install --mode=skip-build --immutable-cache --check-cache`
   - Note that immutable cache can also be enabled
     [via configuration](#override-for-prefetch)
   - The cache mutation error may not be entirely user friendly; consider implementing a git-based
     check instead
   - `--immutable-cache` also fails the build if any archives were to be *deleted* because they're
     no longer required (that's a good thing, otherwise the user would have access to dependencies
     not present in the lockfile and therefore not reported in the SBOM)

*Should we let the user disable `--check-cache`? We should not (at least initially), for the
reasons (the dependencies in the checked-in cache could be entirely different from what we report in the
SBOM). If we were to ever introduce an option to disable the check, we would have to report some
warnings checkable by the Enterprise Contract.*

##### Arbitrary Code Execution During Prefetch

The `yarn install` command will execute the lifecycle scripts (prepack, postpack etc.) of any
git/github dependency that happens to have them. And yes, it's only git/github, no other type —
see the references to the
[prepareExternalProject][prepareExternalProject]
method.

Cachito deals with this by
[banning git dependencies](https://github.com/containerbuildsystem/cachito/blob/6d7f809b0ab3ed34b426263d95bf0ae10213b436/cachito/workers/pkg_managers/general_js.py#L738-L741)
that have
[prepack or prepare scripts](https://github.com/containerbuildsystem/cachito/blob/6d7f809b0ab3ed34b426263d95bf0ae10213b436/cachito/workers/pkg_managers/general_js.py#L534-L535).
We can do the same, but we would have to ban every script relevant to the Yarn 1, Yarnberry and npm
`install` and `pack` commands (see
[prepareExternalProject][prepareExternalProject]).
And pnpm as well for good measure (in case Yarnberry adds support for it).

#### Project Structure

```
${output_dir}/deps/yarn
├── cache
│    ├── ccto-wo-deps-git@github.com-e0fce8c89c-8.zip
│    └── ccto-wo-deps-patch-c3567b709f-8.zip
└── github.com
      └── cachito-testing
            └── cachito-npm-without-deps
                  ├── ccto-wo-deps-git@github.com-e0fce8c89c-8.zip -> ../../../cache/ccto-wo-deps-git@github.com-e0fce8c89c-8.zip
                  └── ccto-wo-deps-patch-c3567b709f-8.zip -> ../../../cache/ccto-wo-deps-patch-c3567b709f-8.zip
```

The symlink structure is intended to give `deps/yarn` a familiar structure consistent with
what Cachito does for `deps/pip` and `deps/npm`. Source container denylists and their application are partially based around the Cachito structure.

If RHTAP wants to use the same denylists, we should try to preserve the structure. The real zip
files must stay in the `cache/` folder, but we can add symlinks from the expected locations. The
one in charge of applying the denylist would have to delete not only the symlinks, but also their
targets.

This is not relevant until RHTAP starts thinking about source container denylists and should be
implementable when necessary.

### Build Environment Config

#### [Installing Yarnberry][yarn-install-guide]

As we’ve thoroughly established earlier, Hermeto will depend on Yarnberry to populate the offline cache. How can we install Yarnberry in the Hermeto container? Even better, how can we make sure we always have exactly the same version as the user? (And is that actually a good idea?)

When one runs `yarn init -2`, two things happen:
1. Yarnberry
   [commits itself into the repository](https://github.com/chmeliik/berryscary/tree/main/.yarn/releases)
   and configures itself
   [in .yarnrc.yml](https://github.com/chmeliik/berryscary/blob/3cad13a72a9367c806d3c8d7ee8c6107528ee184/.yarnrc.yml#L7)
2. And saves its version
   [in package.json](https://github.com/chmeliik/berryscary/blob/3cad13a72a9367c806d3c8d7ee8c6107528ee184/package.json#L3)

If you have any version of Yarn (yes, even the latest v1) and the repo has `yarnPath` set, then any
`yarn` command executed in the repo will automatically use the locally stored yarn executable.

If the repo does not set `yarnPath` but does have `packageManager` in `package.json`, then
[Corepack][corepack] comes into play. Corepack comes
with a `yarn` "shim" which automatically downloads the right version of Yarn and uses it.

```
$ cat /usr/local/bin/yarn
#!/usr/bin/env node
require('./lib/corepack.cjs').runMain(['yarn', ...process.argv.slice(2)]);
```

When you have this shim installed and invoke `yarn` in a project that sets packageManager in package.json, Corepack will automatically download the right version of Yarn[berry] and use it to execute your command.

Failing all the above (no `yarnPath`, no `packageManager` or no Corepack), the globally installed
`yarn` will be used (if there is one).

**So, what does this mean for us?**

We need to make some decisions.

1. Should we trust the Yarnberry binary committed into the repo? This goes back to the same issue as plugins and the exec protocol.
2. Can we trust Corepack to download the real Yarnberry?
    - Corepack does validate that the packageManager field is <supported_pkg_manager>@<semver>. Hopefully that’s good enough.
    - Corepack allows the user to specify the expected checksum. That protects the user (so we should recommend it), but does not protect us from a malicious user.
    - And even if the answer is yes, is it OK to do the prefetch using a dynamically fetched version of Yarnberry? For what it’s worth, it shouldn’t hurt reproducibility (quite the opposite, given that the version is pinned in package.json).

For now, I will assume a **1-No, 2-Yes situation**:

1. Install Corepack in the Hermeto container, enable the `yarn` shim
2. Before processing a Yarnberry project, validate that it sets packageManager
    - If the project doesn’t set packageManager but does set yarnPath, we can try to parse the version from the filename in yarnPath (and use Corepack to get that version).
    - If the project sets both and yarnPath includes a version, validate that the versions match
3. When running any `yarn` commands, make sure to set the ignorePath option (or the YARN_IGNORE_PATH variable) to use the global `yarn` rather than the local one
4. And that’s it.

**And what does it mean for the user?**

We absolutely should not discourage the user from checking in the .yarn/releases/yarn-{version}.cjs binary. In fact, for the vast majority of users, this will be the way to make some version of Yarnberry available to the hermetic build (AFAIK Red Hat still doesn’t provide Yarn[berry] in any official base image).

What we should do - assuming that we’re worried about malicious users - is ignore that binary and use the one downloaded by Corepack instead. We should also verify that the version in packageManager matches the version in the binary filename (to prevent accidental mismatches).

The user, if they cannot get any `yarn` executable through other means, can replace calls to `yarn` with `node .yarn/releases/yarn-{version}.cjs`. Or create an equivalent shell script in $PATH/yarn.

#### Dealing with Plugins

The [Plugins][plugins] section describes the two issues that plugins introduce: arbitrary code
execution and an infinite set of possible protocols.

The solution to the latter is fairly simple, though not entirely satisfying. We need to be able to
generate the SBOM. To do that, we must understand every locator in the lockfile. If we encounter an
unknown format, fail the build.

Dealing with the arbitrary code execution will be trickier. Three approaches are considered:

**Option 1: Ignore all plugins** — Before running `yarn install` during prefetch, set plugins in
`.yarnrc.yml` to an empty array. Restore the original content afterwards.

- Pros: Definitely safe
- Cons: Disabling plugins may affect the prefetched content (but if all the protocols in
  `yarn.lock` are known, the chance should be small — plugins can *add* resolvers and fetchers but
  not necessarily *modify* existing ones)

**Option 2: Allowlist of plugins to ignore** — Maintain an allowlist of plugins by
[spec](https://github.com/chmeliik/berryscary/blob/c424d96e1e36542e52985aee716e1b12881c24fb/.yarnrc.yml#L3).
Verify the user-configured set of plugins is a subset of the allowlist. Then ignore all plugins as
in Option 1.

- Pros: Definitely safe; probably does not affect the prefetched content
- Cons: Requires maintaining an allowlist; plugins are unsupported by default until added to the
  allowlist

**Option 3: Allowlist of plugins to execute** — Maintain an allowlist of plugins by spec + set of
known-safe checksums. Check the user-configured set. If there is a checksum mismatch, fail the
build. If there are unknown plugins, either disable them or fail the build.

- Pros: Probably safe; the allowlist can be more extensive (a plugin that affects the prefetched
  content can be safe to execute)
- Cons: Requires tediously maintaining a very precise allowlist and personally verifying the safety
  of each addition

**The approach taken: Have a default list and reject everything else** - The solution approach that was taken is a mixture of 
both option 1 and 3. There is a list of only official plugins that add new protocols and that also do not implement the 
`fetchPackageInfo` hook, since it would allow arbitrary code execution. Everything else not in the list will cause a failure.
Note that starting from v4, the official plugins are enabled by default and can't be disabled. Since they're not present in 
the [.yarnrc.yml][yarnrc-ref] file anymore, this function has no effect on v4 projects.

#### Dealing with User Configuration

See [.yarnrc.yml][yarnrc-ref].

##### Override for Prefetch

Options useful for implementing Hermeto's functionality or required for security:

| Option | Value | Purpose |
|--------|-------|---------|
| [`checksumBehavior`][checksumBehavior] | `"throw"` | Strict checksum validation |
| [`enableImmutableCache`](https://yarnpkg.com/configuration/yarnrc#enableImmutableCache) | `true` | If the user is using zero-installs |
| [`enableImmutableInstalls`](https://yarnpkg.com/configuration/yarnrc#enableImmutableInstalls) | `true` | Fail if yarn.lock needs an update |
| [`globalFolder`][globalFolder] | `<output_dir>` | Where to download dependencies |
| [`pnpMode`](https://yarnpkg.com/configuration/yarnrc#pnpMode) | `strict` | Modules won't be allowed to require packages they didn't list  |
| [`enableMirror`][enableMirror] | `false` | Define whether to mirror local cache entries into the global cache or not |
| [`enableGlobalCache`](https://yarnpkg.com/configuration/yarnrc#enableGlobalCache) | `true` | Define whether the cache should be shared between all local projects |
| [`enableConstraintsChecks`](https://yarnpkg.com/configuration/yarnrc#enableConstraintsChecks) | `false` | Define whether constraints should run on every install |

Optional:

| Option | Value | Purpose |
|--------|-------|---------|
| [`enableScripts`](https://yarnpkg.com/configuration/yarnrc#enableScripts) | `false` | Extra safety (also achieved by --mode=skip-build) |
| [`enableStrictSsl`](https://yarnpkg.com/configuration/yarnrc#enableStrictSsl) | `true` | Enforce strict SSL |
| [`enableTelemetry`](https://yarnpkg.com/configuration/yarnrc#enableTelemetry) | `false` | Disable telemetry |
| [`ignorePath`](https://yarnpkg.com/configuration/yarnrc#ignorePath) | `true` | Use global `yarn` instead of local |
| [`unsafeHttpWhitelist`](https://yarnpkg.com/configuration/yarnrc#unsafeHttpWhitelist) | `[]` | Disallow HTTP |

##### Respect for Prefetch

| Option | Purpose |
|--------|---------|
| [`cacheFolder`][cacheFolder] | To find out if the user is using zero-installs |
| [`lockfileFilename`](https://yarnpkg.com/configuration/yarnrc#lockfileFilename) | Parse the lockfile specified here (default yarn.lock); probably not needed if we base SBOM generation on `yarn info` output instead |
| [`npmRegistryServer`](https://yarnpkg.com/configuration/yarnrc#npmRegistryServer) and [`npmScopes`](https://yarnpkg.com/configuration/yarnrc#npmScopes) | The user can configure multiple different registries; if we don't respect them we cause Dependency Confusion. `yarn install` will respect them automatically |
| [`yarnPath`](https://yarnpkg.com/configuration/yarnrc#yarnPath) | Depending on how we [handle Yarnberry installs][yarn-install-guide] |

#### Environment Variables

**NOTE:** These are build-time environment variables that are set during prefetch, not during build.

| Variable Name | Purpose | Example Value | Required |
|---------------|---------|---------------|----------|
| `YARN_GLOBAL_FOLDER` | Points to dependency cache for non-zero-installs | `{hermeto_output}/deps/yarn` | Yes (non-zero-installs) |
| `YARN_IGNORE_PATH` | Use global `yarn` instead of local binary | `true` | Yes |

#### Configuration Files

##### Override for Build

| Option | Value | Purpose |
|--------|-------|---------|
| [`globalFolder`][globalFolder] | `<output_dir>` | Make offline builds work (if no zero-installs) |
| [`enableMirror`][enableMirror] | `true` | false would break globalFolder |
| [`enableGlobalCache`][enableGlobalCache] | `false` | true would cause the same issue as [mounting the local cache][global-cache-commit] |
| [`enableImmutableCache`](https://yarnpkg.com/configuration/yarnrc#enableImmutableCache) | `false` | Define whether to allow adding/removing entries from the lockfile or not |

Optional:

| Option | Value | Purpose |
|--------|-------|---------|
| [`enableInlineBuilds`](https://yarnpkg.com/configuration/yarnrc#enableInlineBuilds) | `true` | Otherwise node-gyp compilation errors go to a log file, which is useless in CI |

#### Build Process Integration

**Summary: Resolving a single Yarnberry project**

1. Make sure we will use the right version of Yarnberry to process the project
2. Check if the project uses zero-installs
3. Prepare the configuration options relevant for prefetch
4. Disable plugins
5. Run `yarn info …` to get the necessary data
6. Validate that we can parse every locator in the output
7. Protect against arbitrary code execution by git dependencies
8. Run `yarn install …` to fetch the dependencies (or check the existing cache, if zero-installs)
9. Generate the SBOM based on the data from `yarn info`, the zip files of the dependencies and the
    `.yarnrc.yml` configuration (also report missing checksums based on the data from `yarn info`)
10. Set environment variables for the build

## Implementation Notes

### Summary of Arbitrary Code Execution in Yarnberry

These are the mechanisms that a user — or their dependencies — could use to execute arbitrary code
during the prefetch task. Arbitrary code execution in the prefetch task is a problem, because it
hurts the trustworthiness of the generated SBOM. Arbitrary code can fetch anything it wants from
wherever it wants, Hermeto wouldn't know about it. We should prevent arbitrary code execution.

**Controlled by the user:**

- ~~preinstall, install and postinstall lifecycle scripts in package.json~~ — solved by
  `--mode=skip-build` (see [Prefetch Implementation](#prefetch-implementation))
- The checked-in `.yarn/releases/yarn-{version}.cjs` binary — solved by ignoring said binary and
  depending on Corepack (see [Installing Yarnberry][yarn-install-guide])
- The checked-in `.yarn/plugins/*.cjs` binaries — solved by ignoring plugins while prefetching
  (see [Plugins][plugins], [Dealing with Plugins](#dealing-with-plugins))
- The scripts used to generate `exec:` dependencies — ban the `exec:` protocol
  (see [Exec][protocols])

**Controlled by their dependencies:**

- Any lifecycle script of any git dependency, if that lifecycle script is relevant to `yarn install`,
  `yarn pack`, `npm install` or `npm pack` for any version of Yarn 1, Yarnberry or npm — ban git
  dependencies that use such scripts
  (see [Arbitrary Code Execution During Prefetch](#arbitrary-code-execution-during-prefetch))
- ~~The postinstall script of any dependency~~ — solved by `--mode=skip-build`
  (see [Prefetch Implementation](#prefetch-implementation))

### Yarn@4.x Notes

This design was originally written for Yarn@3.x. Relevant changes in
[Yarn@4.x](https://yarnpkg.com/advanced/changelog):

- Official plugins are now always enabled, notably including the exec plugin — we will parse
  locators and ban exec before running yarn install
- There's a hardened mode: [https://yarnpkg.com/features/security#hardened-mode](https://yarnpkg.com/features/security#hardened-mode)
- The `pnpDataPath` config option was removed; the path is hardcoded now
- Yarnberry now caches npm version metadata — the `globalFolder` now also includes `./metadata`;
  we can probably keep only the `./cache` part
- `enableGlobalCache` is now true by default — we were going to
  [override it to false](#override-for-build) for the build already; this changes the default
  behavior, which should be called out in documentation
- Some configuration options now accept new values (e.g. "reset" for
  [checksumBehavior][checksumBehavior]) but we still want
  to handle configuration the same way
- Lots of miscellaneous changes in `.yarn/cache` and the lockfile:
  [berryscary@8d71311](https://github.com/chmeliik/berryscary/commit/8d71311bcea58238e2ecdbca50e00af3a8155c55)
  — mostly OK since we'll process with the right Yarnberry version via Corepack, but some locator
  formats changed (e.g. builtin `patch:` and `file:` tarball)

## References

- **Official documentation**: [Yarnberry docs (v3)](https://v3.yarnpkg.com/) /
  [Yarnberry docs (v4)](https://yarnpkg.com/)
- **Architecture**: [Yarnberry architecture overview][architecture]
- **Protocols**: [Yarnberry protocols][protocols]
- **Configuration**: [.yarnrc.yml reference][yarnrc-ref]
- **Corepack**: [Node.js Corepack docs][corepack]
- **CycloneDX pedigree patches**: [CycloneDX 1.4 spec][cyclonedx-pedigree]
- **Test repo**: [berryscary](https://github.com/chmeliik/berryscary/)

<!-- Link definitions (URLs used in multiple places) -->

[architecture]: https://v3.yarnpkg.com/advanced/architecture
[cacheFolder]: https://yarnpkg.com/configuration/yarnrc#cacheFolder
[checksumBehavior]: https://yarnpkg.com/configuration/yarnrc#checksumBehavior
[corepack]: https://nodejs.org/dist/latest/docs/api/corepack.html
[cyclonedx-pedigree]: https://cyclonedx.org/docs/1.4/json/#components_items_pedigree_patches
[enableGlobalCache]: https://yarnpkg.com/configuration/yarnrc#enableGlobalCache
[enableMirror]: https://yarnpkg.com/configuration/yarnrc#enableMirror
[global-cache-commit]: https://github.com/chmeliik/berryscary/commit/1275d17d761090fa6999000eb1991ad4e074eacc
[globalFolder]: https://yarnpkg.com/configuration/yarnrc#globalFolder
[prepareExternalProject]: https://github.com/yarnpkg/berry/blob/80f238822227246f0f2fb818ef564937dc17b313/packages/yarnpkg-core/sources/scriptUtils.ts#L221
[protocols]: https://v3.yarnpkg.com/features/protocols
[v3-npmRegistryServer]: https://v3.yarnpkg.com/configuration/yarnrc#npmRegistryServer
[v3-npmScopes]: https://v3.yarnpkg.com/configuration/yarnrc#npmScopes
[plugins]: https://v3.yarnpkg.com/features/plugins
[pnp]: https://v3.yarnpkg.com/features/pnp
[yarn-install-guide]: https://v3.yarnpkg.com/getting-started/install
[yarnrc-ref]: https://v3.yarnpkg.com/configuration/yarnrc
[zero-installs]: https://v3.yarnpkg.com/features/zero-installs
