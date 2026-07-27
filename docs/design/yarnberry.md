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

If `.yarn/cache` is checked into the repo (PnP mode), or if `node_modules` exists when `nodeLinker`
is set to `node-modules` or `pnpm`, the project is using [zero-installs][zero-installs] and does
not need Hermeto — Yarn can resolve dependencies offline on its own. Hermeto rejects such projects
(see [Zero-Installs](#zero-installs)). Otherwise,
Hermeto populates the *global* cache (not the local one,
[here's why][global-cache-commit]).
The container build can mount the cache, set `YARN_GLOBAL_FOLDER` to the right path and offline
installs work.

##### [Zero-Installs][zero-installs]

The concept of [zero-installs][zero-installs], i.e. no install needed (git clone is
sufficient), is inherently flawed for a number of reasons:
  - taking over maintenance (by the means of manual updates) of a
      project's dependencies by baking their sources in to the given
      project's repository
  - creating unnecessary bloat (often in form of binary formats) in
      the repository
  - moving the trust in package contents from the official packaging
      tooling and official public registries to a given project which
      doesn't really solve the biggest security problem of many public
      packaging repositories - unvetted contents

Currently Hermeto will reject [zero-installs][zero-installs] with a `PackageRejected` error. Use this [commit](https://github.com/hermetoproject/hermeto/commit/0a913377ba692ba2e6620bbd8d2b55c16b5b7678) as reference.

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

###### Workspace Focus (Yarn v4+)

Yarn v4 supports [`yarn workspaces focus`](https://yarnpkg.com/cli/workspaces/focus), which installs
only the dependencies of specific workspaces (and their transitive workspace dependencies) rather
than the entire project. Hermeto supports this via the `workspaces` field in the request input.

When workspace focus is used:

- **Dependency resolution** runs `yarn workspace <name> info --recursive --cache --json` per
  workspace instead of `yarn info --all`, deduplicating overlapping dependencies across workspaces.
- **Fetching** uses `yarn workspaces focus <name> [<name>...]` instead of
  `yarn install --mode skip-build`.
- **Lifecycle scripts**: Because `yarn workspaces focus` does not support `--mode skip-build` and
  `enableScripts: false` does not apply to workspace scripts. Hermeto strips the `scripts`
  field from each workspace's `package.json` before running focus
  ([_strip_workspace_scripts()](https://github.com/hermetoproject/hermeto/blob/1ee314c4eb34f1a6a104719feb39a0df0ce4dd56/hermeto/core/package_managers/javascript/yarn/main.py)).
- Workspace focus requires Yarn v4 or later; requesting it on an older version raises
  `PackageRejected`.

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

**Git, GitHub**: git dependencies are not supported by Hermeto and it will raise a `UnsupportedFeature` error.
But here is some useful information on how it works with yarnberry:
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

**[Exec][protocols]**: The exec plugin allows running arbitrary code to generate a package. Hermeto
bans exec dependencies: it runs `yarn info` to obtain dependency data, parses the locators from the
output, and rejects any locator with the `exec` protocol, raising an `UnsupportedFeature` error.

#### Configuration Options

See [.yarnrc.yml][yarnrc-ref] for the full reference.

## Design

### Dependency List Generation

#### Dependency List Toolchain

The core tool is `yarn info -AR --json --cache`. This command returns info based on the data in the
lockfile (if the lockfile is missing or broken, the command fails).

When [workspace focus](#workspace-focus-yarn-v4) is active, Hermeto instead runs
`yarn workspace <name> info --recursive --cache --json` for each requested workspace and
deduplicates the results by raw locator.

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

Example (from "https://github.com/hermetoproject/hermeto/blob/4fc76df98d71df67c740bb1c85029d39124626fb/tests/unit/package_managers/javascript/yarn/test_resolver.py#L52"):

```json
{
  "value": "@isaacs/cliui@npm:8.0.2",
  "children": {
      "Version": "8.0.2",
      "Cache": {
          "Checksum": "8/4a473b9b32a7d4d3cfb7a614226e555091ff0c5a29a1734c28c72a182c2f6699b26fc6b5c2131dfd841e86b185aea714c72201d7c98c2fba5f17709333a67aeb",
          "Path": "{repo_dir}/.yarn/cache/@isaacs-cliui-npm-8.0.2-f4364666d5-4a473b9b32.zip",
          "Size": 10582,
      }
  }
}
```

**Note**: For non-registry dependencies, the output does not state the actual name of the dependency
(based on `package.json`) anywhere. We'll have to get it from the `package.json` in the zip archive
in the cache. For registry dependencies, the name seems to be accurate (even for aliased registry
dependencies).

#### Development Dependencies

`yarn info` does not distinguish production from development dependencies, so every
locator appears as a regular package.

Other JavaScript backends (npm, pnpm, Yarn classic) already report development
dependencies via the CycloneDX `cdx:npm:package:development`
[property](https://github.com/CycloneDX/cyclonedx-property-taxonomy/blob/main/cdx/npm.md).

**Approach:** after the full-tree prefetch, temporarily strip `devDependencies` from every
`package.json`, run `yarn install --mode=update-lockfile` plus a second `yarn info`, and treat
locators in the full set but not the production set as development (including transitive ones).
Mark those packages with `cdx:npm:package:development` in the SBOM.

`--mode=update-lockfile` is enough for the second pass: it skips linking, does not re-download
existing packages, and disables immutable installs
([yarnpkg/berry#3933](https://github.com/yarnpkg/berry/pull/3933)). The lockfile must be restored
afterwards — re-running `update-lockfile` after putting `devDependencies` back would
let Yarn bump them. Patches are not components themselves, so fewer components may carry the
development property than there are development locators.

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

Hermeto resolves patch file URLs from the locator and reports them via `PatchDiff` URL references
in the SBOM pedigree.


##### Permissive Mode and Non-Git Sources

For workspace, file, link, and portal locators, purl generation requires VCS qualifiers (e.g.
`vcs_url`). When the project is not inside a git repository, this raises `NotAGitRepo`. In
**permissive mode**, Hermeto silently skips the VCS qualifiers instead of failing. Patch locators
always require git repository context regardless of mode and will raise `PackageRejected` if the
project is not a git repo.

##### Proxy External References

When a proxy URL is configured (see [Proxy Support](#proxy-support)), components with PURLs of type npm
include an `externalReference` in the SBOM with the proxy URL, annotated with the standard proxy
reference type and comment.

##### Alternative Registries

See the [npmRegistryServer][v3-npmRegistryServer] and
[npmScopes][v3-npmScopes] options. When these are present, the `repository_url`
[qualifier](https://github.com/package-url/purl-spec/blob/master/PURL-SPECIFICATION.rst#known-qualifiers-keyvalue-pairs)
should be added to the purls for registry dependencies.

> **Note:** The `repository_url` qualifier is not yet implemented in the Yarn Berry backend.

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

[Zero-installs][zero-installs] workflow will be rejected with a `PackageRejected` error.

**For a regular workflow:**

1. Set `$YARN_GLOBAL_FOLDER` to the directory where you want to prefetch dependencies
   (i.e. `{hermeto_output}/deps/yarn`)
2. `yarn install --mode=skip-build`
   - `skip-build` makes sure that Yarnberry won't try to compile any node-gyp C(++) libraries and
     will instead leave that to the build
   - `skip-build` also ensures that the user's preinstall, install and postinstall lifecycle scripts
     will not run (again, leaving them for the build)
3. Set `$YARN_GLOBAL_FOLDER` for the build

**For a workspace focus workflow (Yarn v4 only):**

1. Set `$YARN_GLOBAL_FOLDER` as in the regular workflow
2. Strip `scripts` from each workspace's `package.json` (see
   [Workspace Focus](#workspace-focus-yarn-v4) for rationale)
3. `yarn workspaces focus <workspace1> [<workspace2> ...]`
4. Undo accidental changes to the user's repo
5. Set `$YARN_GLOBAL_FOLDER` for the build

##### Proxy Support

When Hermeto is configured with a proxy URL for Yarn, the following `.yarnrc.yml` options are set
during prefetch:

| Option | Value | Purpose |
|--------|-------|---------|
| `npmRegistryServer` | proxy URL | Redirect registry traffic through the proxy |
| `npmAlwaysAuth` | `true` | Force authentication on every request (only if credentials are provided) |
| `npmAuthIdent` | `<login>:<password>` | Proxy credentials (only if both login and password are provided) |

If the proxy requires authentication and invalid or no credentials are provided, `yarn install` (or
`yarn workspaces focus`) will fail with an "Invalid authentication" error. Hermeto catches this and
raises a `PackageManagerError` with guidance to verify proxy settings.

##### Arbitrary Code Execution During Prefetch

Git deps are unsupported outright, so arbitrary code execution from git deps isn't a concern. Hermeto 
will raise a `UnsupportedFeature` error when it encounters them.

**Useful info:** Although git dependencies aren't supported, here is some useful information for it
in case it ever gets supported:

The `yarn install` command will execute the lifecycle scripts (prepack, postpack etc.) of any
git/github dependency that happens to have them. And yes, it's only git/github, no other type —
see the references to the
[prepareExternalProject][prepareExternalProject]
method.

Hermeto deals with this by banning git dependencies that have prepack or prepare scripts.
We can do the same, but we would have to ban every script relevant to the Yarn 1, Yarnberry and npm
`install` and `pack` commands (see [prepareExternalProject][prepareExternalProject]).
And pnpm as well for good measure (in case Yarnberry adds support for it).

#### Output Structure

```
${output_dir}/deps/yarn
└── cache
    ├── @isaacs-cliui-npm-8.0.2-f4364666d5-4a473b9b32.zip
    └── ccto-wo-deps-patch-c3567b709f-8.zip
```

Yarnberry doesn't download .tgz tarballs like npm does. Instead, it stores every dependency as a .zip file in the cache
directory (deps/yarn/cache/).

### Build Environment Config

#### [Installing Yarnberry][yarn-install-guide]

As we've thoroughly established earlier, Hermeto will depend on Yarnberry to populate the offline cache. How can we install Yarnberry in the Hermeto container? Even better, how can we make sure we always have exactly the same version as the user? (And is that actually a good idea?)

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

#### Design Considerations

Two key decisions arise from this architecture:

1. **Trusting the committed Yarnberry binary**: The binary in `.yarn/releases/` is untrusted for the same reasons as plugins and the exec protocol.
2. **Trusting Corepack**: Corepack validates that the `packageManager` field follows the `<supported_pkg_manager>@<semver>` format. Users can specify expected checksums for additional protection, though this does not protect against a malicious user. Using a dynamically fetched version does not hurt reproducibility since the version is pinned in `package.json`.

The chosen approach (**do not trust the binary, trust Corepack**):

1. Install Corepack in the Hermeto container, enable the `yarn` shim
2. Before processing a Yarnberry project, validate that it sets `packageManager`
    - If the project doesn’t set `packageManager` but does set `yarnPath`, parse the version from the filename in `yarnPath` (and use Corepack to get that version)
    - If the project sets both and `yarnPath` includes a version, validate that the versions match
3. When running any `yarn` commands, set the `ignorePath` option (or the `YARN_IGNORE_PATH` variable) to use the global `yarn` rather than the local one

#### User Requirements

Users should continue to check in the `.yarn/releases/yarn-{version}.cjs` binary. For most users, this is the way to make a version of Yarnberry available to the hermetic build, as official base images typically do not include Yarn Berry.

Hermeto ignores the committed binary and uses the one downloaded by Corepack instead. The version in `packageManager` must match the version in the binary filename to prevent accidental mismatches.

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

**The approach taken: Have a default list and reject everything else** — A mixture of Option 1 and                                        
Option 3. Hermeto maintains an                                                                                                            
[allowlist](https://github.com/hermetoproject/hermeto/blob/4fc76df98d71df67c740bb1c85029d39124626fb/hermeto/core/package_managers/javascript/yarn/main.py)
of official plugins that add new protocols but do not implement the `fetchPackageInfo` hook (which 
would allow arbitrary code execution). Currently, only the exec plugin is on this list — it is kept 
so that `yarn info` can recognize exec locators, which are then rejected. All other plugins are                                           
silently removed from `.yarnrc.yml` before processing. If a removed plugin's protocol appears in 
the lockfile, the build fails with an unsupported locator error. Note that starting from v4, the 
official plugins are enabled by default and can't be disabled. Since they're not present in the 
[.yarnrc.yml][yarnrc-ref] file anymore, this function has no effect on v4 projects.

#### Dealing with User Configuration

See [.yarnrc.yml][yarnrc-ref].

##### Override for Prefetch

Hermeto overrides the following options during prefetch:

| Option | Value | Purpose |
|--------|-------|---------|
| [`checksumBehavior`][checksumBehavior] | `"throw"` | Strict checksum validation |
| [`enableImmutableInstalls`](https://yarnpkg.com/configuration/yarnrc#enableImmutableInstalls) | `true` | Fail if yarn.lock needs an update |
| [`globalFolder`][globalFolder] | `<output_dir>` | Where to download dependencies |
| [`pnpMode`](https://yarnpkg.com/configuration/yarnrc#pnpMode) | `strict` | Modules won't be allowed to require packages they didn't list  |
| [`enableMirror`][enableMirror] | `false` | Prevent mirroring packages to the local cache; Hermeto uses the global cache exclusively |
| [`enableGlobalCache`](https://yarnpkg.com/configuration/yarnrc#enableGlobalCache) | `true` | Define whether the cache should be shared between all local projects |
| [`enableConstraintsChecks`](https://yarnpkg.com/configuration/yarnrc#enableConstraintsChecks) | `false` | Prevent constraints from running during install (Yarn v4 only) |
| [`enableScripts`](https://yarnpkg.com/configuration/yarnrc#enableScripts) | `false` | Extra safety (also achieved by --mode=skip-build) |
| [`enableStrictSsl`](https://yarnpkg.com/configuration/yarnrc#enableStrictSsl) | `true` | Enforce strict SSL |
| [`enableTelemetry`](https://yarnpkg.com/configuration/yarnrc#enableTelemetry) | `false` | Disable telemetry |
| [`npmRegistryServer`][v4-npmRegistryServer] | proxy URL | Redirect registry traffic through proxy (only when proxy is configured) |
| [`npmAlwaysAuth`](https://yarnpkg.com/configuration/yarnrc#npmAlwaysAuth) | `true` | Force authentication (only when proxy credentials are provided) |
| [`npmAuthIdent`](https://yarnpkg.com/configuration/yarnrc#npmAuthIdent) | `<login>:<password>` | Proxy credentials (only when proxy credentials are provided) |
| [`ignorePath`](https://yarnpkg.com/configuration/yarnrc#ignorePath) | `true` | Use global `yarn` instead of local |
| [`unsafeHttpWhitelist`](https://yarnpkg.com/configuration/yarnrc#unsafeHttpWhitelist) | `[]` | Disallow HTTP |

##### Respect for Prefetch

| Option | Purpose |
|--------|---------|
| [`cacheFolder`][cacheFolder] | To find out and reject if the user is using [zero-installs][zero-installs] |
| [`lockfileFilename`](https://yarnpkg.com/configuration/yarnrc#lockfileFilename) | Parse the lockfile specified here (default yarn.lock); probably not needed if we base SBOM generation on `yarn info` output instead |
| [`npmRegistryServer`][v4-npmRegistryServer] and [`npmScopes`](https://yarnpkg.com/configuration/yarnrc#npmScopes) | The user can configure multiple different registries; if we don't respect them we cause Dependency Confusion. `yarn install` will respect them automatically |
| [`yarnPath`](https://yarnpkg.com/configuration/yarnrc#yarnPath) | Depending on how we [handle Yarnberry installs][yarn-install-guide] |

#### Environment Variables

##### Build-Time Environment Variables

Hermeto generates these environment variables for the user's build process (see
[Override for Build](#override-for-build)):

| Variable Name | Value | Purpose |
|---------------|-------|---------|
| `YARN_ENABLE_GLOBAL_CACHE` | `false` | Use the local project cache during build |
| `YARN_ENABLE_IMMUTABLE_CACHE` | `false` | Allow the build to write to the cache |
| `YARN_ENABLE_MIRROR` | `true` | Enable mirroring so builds can read from the global cache populated during prefetch |
| `YARN_GLOBAL_FOLDER` | `${output_dir}/deps/yarn` | Point to the prefetched dependency cache |

##### Prefetch-Time Environment Variables

These variables are set internally by Hermeto during the prefetch process:

| Variable Name | Value | Purpose |
|---------------|-------|---------|
| `NODE_USE_SYSTEM_CA` | `1` | Allow Node.js to use the OS trust store (certificates added via `update-ca-trust`) |

#### Configuration Files

##### Override for Build

These overrides are exposed as build-time environment variables (see
[Build-Time Environment Variables](#build-time-environment-variables)).

| Option | Value | Purpose |
|--------|-------|---------|
| [`globalFolder`][globalFolder] | `<output_dir>` | Point Yarn to the prefetched dependency cache so offline builds can resolve packages without network access |
| [`enableMirror`][enableMirror] | `true` | Enable mirroring so builds can read from the global cache populated during prefetch |
| [`enableGlobalCache`][enableGlobalCache] | `false` | true would cause the same issue as [mounting the local cache][global-cache-commit] |
| [`enableImmutableCache`](https://yarnpkg.com/configuration/yarnrc#enableImmutableCache) | `false` | Allow the build to write to the cache |

Optional:

| Option | Value | Purpose |
|--------|-------|---------|
| [`enableInlineBuilds`](https://yarnpkg.com/configuration/yarnrc#enableInlineBuilds) | `true` | Otherwise node-gyp compilation errors go to a log file, which is useless in CI |

#### Build Process Integration

**Summary: Resolving a single Yarnberry project**

1. Make sure we will use the right version of Yarnberry to process the project
2. If workspace focus is requested, validate that the project uses Yarn v4+
3. Check and reject if the project uses zero-installs
4. Prepare the configuration options relevant for prefetch (including proxy settings if configured)
5. Disable plugins
6. Run `yarn info …` to get the necessary data (or per-workspace info queries if using workspace
   focus)
7. Validate that we can parse needed locator in the output
8. Reject unsupported dependency types (git, exec)
9. If using workspace focus, strip `scripts` from workspace `package.json` files
10. Run `yarn install …` or `yarn workspaces focus …` to fetch the dependencies
11. Resolve production-only locators
12. Generate the SBOM based on the data from `yarn info`, the zip files of the dependencies and the
    `.yarnrc.yml` configuration (also report missing checksums based on the data from `yarn info`);
    mark development packages with `cdx:npm:package:development`; include backend annotations and
    proxy external references where applicable
13. Set environment variables for the build

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

- Any git dependency are no longer supported (see [Arbitrary Code Execution During Prefetch](#arbitrary-code-execution-during-prefetch))
- ~~The postinstall script of any dependency~~ — solved by `--mode=skip-build`
  (see [Prefetch Implementation](#prefetch-implementation))

### Yarn v4 Support

This design was originally written for Yarn@3.x. Yarn v4 was released in October 2023, which
coincided with the time that v3 support was being introduced in Hermeto. To limit the scope of
implementation, v4 support was deferred. As more projects migrate to v4, proper support has become
a priority.

Main references:
- [v4 blog post](https://yarnpkg.com/blog/release/4.0)
- [Breaking changes for Yarn 4](https://github.com/yarnpkg/berry/issues/3591)

#### Changes in Yarn Behavior

##### Global cache is enabled by default

The [enableGlobalCache](https://yarnpkg.com/configuration/yarnrc#enableGlobalCache) option allows
the user to set a shared location for the cache folder. Hermeto already treats this option the
following way:

- Set to `true` during the prefetch, and point it to a specific location in order to keep the
  prefetched dependencies.
- Set to `false` during the build, since the build needs to read from the cache, but the
  dependencies need to be installed to a local folder since they'll be used during the runtime.

Since v4 defaults this to `true`, Hermeto is now setting the opposite option that would be expected
as default during build-time.

##### All official plugins are enabled by default

Official plugins can no longer be disabled by `.yarnrc.yml` configuration. The current official
plugins don't introduce any behavior that would taint the accuracy of the prefetched dependencies.
Most of them only add support to protocols (which will still be filtered using the same rules) or
CLI commands.

The list of all official plugins can be found under the "Default Plugins" section in the
[API](https://yarnpkg.com/api) page on the official Yarn documentation. Here's a short summary of
every official plugin (as of 4.5.3):

<details>
    <summary>Plugins that add support for a protocol</summary>

- plugin-exec
- plugin-file
- plugin-git
- plugin-http
- plugin-link
- plugin-npm
- plugin-patch
</details>

<details>
    <summary>Plugins that enable a CLI command</summary>

- plugin-essentials
- plugin-init
- plugin-interactive-tools
- plugin-npm-cli
- plugin-pack
- plugin-stage
- plugin-workspace-tools
- plugin-version
</details>

<details>
    <summary>Other plugins</summary>

- plugin-compat: patches packages that aren't compatible with Plug'n'Play
- plugin-constraints: support for [constraints](https://yarnpkg.com/features/constraints)
- plugin-dlx: install a package in temporary environment
- plugin-github: improves the performance when cloning from Github
- plugin-nm: support for installing packages in `node_modules`
- plugin-pnp: support for [Plug'n'Play](https://yarnpkg.com/features/pnp)
- plugin-pnpm: support for installing packages using symlinks
- plugin-typescript: Automatically adds `@types/` packages into your dependencies
</details>

Hermeto proceeds by disabling non-official plugins without introducing any arbitrary code execution,
and keeps the current behavior for v3 projects unchanged.

###### A note about plugin-typescript

The [typescript](https://yarnpkg.com/api/plugin-typescript) plugin automatically includes types when
adding a dependency that does not package them by default. Since these changes are reflected in the
`package.json` file, Hermeto handles them normally.

<details>
    <summary>Example of how the Typescript plugin works</summary>

```
$ yarn add lodash
➤ YN0000: · Yarn 4.5.3
➤ YN0000: ┌ Resolution step
➤ YN0085: │ + @types/lodash@npm:4.17.13, lodash@npm:4.17.21
➤ YN0000: └ Completed
➤ YN0000: ┌ Fetch step
➤ YN0013: │ A package was added to the project (+ 957.26 KiB).
➤ YN0000: └ Completed in 0s 252ms
➤ YN0000: ┌ Link step
➤ YN0000: └ Completed
➤ YN0000: · Done in 0s 313ms

$ cat package.json
{
  "name": "yarn-types",
  "packageManager": "yarn@4.5.3",
  "dependencies": {
    "lodash": "^4.17.21"
  },
  "devDependencies": {
    "@types/lodash": "^4"
  }
}
```
</details>

##### pnpDataPath is no longer configurable

The `pnpDataPath` config option was removed; the default path `./.pnp.data.json` is now hard-coded.
The only mention of `pnpDataPath` in Hermeto is the check for paths pointing outside of the repo.
The check is kept for Yarn v3 compatibility and is simply skipped in v4.

##### Yarn now caches npm version metadata

Yarn only seems to create the metadata cache folder (`{globalFolder}/metadata/npm`) when                                                  
the [hardened mode](https://yarnpkg.com/configuration/yarnrc#enableHardenedMode) is enabled.                                              
Running `yarn install` on a local project or prefetching with Hermeto does not generate this                                              
extra folder. The metadata is a collection of JSON files with a few kilobytes each.

##### Changes to .yarnrc.yml options

- **enableConstraintsChecks**: when set to true, automatically executes
  [constraint checks](https://yarnpkg.com/features/constraints) after `yarn install` finishes.
  Hermeto explicitly disables it during prefetch.

Some new options worth of notice:

- **cacheMigrationMode**: determines behavior when dealing with outdated cache. Hermeto never reuses
  cache, so this has no effect.
- **enableOfflineMode**: tells Yarn to use the local cache instead of making a network request. Since
  it is not enforcing (it tries to use the local cache only if possible), setting it as a build
  environment variable is not useful.
- **tsEnableAutoTypes**: enable/disable the installing of types for packages that don't provide their
  own. This only happens during `yarn add`, so it has no effect on Hermeto.

##### Changes to yarn.lock

When updating v3 projects to v4, some differences in the lockfile appear:

**`npm:` locator added to npm dependencies:**
```
   dependencies:
-    chownr: ^2.0.0
-    fs-minipass: ^2.0.0
-    minipass: ^5.0.0
-    minizlib: ^2.1.1
-    mkdirp: ^1.0.3
-    yallist: ^4.0.0
-  checksum: ...
+    chownr: "npm:^2.0.0"
+    fs-minipass: "npm:^2.0.0"
+    minipass: "npm:^5.0.0"
+    minizlib: "npm:^2.1.1"
+    mkdirp: "npm:^1.0.3"
+    yallist: "npm:^4.0.0"
+  checksum: ...
```

**Changes to some instances of the file locator:**
```
-  resolution: "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz::locator=berryscary%40workspace%3A."
+  resolution: "strip-ansi-tarball@file:external-packages/strip-ansi-4.0.0.tgz#external-packages/strip-ansi-4.0.0.tgz::hash=e17689&locator=berryscary%40workspace%3A."
```

Yarn v4 introduced the subdirectory and the hash to the previously existing locator. These parts
are already handled by the v3 implementation.

**Changes to some instances of the patch locator:**
```
-  resolution: "typescript@patch:typescript@npm%3A5.1.6#~builtin<compat/typescript>::version=5.1.6&hash=5da071"
+  resolution: "typescript@patch:typescript@npm%3A5.1.6#optional!builtin<compat/typescript>::version=5.1.6&hash=5da071"
```

Hermeto processes both forms of the patch locator and produces pedigree records in the CycloneDX SBOM.

##### Hardened mode

Yarn v4 has introduced a [hardened mode](https://yarnpkg.com/blog/release/4.0#hardened-mode) to
avoid lockfile poisoning attacks (i.e. when the resolved url for a dependency points to a
non-standard malicious location). The downside is that `yarn install` time increases by roughly
1.5 to 2 times. Hermeto may benefit from toggling this on during the prefetch, but the extra
prefetch time needs consideration.

##### Workspace focus

[yarn workspaces focus](https://yarnpkg.com/cli/workspaces/focus) installs only the dependencies
for specified workspaces and their transitive workspace dependencies. To produce accurate output,
Hermeto queries each specified workspace individually using Yarn's workspace-scoped `info` command,
which reports only the transitive dependencies of that workspace.

`yarn workspaces focus` does not support `--mode skip-build`
([yarnpkg/berry#3524](https://github.com/yarnpkg/berry/issues/3524)), and `enableScripts: false`
[does not apply to workspace scripts](https://github.com/yarnpkg/berry/pull/4781). To prevent
workspace lifecycle scripts from executing, Hermeto strips the `scripts` field from workspace
`package.json` files before running the command.

> [!NOTE]
> This feature is available in Yarn v3 via the `@yarnpkg/plugin-workspace-tools` plugin, but only
> Yarn v4 (where the plugin is built-in) is supported at this time.

#### Decision

The changes needed to support Yarn v4 boil down to raising the maximum supported version, adding a
few integration tests to cover v4 scenarios and updating the documentation. The Corepack shim is
compatible with v4, so the project will still be processed with the exact Yarn version that is
defined in its configuration files.

Enabling the newly introduced hardened mode during the prefetch might prove useful to further
increase the security of the process, but is by no means necessary to introduce basic support for
v4. The impact on the prefetching speed is something that needs to be investigated in depth before
enabling it, but the decision can be deferred.

Workspace focus is exposed as an optional `workspaces` field in the package input. When omitted,
existing behavior is unchanged. An empty list is rejected as invalid input, and the field requires
Yarn v4 or later.

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
[v4-npmRegistryServer]: https://yarnpkg.com/configuration/yarnrc#npmRegistryServer
[yarnrc-ref]: https://v3.yarnpkg.com/configuration/yarnrc
[zero-installs]: https://v3.yarnpkg.com/features/zero-installs
