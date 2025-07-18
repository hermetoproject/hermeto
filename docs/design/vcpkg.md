# Adding Cargo support to Hermeto

## Background

[vcpkg][] is a C/C++ package manager maintained by Microsoft. At the moment of
writing it is not abandoned and supports quite a few (about 2600)  C++
libraries. Support for this PM was inquired about by users.  vcpkg supports
multiple build systems, provides both default registry as well as allows
setting up custom ones, provides binaries and sources, [the documentation][]
specifically mentions air-gapped environments which in theory maps nicely to
hermetic builds concept. Despite being advertised the underlying feature
necessary for hermetic builds is [considered experimental][] at the moment of
writing.

While vcpkg is available through at least DNF installing it from the system
package manager resulted in a partially working version due to auxiliary
scripts not being present in expected location. [The recommended way][] of
doing this appears to be by running a bootstrap script shipped via GitHub which
either downloads a release from GitHub or builds vcpkg locally.

vcpkg relies on json and cmake files to describe a package. Compiled binaries
reside within [vcpkg installation tree][].

vcpkg does not seem to provide any lock file mechanism besides pinning a
dependency to a specific version.


## Specifying dependencies

vcpkg operates with a concept of a port: a versioned recipe for building some
artifacts. Ports may depend on other ports and may have optional dependencies.
Each port specifies some metadata about its package including, but not limited
to name, version and dependencies, location of package source and build
instructions.

Every port contains a `portfile.cmake` with fetch and build instructions for a
package and [vcpkg.json][] containing metadata about the package and its
dependencies. Ports can carry patches to be applied to sources of packages.

<details>
  <summary>A typical vcpkg.json</summary>

  ```
  {
  "name": "fmt",
  "version": "11.0.2",
  "port-version": 1,
  "description": "{fmt} is an open-source formatting library providing a fast and safe alternative to C stdio and C++ iostreams.",
  "homepage": "https://github.com/fmtlib/fmt",
  "license": "MIT",
  "dependencies": [
      {
        "name": "vcpkg-cmake",
        "host": true
      },
      {
        "name": "vcpkg-cmake-config",
        "host": true
      }
    ]
  }
  ```

</details>


<details>
  <summary>The relevant part of corresponding portfile.cmake</summary>

  ```
  vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO fmtlib/fmt
    REF "${VERSION}"
    SHA512 47ff6d289dcc22681eea6da465b0348172921e7cafff8fd57a1540d3232cc6b53250a4625c954ee0944c87963b17680ecbc3ea123e43c2c822efe0dc6fa6cef3
    HEAD_REF master
    PATCHES
        fix-write-batch.patch
        fix-pass-utf-8-only-if-the-compiler-is-MSVC-at-build.patch # remove in next release
  )
  ...
  ```

</details>

Dependencies come from registries which could be of three types:

 1. The "built-in" registry (effectively a local copy of [vcpkg][] repository);
 1. A git registry -- any repository which follows certain layout rules and
    contains ports;
 1. A directory maintaining certain structure.


## Downloading dependencies

vcpkg would work on a project created with it. Before using the tool for a new
project [the official guide][] recommends acquiring vcpkg repository and
pointing an environment variable to it. This location (`$VCPKG_ROOT`) will be
used to store sources for dependencies. More precisely the downloaded sources
will be stored in `downloads` subdirectory of `$VCPKG_ROOT` as `tar.gz` files.
When downloaded from Github these files will be named as follows:
`<organization>-<package_name>-<version>` where `<organization>` is the name of
organization hosting the project on Github, `<package_name>` is the name of the
package as it exists on Github and version is package version as defined in a
corresponding portfile.  The most straightforward source of organization,
package name and version are named arguments values to `vcpkg_from_github`
function in a portfile, REPO and REF respectively. REPO could be split by the
first '/' to obtain organization and name, while REF usually contains an
expansion `$VERSION` for a value computed from `vcpkg.json`. In the most cases
version is just the latest version available, however it is technically possible
to specify a version override to pin the version to a specific value. Note that
sometimes REF value could be prefixed (e.g. with `v`) and that prefix would end
up in the name too.

Dependencies are resolved basing on registries defined in `vcpkg-configuration.json`
file in the root of the project to be built. A typical configuration file
is present below:

```
{
  "default-registry": {
    "kind": "git",
    "baseline": "30f771d4acf01bc7773d5c602443e7f839844a15",
    "repository": "https://github.com/microsoft/vcpkg"
  },
  "registries": [
    {
      "kind": "artifact",
      "location": "https://github.com/microsoft/vcpkg-ce-catalog/archive/refs/heads/main.zip",
      "name": "microsoft"
    }
  ]
}
```
Note the `default-registry` entry which point to the stock vcpkg registry.
(The other entry points to an apparently archived project.)
There are two kinds or registries: git and filesystem. Git is the default one,
filesystem will be covered below. The registry contains `ports` directory
with all the data needed to determine how to build a dependency.

There is no separate command for just
downloading sources, however `vcpkg install` accepts `--only-downloads` flag
which does exactly that. When sources are downloaded they modify two locations:
vcpkg root directory (effectively a location to which [vcpkg][] repository was
cloned) and registry cache. The latter defaults to `$HOME/.cache/vcpkg/registries/`
but could be retargeted with `$X_VCPKG_REGISTRIES_CACHE`. Once both caches are
populated a package could be built by running `vcpkg install --no-downloads`.
Setting registries cache apparently always results in re-downloading a registry
(unless there is a `--no-downloads` flag present).  Note, that if any patches
are present in a port they will be applied to sources at this time. Transitive
dependencies appear to be handled as well.

In case of necessity dependency graph could be computed and represented in a
variety of formats (excluding json) by running `vcpkg depend-info fmt`.

## Building a project with dependencies

The only thing that vcpkg does when downloading dependencies is figuring out
what to download and storing that in `downloads` subdirectory in the format
described above. Technically a clone of a registry and these downloaded
archives is everything that is necessary to build a project managed with vcpkg.
In practice, however, vcpkg would always try and update the registry before
building anything which would obviously fail in a network-isolated environment.

An alternative approach to a network-centric repository is the `filesystem` one.
This refers to a structure n the file system which contains the same data as
a git registry. When a project depends on a filesystem registry vcpkg
does not attempt to synchronize with any remote server. Given all dependencies
predownloaded to `downloads` subdirectory it is thus possible to build
such project without network access.


## Proposed solution

### Option 1

Following the precedent set in other package managers vcpkg could be used as
an external tool. It will need to be downloaded and bootstrapped first, then
caches locations will need to be set, after which package's dependencies
could be resolved.

The main problem with vcpkg appears to be the lack of any reporting mechanism
other than dumping build information to stdout:

<details>
  <summary>Sample dry-run install output</summary>

  ```
  $ vcpkg install --dry-run
  Detecting compiler hash for triplet x64-linux...
  Compiler found: /usr/bin/c++
  The following packages are already installed:
    * vcpkg-cmake:x64-linux@2024-04-23 -- git+https://github.com/microsoft/vcpkg@e74aa1e8f93278a8e71372f1fa08c3df420eb840
    * vcpkg-cmake-config:x64-linux@2024-05-23 -- git+https://github.com/microsoft/vcpkg@97a63e4bc1a17422ffe4eff71da53b4b561a7841
  The following packages will be rebuilt:
      fmt:x64-linux@10.1.1 -- git+https://github.com/microsoft/vcpkg@dfe9aa860f5a8317f341a21d317be1cf44e89f18
  The following packages will be built and installed:
      zlib:x64-linux@1.3.1 -- git+https://github.com/microsoft/vcpkg@3f05e04b9aededb96786a911a16193cdb711f0c9
  ```

</details>

While it contains enough data to populate a SBOM it has to be parsed which is
inherently error-prone. There does not seem to be any way to get around this.

Once packages are downloaded two environment variables will need to be set to
account for the caches. Note, that this would create a tighter coupling between
vcpkg used to download artifacts and one that will be used to build them.  It
is not immediately clear how big this problem is, if it is a big problem then
sources will have to be injected into a new $VCPKG_ROOT on a build system.

### Option 2

Another possible way of handling vcpkg dependencies would require the following:

 1. constructing dependencies tree for a package ourselves;
 2. consulting a registry to collect relevant ports;
 3. converting a git registry into a filesystem registry;
 4. downloading the sources;
 5. amending the project to use the new filesystem registry.

After these steps it should be possible to build the project hermetically.

`1.` is relatively straightforward and requires traversing jsons in a registry.
Once all dependencies are known `ports` directory must be traversed to extract
data about versions (the latest available unless explicitly specified otherwise) and
upstream location.

`3.` would require minimal changes to `versions` subdirectory in $VCPKG_ROOT similar to
one shown below:

```
 {
   "versions": [
     {
-      "git-tree": "68564a79f07645b24c9267fef692229c7a888559",
+      "path": "$/ports/7zip",
       "version-string": "24.09",
-      "port-version": 1
    },
...
```
Note, that `git-tree` hash is being replaced by `path` pointing to a directory containing
a port relative to $VCPKG_ROOT. It sohuld be possible to use absolute paths too here, but
that did not result in a success so far. Another amendment that appears necessary is removing
port version: for some reason versions other than 0 (the default one, assumed when
port version is missing) result in vcpkg being unable to process the recipe. This procedure
appears to be safe though because port version indicates how many changes have been made to
a recipe itself after the last package version change, thus it exists mainly for internal
bookkeeping. Nevertheless Hermeto could preserve the original values and report them in
SBOM.

`5.` appears to be necessary and consist of replacing `default-registry` section of
`vcpkg-configuration.json` with one pointing to the new filesystem registry:

```diff
  "default-registry": {
-   "kind": "git",
-   "baseline": "3f5ad7be7693ce6ac5599ddb7cc24f260b9d44f9",
-   "repository": "https://github.com/microsoft/vcpkg"
+   "kind": "filesystem",
+   "path": "/home/user/path/to/vcpkg/registry"
  },
```

Prepared this way a project should be ready for being built by vcpkg without network access.


[vcpkg]: https://github.com/microsoft/vcpkg
[the documentation]: https://learn.microsoft.com/en-us/vcpkg/
[The recommended way]: https://learn.microsoft.com/en-us/vcpkg/get_started/get-started?pivots=shell-bash#1---set-up-vcpkg
[vcpkg installation tree]: https://learn.microsoft.com/en-us/vcpkg/reference/installation-tree-layout
[vcpkg.json]: https://learn.microsoft.com/en-us/vcpkg/reference/vcpkg-json
[the official guide]: https://learn.microsoft.com/en-us/vcpkg/get_started/get-started?pivots=shell-bash
[considered experimental]: https://learn.microsoft.com/en-us/vcpkg/concepts/asset-caching
