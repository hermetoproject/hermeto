# Adding Cargo support to Hermeto

## Background

[Cargo](https://doc.rust-lang.org/cargo/) is the package manager of choice for
[Rust](https://www.rust-lang.org/) programming language.  It handles building
Rust projects as well as retrieving and building their dependencies. Basic Cargo
functionality could be further extended with plugins.

A typical Cargo-managed project has the following structure:

```
├── .cargo
│   └── config.toml
├── Cargo.toml
├── Cargo.lock
└── src
    └── main.rs (or lib.rs)
```

Where Cargo.toml contains dependency listing and project configuration,
Cargo.lock is a lockfile that contains the latest resolved dependencies
and .cargo/config.toml: package manager specific configuration.

### Glossary

- crate: smallest amount of code that the Rust compiler considers at a time.
- package: a bundle of one or more crates that provides a set of functionality;
  defined by a `Cargo.toml` file.

## Specifying dependencies

Cargo supports several types of
[dependencies](https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html):
on crates distributed through registries, on github projects and on filesystem
paths.

The examples below show the different types of dependencies Cargo supports, and
how they can be specified in the `Cargo.toml` file.

<details>
  <summary>default registry (crates.io)</summary>

  ```toml
  [dependencies]
  # pinned version
  heck = "0.4.1"

  # pinned version, same as above
  heck = "^0.4.1"

  # greater than
  heck = "~0.4.1"

  # wildcard
  heck = "0.4.*"
  ```
</details>

<details>
  <summary>git</summary>

  ```toml
  [dependencies]
  # will fetch latest commit from default branch
  rand = { git = "https://github.com/rust-random/rand" }

  # fetch specific branch
  rand = { git = "https://github.com/rust-random/rand", branch = "main" }

  # fetch specific tag
  rand = { git = "https://github.com/rust-random/rand", tag = "alpha" }

  # fetch specific commit
  rand = { git = "https://github.com/rust-random/rand", rev = "8792268dfe57e49bb4518190bf4fe66176759a44" }

  # fetch the dependency and validate if the version matches what was pinned
  # can be used alongside any of the variations above
  rand = { git = "https://github.com/rust-random/rand", rev = "8792268dfe57e49bb4518190bf4fe66176759a44", version = "0.8.4"}
  ```
</details>

<details>
  <summary>path</summary>

  ```toml
  [dependencies]
  heck = { path = "./heck" }
  ```
</details>


<details>
  <summary>platform specific</summary>
  Note: in cargo docs, "platform" refers interchangeably to both architecture
  and OS Cargo has support for specifying dependencies under a certain platform
  with `#[cfg]` syntax:

  ```
      [target.'cfg(windows)'.dependencies]
      winhttp = "0.4.0"

      [target.'cfg(unix)'.dependencies]
      openssl = "1.0.1"
  ```

  or

  ```
      [target.x86_64-pc-windows-gnu.dependencies]
      winhttp = "0.4.0"

      [target.i686-unknown-linux-gnu.dependencies]
      openssl = "1.0.1"
  ```

  Cargo build apparently requires all dependencies to be present - even if they
  won't be used ( this was determined experimentally and this is in line with
  other package managers behavior, see, for example, related section in
  [Bundler](../bundler.md) documentation).
</details>

<details>
  <summary>multiple locations</summary>

  A package can't be published to crates.io if it has a git or a path
  dependency without a version number.  This is because when building the
  package locally, it will use the git or path dependency, but when it's
  published to crates.io, it'll use the registry version of the dependency.

  ```toml
  [dependencies]
  # the version also needs to be specified in case the crate will be published to crates.io
  rand = { git = "https://github.com/rust-random/rand", rev = "8792268dfe57e49bb4518190bf4fe66176759a44", version = "0.8.4"}
  heck = { path = "./heck", version = "0.4.1" }
  ```
</details>

<details>
  <summary>alternative registry</summary>

  Once an alternative registry is configured in `.cargo/config.toml`, it can be used to specify a
  dependency.

  ```toml
  [dependencies]
  some-crate = { version = "1.0", registry = "my-registry" }
  ```
</details>

All the dependencies types mentioned above are supported by Cargo out of the
box, with either no or minimal additional set up.

### Cargo.lock

The `Cargo.lock` file follows the toml format. Below are some examples of how
dependencies are represented in it.

<details>
  <summary>main or local package</summary>

  ```toml
  [[package]]
  name = "rustic"
  version = "0.1.0"
  dependencies = [
  "adler",
  "bombadil",
  "clap",
  "heck",
  "indicatif",
  "rand 0.8.4",
  "textwrap",
  "utils",
  ]
  ```
</details>

<details>
  <summary>crates.io package</summary>

  ```toml
  [[package]]
  name = "rand_hc"
  version = "0.2.0"
  source = "registry+https://github.com/rust-lang/crates.io-index"
  checksum = "ca3129af7b92a17112d59ad498c6f81eaf463253766b90396d39ea7a39d6613c"
  dependencies = [
  "rand_core 0.5.1",
  ]
  ```
</details>

<details>
  <summary>git package</summary>

  ```toml
  [[package]]
  name = "rand_core"
  version = "0.6.3"
  source = "git+https://github.com/rust-random/rand?rev=8792268dfe57e49bb4518190bf4fe66176759a44#8792268dfe57e49bb4518190bf4fe66176759a44"
  dependencies = [
  "getrandom 0.2.11",
  ]
  ```
</details>


## Other dependency types

Besides regular dependencies, cargo also supports
[dev](https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html#build-dependencies)
and
[build](https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html#build-dependencies)
dependencies. Here's how they're defined in `Cargo.toml`:

```toml
[dev-dependencies]
textwrap = "0.15.2"

[build-dependencies]
adler = "0.2.3"
```

They are resolved in the exact same way as regular dependencies in the
`Cargo.lock` file, which means they can't be identified just by looking at this
file. They can be identified via the `cargo manifest` command, or by looking at
the `Cargo.toml file`.

## Workspaces
A [workspace](https://doc.rust-lang.org/cargo/reference/workspaces.html) is
simply a way to manage multiple packages together. This means it'll have a
single `Cargo.lock` file and that common `cargo` commands will affect all
packages within the workspace.  Also, all path dependencies are automatically
considered a workspace member if they reside in the workspace.

Since workspaces are also path dependencies, they will be reported as expected
in the Cargo.toml file or via the `cargo metadata` command.

<details>
  <summary>sample project structure</summary>

  ```
    ├── Cargo.toml
    ├── Cargo.lock
    ├── src
    │   └── main.rs
    └── utils
        └── lib.rs
  ```

</details>

<details>
  <summary>sample Cargo.toml</summary>

  ```toml
  [workspace]
  members = ["utils"]

  [package]
  name = "my-pkg"
  version = "0.1.0"

  [dependencies]
  utils = { path = "utils" }
  ```
</details>

## Features

[Features](https://doc.rust-lang.org/cargo/reference/features.html) allow
conditional compilation of projects. From hermeto's perspective the most
important aspect of features is optional dependencies. Optional dependency is
such dependency which will not be processed unless explicitly requested.  The
safest way to deal with optional dependencies in the context of hermetic builds
would be to use `--all-features` flag with cargo commands when prefetching
dependencies.

## Build Scripts

Any package that contains a `build.rs`
[file](https://doc.rust-lang.org/cargo/reference/build-scripts.html) in it's
root will have it executed during build-time.  Note that this does not happen
in any other stage, such as during vendoring or dependency fetching.  The build
script can contain arbitrary code, and not running it could result in a failed
build, moreover, a [plugin](https://embarkstudios.github.io/cargo-deny/) is
necessary to skip build scripts.

## Vendoring

Cargo offers the option to
[vendor](https://doc.rust-lang.org/cargo/commands/cargo-vendor.html) the
dependencies by using `cargo vendor`. All dependencies (including git
dependencies) are downloaded to the `./vendor` folder by default.

The command also prints the required configuration that needs to be added to
`.cargo/config.toml` in order for the offline compilation to work:

```toml
[source.crates-io]
replace-with = "vendored-sources"

[source."git+https://github.com/rust-random/rand?rev=8792268dfe57e49bb4518190bf4fe66176759a44"]
git = "https://github.com/rust-random/rand"
rev = "8792268dfe57e49bb4518190bf4fe66176759a44"
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
```

Note that each git dependency has its own separate configuration in the generated output.
Also, vendoring does not trigger any builds scripts.


# Cargo support in Hermeto

## Approach 1 (preferred): use cargo commands

### Identifying the dependencies

It can be done by using the `cargo metadata` command, so there's no need to parse the
`Cargo.lock` file. Here's an example:

```
$ cargo metadata --frozen | jq '.packages[] | select(.name == "adler")'
```

<details>
  <summary>json output</summary>

  ```json
  {
    "name": "adler",
    "version": "0.2.3",
    "id": "adler 0.2.3 (registry+https://github.com/rust-lang/crates.io-index)",
    "license": "0BSD OR MIT OR Apache-2.0",
    "license_file": null,
    "description": "A simple clean-room implementation of the Adler-32 checksum",
    "source": "registry+https://github.com/rust-lang/crates.io-index",
    "dependencies": [
      {
        "name": "compiler_builtins",
        "source": "registry+https://github.com/rust-lang/crates.io-index",
        "req": "^0.1.2",
        "kind": null,
        "rename": null,
        "optional": true,
        "uses_default_features": true,
        "features": [],
        "target": null,
        "registry": null
      },
      {
        "name": "rustc-std-workspace-core",
        "source": "registry+https://github.com/rust-lang/crates.io-index",
        "req": "^1.0.0",
        "kind": null,
        "rename": "core",
        "optional": true,
        "uses_default_features": true,
        "features": [],
        "target": null,
        "registry": null
      },
      {
        "name": "criterion",
        "source": "registry+https://github.com/rust-lang/crates.io-index",
        "req": "^0.3.2",
        "kind": "dev",
        "rename": null,
        "optional": false,
        "uses_default_features": true,
        "features": [],
        "target": null,
        "registry": null
      }
    ],
    "targets": [
      {
        "kind": [
          "lib"
        ],
        "crate_types": [
          "lib"
        ],
        "name": "adler",
        "src_path": "/home/bpimente/.cargo/registry/src/index.crates.io-6f17d22bba15001f/adler-0.2.3/src/lib.rs",
        "edition": "2015",
        "doc": true,
        "doctest": true,
        "test": true
      },
      {
        "kind": [
          "bench"
        ],
        "crate_types": [
          "bin"
        ],
        "name": "bench",
        "src_path": "/home/bpimente/.cargo/registry/src/index.crates.io-6f17d22bba15001f/adler-0.2.3/benches/bench.rs",
        "edition": "2015",
        "doc": false,
        "doctest": false,
        "test": false
      }
    ],
    "features": {
      "compiler_builtins": [
        "dep:compiler_builtins"
      ],
      "core": [
        "dep:core"
      ],
      "default": [
        "std"
      ],
      "rustc-dep-of-std": [
        "core",
        "compiler_builtins"
      ],
      "std": []
    },
    "manifest_path": "/home/bpimente/.cargo/registry/src/index.crates.io-6f17d22bba15001f/adler-0.2.3/Cargo.toml",
    "metadata": {
      "docs": {
        "rs": {
          "rustdoc-args": [
            "--cfg docsrs"
          ]
        }
      },
      "release": {
        "no-dev-version": true,
        "pre-release-commit-message": "Release {{version}}",
        "tag-message": "{{version}}",
        "pre-release-replacements": [
          {
            "file": "CHANGELOG.md",
            "replace": "## Unreleased\n\nNo changes.\n\n## [{{version}} - {{date}}](https://github.com/jonas-schievink/adler/releases/tag/v{{version}})\n",
            "search": "## Unreleased\n"
          },
          {
            "file": "README.md",
            "replace": "adler = \"{{version}}\"",
            "search": "adler = \"[a-z0-9\\\\.-]+\""
          },
          {
            "file": "src/lib.rs",
            "replace": "https://docs.rs/adler/{{version}}",
            "search": "https://docs.rs/adler/[a-z0-9\\.-]+"
          }
        ]
      }
    },
    "publish": null,
    "authors": [
      "Jonas Schievink <jonasschievink@gmail.com>"
    ],
    "categories": [
      "algorithms"
    ],
    "keywords": [
      "checksum",
      "integrity",
      "hash",
      "adler32"
    ],
    "readme": "README.md",
    "repository": "https://github.com/jonas-schievink/adler.git",
    "homepage": null,
    "documentation": "https://docs.rs/adler/",
    "edition": "2015",
    "links": null,
    "default_run": null,
    "rust_version": null
  }
  ```
</details>

The `source` key shows where the package was fetched from, and will be `null`
for local dependencies.  This way, we can identify path and git dependencies,
    as well as the main package and dependencies fetched from non-default
    registries.

Dev and build dependencies have respective `kind`s when listed in the nested
`.dependencies` key.  To identify them and mark them as such in the SBOM, we'd
need only to check all the times a single package appears as a transitive
dependency in this output.

### Prefetching

Prefetching the packages can be done by simply using the `cargo vendor` command:

```
$ cargo vendor --locked ./hermeto-output/deps/cargo'
```

The command will handle all types of dependencies and allow them to be used
during the build stage.

### Building hermetically

When vendoring, the `cargo vendor` command also outputs the necessary
configuration to use the vendored deps to build or run the project (see
[Vendoring](#vendoring)). We'd need to simply append this configuration to a
project config file (`.cargo/config.toml`).

A typical addition to project config looks like this:

```
   [source.crates-io]
   replace-with = "vendored-sources"

   [source.vendored-sources]
   directory = "vendor"
```

The "directory" entry will be pointing to an actual directry with vendored sources.

It is possible to set some parameters through
[environment variables](https://doc.rust-lang.org/cargo/reference/config.html#environment-variables),
however this won't work for sources overrides until a
[corresponding issue](https://github.com/rust-lang/cargo/issues/5416)
is resolved.

### Summary
Pros:
- Trivial to use and less error-prone, since we're relying on a built-in command
- Repo configuration is generated automatically by the vendoring command

Cons:
- Relying on a built-in command brings it's own disadvantages:
  - We have less control on what will be executed when invoking `cargo` commands
  - We need to account for cargo behavior changes more closely
  - We need to install cargo in the Hermeto image and keep its version up to date
- Might make it harder to build Pip+Rust projects
  - Cargo will refuse to vendor an empty directory with a single `Cargo.toml` file, which
  means we'd need to minimally provide a minimal `src/main.rs` file to it.


## Approach 2 (alternative): manually fetching the dependencies

### Identifying the dependencies

By parsing the `Cargo.lock` file, we can easily identify all dependencies that
were downloaded the last time the project was built, and where to fetch them
from. This file is a `toml` file, which makes its parsing very trivial (see
examples in [Cargo.lock](#cargolock)).

The only downside we have here is that `Cargo.lock` does not specify which
dependencies are "dev" or "build". We'd need to rely on the info in
`Cargo.toml` to identify those.

### Prefetching

The info parsed from `Cargo.lock` contains the location of where each package
was fetched from (in the `source` key), as well as its checksum (for registry
dependencies).

With this info, we could simply fetch the packages from the internet (i.e.
crates.io in the majority of cases) using any standard method.

### Building hermetically

To build the project using local files, we'd need to use the same configuration as if the
files were vendored by cargo (check [option 1](#building-hermetically)). There are two
caveats to make this work manually, though:

**1 .cargo-checksum.json file:**

A file that cargo expects to live in the root of every package in a filesystem.
It can be generated by calculating the checksum of every file contained in the
package.

<details>
  <summary>Sample file</summary>

  ```json
  {
    "files": {
      "CHANGELOG.md": "042ed3158af7000c88a6617d775f11456bd30f6c7c8b5b586978faa1e11b1e24",
      "Cargo.toml": "107d13689eecfa82a8b5ae35bf835b9d2775337226630e4bdb35f22d0dd52e18",
      "LICENSE-0BSD": "861399f8c21c042b110517e76dc6b63a2b334276c8cf17412fc3c8908ca8dc17",
      "LICENSE-APACHE": "8ada45cd9f843acf64e4722ae262c622a2b3b3007c7310ef36ac1061a30f6adb",
      "LICENSE-MIT": "23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3",
      "README.md": "fa83fd5ee10b61827de382e496bf66296a526e3d2c3b2aa5ad672aa15e8d2d7f",
      "RELEASE_PROCESS.md": "a86cd10fc70f167f8d00e9e4ce0c6b4ebdfa1865058390dffd1e0ad4d3e68d9d",
      "benches/bench.rs": "c07ce370e3680c602e415f8d1ec4e543ea2163ab22a09b6b82d93e8a30adca82",
      "src/algo.rs": "b664b131f724a809591394a10b9023f40ab5963e32a83fa3163c2668e59c8b66",
      "src/lib.rs": "67f3ca5b6333e22745b178b70f472514162cea2890344724f0f66995fcf19806"
    },
    "package": "ee2a4ec343196209d6594e19543ae87a39f96d5534d7174822a3ad825dd6ed7e"
  }
  ```
</details>

<br>

**2. Nested packages:**

If a package contains subpackages (i.e. path dependencies), we will need to
unnest them and provide a flat folder structure, as `cargo vendor` would do.

**Default file structure:**
```
└──package
    ├── Cargo.toml
    ├── src
    │   └── main.rs
    ├── subpackage-1
    │   ├── Cargo.toml
    │   └── src
    │     └── lib.rs
    └── subpackage-2
        ├── Cargo.toml
        └──src
            └── lib.rs
```

**Changes needed for offline installs:**

```
├──package
│   ├── .cargo-checksum.json
│   ├── Cargo.toml
│   └── src
│       └── main.rs
├──subpackage-1
│   ├── .cargo-checksum.json
│   ├── Cargo.toml
│   └── src
│       └── main.rs
└──subpackage-1
    ├── .cargo-checksum.json
    ├── Cargo.toml
    └── src
        └── main.rs
```

### Summary

Pros:
- We won't rely on the `cargo` binary, so all the downsides of option 1 are not applicable
- Zero risk of arbitrary code execution

Cons:
- Checksum files need to be manually generated
- Sub-packages in git dependencies need to be moved to a flat structure
- The "vendor" configuration needs to be generated manually
- Extra maintenance burden for Cargo.lock parser

## Decision

Given the rich set of features provided by Cargo for managing dependencies it
is more cost effective to rely on Cargo for performing all the necessary
parsing and fetching.  This decision is in line with current approach to other
package managers (e.g. Bundler or Yarn).


## Appendix A. Rust extensions to Python

Using Rust as an extension language gains popularity in part of Python
community.  At the moment of writing a number of popular Python projects
contain Rust extensions ([cryptography](https://github.com/pyca/cryptography)
and [pydantic-core](https://github.com/pydantic/pydantic-core) among others).
Since it is Hermeto's goal to build as much as possible from source Python Rust
extensions must be also built from sources. This is not strictly Rust work
since it will be happening in Python ecosystem, however it is related to the
proposed Rust package manager and could leverage its functionality. Let us
investigate how this could be done in this Appendix.

Let us consider a typical Python package augmented with Rust:
[cryptography](https://github.com/pyca/cryptography).  When [pip PM](../pip.md)
processes a package which depends on cryptography it either downloads a
precompiled binary (if `allow_binary` is set to `true`) or download the sources
of `cryptography`(if `allow_binary` is set to `false`).  Let us consider the
case when the sources are downloaded. Then a tarball with requested version
will be present in output directory. When extracted it will contain
`cryptography` source which, among other things would contain a regular
`Cargo.toml` file:

```
$ pwd
../hermeto-output/deps/pip/cryptography-44.0.0
$ ls
Cargo.lock        LICENSE.APACHE  release.py
Cargo.toml        LICENSE.BSD     src
CHANGELOG.rst     noxfile.py      tests
CONTRIBUTING.rst  PKG-INFO
docs              pyproject.toml
LICENSE           README.rst
```

Moreover, vendoring this directory the standard way would yield expected result:
a `vendor` directory with all necessary crates in it:

```
$ ls vendor
ls: cannot access 'vendor': No such file or directory
$ cargo vendor
...
$ ls vendor
asn1         foreign-types         once_cell        proc-macro2          self_cell
asn1_derive  foreign-types-shared  openssl          pyo3                 shlex
autocfg      heck                  openssl-macros   pyo3-build-config    syn
base64       indoc                 openssl-sys      pyo3-ffi             target-lexicon
bitflags     itoa                  pem              pyo3-macros          unicode-ident
cc           libc                  pkg-config       pyo3-macros-backend  unindent
cfg-if       memoffset             portable-atomic  quote                vcpkg
$ ls vendor/asn1
Cargo.lock  Cargo.toml  clippy.toml  examples  LICENSE  README.md  src  tests
```

Now `cryptography` could be built hermetically by pip if it knows where to
find the necessary crates.

Thus to make Rust-based Python extensions hermetically buildable it is necessary to
augment _pip_ package manager to search for possible Rust-based dependencies, to
process them with Rust PM proposed in this document first and then to either copy or
link the vendored directories to locations where pip expects to find all the
necessary dependencies. After that such package could be build as any other Python
package (given that the build system has both Rust compiler and Cargo available).


## Appendix B. Crates with binaries

Crates are supposed to contain only source code. However, crates.io don't seem
to enforce any rule to prohibit crates being uploaded with binaries. This
happened at least once with [serde][serde-with-binaries], one of the most
popular rust libraries.

## Appendix C. Pip + Cargo support in Hermeto

This appendix contains the original research into the problem of support for
Python extensions written in Rust. It is kept intact for posterity.

### Context

Traditionally, performance bottlenecks in the python ecosystem are addressed
with C extensions, which introduce their own complexities and safety concerns.

Rust, with its performance, memory safety, and concurrency capabilities, is
emerging as an effective solution. Key Python packages like `cryptography` and
`pydantic-core` have incorporated Rust to enhance their performance and
reliability​​. Additionally, the Rust-based linter `ruff` is gaining
popularity due to its speed and compatibility with tools like `flake8` and
`pylint​`.

Tools such as `PyO3`, `Rust-CPython`, `maturin`, and `setuptools-rust` simplify
the integration of Rust into Python (and python into rust as well, in the case
of `PyO3`)​.

Addressing the integration challenges of Rust in Python projects is crucial to
enhancing the performance, safety, and concurrency of Python applications. The
"rustification" of Python libraries is here to stay.

On the other hand Hermeto in its current shape does not mandate the presence of
the sources for all dependencies. For example both Bundler and Pip will ignore
all binary dependencies unless requested otherwise. Once requested only the
binaries themselves will be collected during the prefetch phase.  They will be
reported in SBOM as regular packages. Making fully self-contained builds is a
larger topic and is out of scope for this document.  A general description of
how this could be achieved for Python packages depending on Rust is presented
in this section.

### The challenge and hermeto boundaries

Building projects that do DIRECTLY depend on both rust and python should be
straightforward and similar to build with pip and cargo independently. The
developers of those projects can easily have `requirements.txt`, `Cargo.lock`,
etc readily available to them and have full control of how to build their own
software. The challenge comes with indirect rust dependencies. For instance,
when your project is "pure python" but have dependencies that rely on rust
(like cryptography).

In this scenario, cargo vendor won't help unless you have all sources
available. Also, users don't have a way to explicitly declare those
dependencies, and, henceforth, aren't necessarily doing reproducible builds.

Another issue is how to configure cargo, something those developers are not
even calling directly - that will be made by the python build backend
(hopefully `maturin` or `setuptools-rust`).

In the following sections we are going to expose a bit of how `maturin` and
`setuptools-rust` are configured in order to come with ideas on how to tackle
the problem of FINDING rust dependencies on a pure-python project. This is
probably outside of the scope of hermeto, but we will need to at very least come
up with a way for those users to share the (potential) multiple Cargo.locks the
package indirectly depends or a file format designed for this. Also
[pybuild-deps][pybuild-deps] might evolve to help solving this problem, so it
is not like we would waste any time understanding these problems.

### Build dependencies

`maturin` and `setuptools-rust` are PEP517 compliant build backends for python
packages with embedded rust code.

Under the hood, `maturin` relies exclusively on `PyO3` while `setuptools-rust`
can use either `PyO3` or `Rust-CPython` (but newer projects are likely
preferring the former, as the author of `Rust-CPython` development is halted
and its author recommends `PyO3`).

### Detecting python packages with rust dependencies

We could use the presence of either `maturin` or `setuptools-rust` as build
dependencies of a python package as a heuristic to determine if a package is a
python+rust library. Alternatively, we could simply brute force searching for
`.rs` sources and/or `Cargo.toml/lock`, but looking at build dependency has one
advantage on potentially confusing situations.

In a [research with popular python packages and github python projects relying
on `maturin` or `setuptools-rust`][python-rust-research], one interesting
finding is that some packages contain multiple Cargo.locks. That usually
happens when the library vendored some other rust code for whatever reason. In
this case, it would be better to use for downstream vendoring only the "main"
lock file, which presumably would be pointing to the rest of the code.

Packages relying on `maturin` and `setuptools` have a default place to have
their main Cargo.toml/lock stored. Also, parsing the configuration it is
possible to know if the path for those manifests were modified.

#### maturin
Detecting `maturin` is easier because it only supports python packages that use
`pyproject.toml` to configure it. So detecting its presence is only a matter of
verifying if `[build-system].requires` contains `maturin`.

example:

```toml
#pyproject.toml
[build-system]
requires = ["maturin"]
```

As for the manifest location, `maturin` looks at two places by default:
- same folder as in `pyproject.toml`
- `rust/`

The manifest path can be customized under `[tool.maturin].manifest-path`.

example:
```toml
#pyproject.toml
[tool.maturin]
# Cargo manifest path
manifest-path = "Cargo.toml"
```

#### setuptools-rust

Oldest versions of `setuptools-rust` exclusively support `setup.py`, but since
version 1.7.0 it also supports `pyproject.toml`.

Detecting `setuptools-rust` on newer python packages, specially those
containing only `pyproject.toml`, is exactly like with `maturin`.

As for older packages we would need to parse `setup.cfg` and look for
`setup_requires` under `[options]`, like in the following example:

```
# setup.cfg
[options]
setup_requires = setuptools-rust >= 0.12
```

A setup.py-only scenario is impossible for packages being built in isolation.
This occurs because configuring setuptools-rust in that file requires importing
at least `setuptools_rust.RustExtension` or `setuptools_rust.RustBin`.

Parsing where manifest files are is easier if the configuration is made on
`pyproject.toml`. It will be available at `path` under the array of tables
`[[tool.setuptools-rust.ext-modules]]` or `[[tool.setuptools-rust.bins]]`. The
default value for `path` is `Cargo.toml`.

Example:

```toml
# pyproject.toml

[[tool.setuptools-rust.ext-modules]]
# Private Rust extension module to be nested into the Python package
target = "hello_world._lib"  # The last part of the name (e.g. "_lib") has to match lib.name in Cargo.toml,
                             # but you can add a prefix to nest it inside of a Python package.
path = "Cargo.toml"          # Default value, can be omitted
```

For projects relying on `setup.py`, detecting where the relevant manifest files
are is a bit more tricky and would involve playing with regexes or ast to parse
it. We would look for the keyword argument `path` or second positional argument
in `setuptools_rust.RustExtension` or `setuptools_rust.RustBin`. The default
for `path` here is also `Cargo.toml`.

Example:

```python
from setuptools import setup
from setuptools_rust import RustExtension

setup(
  rust_extensions=[
      RustExtension(
          "cryptography.hazmat.bindings._rust",
          "src/rust/Cargo.toml",
          py_limited_api=True,
          rust_version=">=1.56.0",
      )
  ],
)
```

### Vendoring rust dependencies

Even though `cargo vendor` only requires `Cargo.toml` (and optionally, but
ideally for reproducible builds, `Cargo.lock`), it will fail without source
code present. If it wasn't for this, manifest files would be enough to prefetch
dependencies.

Because of this limitation, the way quipucords (the project where Bruno C. works) prefetches
dependencies is:
- fetch the source code of the python libraries that do depend on rust
- run cargo vendor pointing to all the manifest files like the following

```
cargo vendor --manifest-path=dependencies/cryptography-43.0.0/src/rust/Cargo.toml \
  -s=dependencies/bcrypt-4.2.0/src/_bcrypt/Cargo.toml \
  -s=dependencies/maturin-1.7.0/Cargo.toml
```

Alternatively, we could prefetch dependencies in a custom code, like what was done in the [original
hermeto-rust PoC][hermeto-rust-poc] - see usage [here][hermeto-rust-poc-usage]. That would remove the
need for downloading all python sources and we could rely only on manifest files.

If we go in that direction, we could even go one step further and expect a specific file format for
python+rust dependencies. This allow customers to only need to include a file like
`rust-requirements.txt/toml/json/etc`.

### Hermetically build python + rust libraries

Both `maturin` and `setuptools-rust` will, somehow, invoke cargo during the build process. For this
reason, we can leverage the way cargo is configured to look for vendored packages.

In order to do that, we need:
1. A folder with all vendored crates
2. A .cargo/config.toml [[link to the section in the document where this is explained]] overriding
crates.io source with the path to vendored dependencies.
The config file looks like the following:
```toml
[source.crates-io]
replace-with = "local"

[source.local]
directory = "path/to/deps/cargo"
```

.cargo/config.toml MUST be [placed somewhere relative to where the cargo will be invoked][placement-of-cargo-config].
That's a bit tricky with pip because (AFAIK) there's no way to control where the build process (and
hence in which exact folder) the build will occur. For pure rust projects, placing
`.cargo/config.toml` relative to project root folder is enough. For python+rust, we need to place
this config under `/tmp/`, which is the closest place where builds occur. Example container image
(from [hermeto-rust PoC][hermeto-rust-poc-usage]):

```Dockerfile
FROM dummy-base-image:latest

COPY dummy /app
# we don't have a way to control where pip will build
# cargo dependencies, so we need to move cargo configuration
# to the place where python run builds
COPY dummy/.cargo/config.toml /tmp/.cargo/config.toml
WORKDIR /app
RUN source /tmp/hermeto.env && \
    pip3 install -r requirements.txt

```

#### Limitations

- The process likely won't work with python packages lacking Cargo.lock.
  - Interestingly, while inspecting some projects relying on maturin I saw many that didn't have a
  Cargo.lock BUT their sources uploaded to pypi actually HAD those. I couldn't find in maturin
  documentation if this is a behavior we could rely upon. Example library with this behavior:
  [css-inline][css-inline-github]
  - this might represent a risk for dependencies pointing to git sources instead of pypi/crates.io
- This approach might work well for setuptools-rust and maturin - and might work for some new tool
that resorts to invoke `cargo` at some point, but it won't work if a completely alien approach is
created.
  - OTOH, that's not a problem for fetching dependencies, only for actually building the project.
  Given this is only a big IF, this is probably fine.

<!-- REFERENCES -->

[cachi2-rust-poc]: https://github.com/bruno-fs/cachi2/blob/920e7efc9abc525d7db8abec621d25f2691a178b/cachi2/core/package_managers/cargo.py
[cachi2-rust-poc-usage]: https://github.com/bruno-fs/cachi2/blob/920e7efc9abc525d7db8abec621d25f2691a178b/docs/usage.md#example-pip-with-indirect-cargo-dependencies
[ccs-inline-github]: https://github.com/Stranger6667/css-inline/tree/wasm-v0.11.2/bindings/python
[serde-with-binaries]: https://www.bleepingcomputer.com/news/security/rust-devs-push-back-as-serde-project-ships-precompiled-binaries/
[pybuild-deps]: https://pybuild-deps.readthedocs.io/en/latest/
[python-rust-research]: https://github.com/bruno-fs/python-rust-research/blob/afebfc7ab6ef55aa0db6879b0cda7760373b60cd/python-rusty-exploration.ipynb
