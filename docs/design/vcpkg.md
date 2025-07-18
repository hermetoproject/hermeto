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
used to store sources for dependencies. There is no separate command for just
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


## Proposed solution

Following the precedent set in other package managers vcpkg should be used as
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


[vcpkg]: https://github.com/microsoft/vcpkg
[the documentation]: https://learn.microsoft.com/en-us/vcpkg/
[The recommended way]: https://learn.microsoft.com/en-us/vcpkg/get_started/get-started?pivots=shell-bash#1---set-up-vcpkg
[vcpkg installation tree]: https://learn.microsoft.com/en-us/vcpkg/reference/installation-tree-layout
[vcpkg.json]: https://learn.microsoft.com/en-us/vcpkg/reference/vcpkg-json
[the official guide]: https://learn.microsoft.com/en-us/vcpkg/get_started/get-started?pivots=shell-bash
[considered experimental]: https://learn.microsoft.com/en-us/vcpkg/concepts/asset-caching
