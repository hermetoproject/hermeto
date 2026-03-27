# Adding DEB (Debian/Ubuntu) Support to Hermeto

## Overview

[Debian packages][deb-format] (`.deb` files) are the standard binary packaging format for
Debian, Ubuntu, and derivative distributions. The system relies on two primary tools:

- [`dpkg`][dpkg] — the low-level tool that installs, removes, and manages individual `.deb`
  packages
- [`apt`][apt] — the higher-level tool that handles dependency resolution, repository
  management, and automated downloads

Debian-based container images (`debian:bookworm`, `ubuntu:24.04`, etc.) are among the most
widely used base images. Adding DEB support to Hermeto enables offline, network-isolated
builds for these images while providing accurate SBOM output.

**Key challenge**: Unlike ecosystems such as npm (`package-lock.json`) or Cargo
(`Cargo.lock`), the Debian world lacks a standardized lockfile format for pinning exact
package versions with checksums. This design proposes a lockfile schema to fill that gap.

### Developer Workflow

A typical workflow for a developer building a Debian-based container image:

1. **Prerequisites**: A `Dockerfile`/`Containerfile` starting with a Debian or Ubuntu base
   image.
2. **Adding dependencies**: `RUN apt-get update && apt-get install -y <packages>` in the
   Dockerfile.
3. **Dependency management**: `apt` automatically resolves transitive dependencies
   (`Depends`, `Pre-Depends`) during installation.
4. **Build process**: Container build tools (Podman, Docker) execute the Dockerfile, which
   calls `apt-get install` to fetch and install packages from remote repositories.

The problem: this workflow requires network access during the build. Hermeto aims to
pre-fetch all required packages so that `apt-get install` can run entirely offline.

### How the Debian Package System Works

#### Repository Structure

Debian repositories follow a well-defined [directory layout][repo-format]:

```text
repository-root/
├── dists/
│   └── <codename>/                    # e.g., bookworm, jammy
│       ├── Release                    # Signed metadata: checksums for all index files
│       ├── InRelease                  # Same as Release but with inline GPG signature
│       ├── Release.gpg                # Detached GPG signature for Release
│       └── <component>/              # e.g., main, contrib, non-free
│           ├── binary-<arch>/        # e.g., binary-amd64, binary-arm64
│           │   └── Packages.gz       # Index of all binary packages
│           └── source/
│               └── Sources.gz        # Index of all source packages
└── pool/
    └── <component>/
        └── <prefix>/<source>/        # e.g., pool/main/c/curl/
            ├── curl_7.88.1-10_amd64.deb
            └── curl_7.88.1-10.dsc
```

- **`dists/`** contains repository metadata organized by release codename, component, and
  architecture.
- **`pool/`** contains the actual `.deb` and source package files, organized to avoid
  duplication across releases.

#### Packages.gz — The Package Index

The `Packages.gz` file is a gzip-compressed text index listing every binary package
available for a given release, component, and architecture. Each entry contains fields such
as:

```text
Package: curl
Version: 7.88.1-10+deb12u8
Architecture: amd64
Filename: pool/main/c/curl/curl_7.88.1-10+deb12u8_amd64.deb
Size: 311296
SHA256: 5b8c3db5ed8c5ef65c3a7d0a3c8c3dbd05e8a9b3a5c7f2e1d4b6a8c9e0f1a2b3
Depends: libc6 (>= 2.34), libcurl4 (= 7.88.1-10+deb12u8), ...
Description: command line tool for transferring data with URL syntax
```

> **Important**: All fields needed for the lockfile (`Package`, `Version`, `Filename`,
> `SHA256`, `Size`) are directly available in `Packages.gz`. The lockfile generator
> prototype can derive all required data from this single metadata source.

#### Release / InRelease — Signed Metadata

The `Release` file at each distribution root contains checksums (`MD5Sum`, `SHA1`,
`SHA256`) for every index file (`Packages.gz`, `Sources.gz`, etc.) in the distribution.
This file is GPG-signed (either as a detached `Release.gpg` or inline as `InRelease`) to
ensure integrity and authenticity of the repository metadata.

#### Package Identity and Versioning

Debian packages are identified by their name, version, and architecture:

- **Name**: the source or binary package name (e.g., `curl`)
- **Version**: follows the format `[epoch:]upstream_version[-debian_revision]`
  - `epoch` — an integer for ordering when upstream versioning changes (optional, defaults
    to 0)
  - `upstream_version` — the version of the original upstream software
  - `debian_revision` — the Debian-specific packaging revision
- **Architecture**: `amd64`, `arm64`, `i386`, `armhf`, `all` (architecture-independent),
  etc.

#### Dependency Resolution

`apt` resolves dependencies using the following relationship fields from `Packages.gz`:

| Field | Behavior |
|-------|----------|
| `Depends` | Must be installed; installed before the package |
| `Pre-Depends` | Must be installed and configured before unpacking |
| `Recommends` | Installed by default unless `--no-install-recommends` |
| `Suggests` | Not installed by default, informational only |
| `Conflicts` | Cannot be installed alongside this package |
| `Provides` | This package provides a virtual package |

For Hermeto's purposes, `Depends` and `Pre-Depends` are always required. `Recommends` are
typically included unless explicitly excluded. Resolution of these relationships is the job
of the lockfile generator tool, not Hermeto itself.

## Design

### Scope

#### In Scope

- Binary `.deb` packages from standard Debian and Ubuntu repositories
- Source packages (`.dsc` files)
- Multi-architecture lockfiles (e.g., `amd64` + `arm64` in the same file)
- Mirrors and alternative repository URLs
- Checksum verification (SHA256)

#### Out of Scope (initial implementation)

- PPAs or third-party repositories requiring custom GPG key handling
- Automatic dependency resolution (this is the lockfile generator's responsibility)
- Virtual packages and alternatives resolution
- Package pinning or APT preferences

#### Edge Cases

- **Architecture `all`** packages: these are architecture-independent and may need to be
  shared across architecture-specific directories
- **Epoch in version strings**: the `:` character in versions like `2:1.2.3-4` needs
  careful handling in filenames and PURLs
- **Packages with no checksum in the lockfile**: must be clearly marked in the SBOM (same
  approach as the RPM backend)

### Dependency List Generation

#### Lockfile Format: `debs.lock.yaml`

Hermeto requires a fully resolved lockfile as input. The Debian ecosystem does not natively
provide a lockfile format, so we define one: `debs.lock.yaml`.

> **Design rationale**: The schema mirrors the existing [`rpms.lock.yaml`][rpm-doc] format
> for consistency within Hermeto. All fields are directly derivable from Debian
> `Packages.gz` metadata (`Filename`, `SHA256`, `Size`, `Package`, `Version`,
> `Architecture`).

```yaml
# Root level — all fields are required
lockfileVersion: 1                    # Required: must be 1
lockfileVendor: "debian"              # Required: "debian" or "ubuntu"
arches:                               # Required: list of architecture objects
  - arch: "amd64"                     # Required: Debian architecture name

    # At least one of 'packages' or 'source' must be present and non-empty
    packages:                         # Optional: list of binary .deb packages
      - url: "string"                 # Required: full download URL for the package
        name: "string"               # Optional: package name (for provenance)
        version: "string"            # Optional: full Debian version string
        repoid: "string"             # Optional: repository identifier (see note below)
        checksum: "string"           # Optional: format "sha256:<hex digest>"
        size: integer                # Optional: file size in bytes

    source:                           # Optional: list of source packages
      - url: "string"                 # Required: download URL for the .dsc or source tarball
        name: "string"               # Optional: source package name
        version: "string"            # Optional: version string
        repoid: "string"             # Optional: repository identifier
        checksum: "string"           # Optional: format "sha256:<hex digest>"
        size: integer                # Optional: file size in bytes
```

**Notes on the schema:**

- **`lockfileVendor`**: Identifies the distribution family. Both Debian and Ubuntu use the
  same `.deb` format and repository structure. The value affects PURL namespace generation
  (`pkg:deb/debian/curl` vs `pkg:deb/ubuntu/curl`).
- **`repoid`**: Debian does not have a native repository identifier concept (unlike RPM's
  `.repo` files with `[repoid]` sections). Hermeto requires `repoid` to group packages by
  source repository for two purposes: organizing downloaded files into per-repo directories
  and generating `sources.list` entries during `inject-files`. If missing in the lockfile,
  a random one will be generated following the `hermeto-UUID[6](-source)?` pattern (same as
  the RPM backend).
- **`checksum`** format should be `algorithm:digest` (e.g., `sha256:abc123...`)
- **`name`** and **`version`**: While Hermeto can derive these from the `.deb` filename or
  by querying `dpkg-deb`, including them in the lockfile avoids a tool dependency during
  the fetch phase. The lockfile generator should populate these from `Packages.gz` data.
- Extra fields may be present in the lockfile (e.g., `description`, `source`) that the
  generator tool includes for provenance but Hermeto does not process.

#### Real World Example Lockfile

<details>
  <summary>debs.lock.yaml</summary>

```yaml
---
lockfileVersion: 1
lockfileVendor: debian
arches:
- arch: amd64
  packages:
   - url: https://deb.debian.org/debian/pool/main/c/curl/curl_7.88.1-10+deb12u8_amd64.deb
     name: curl
     repoid: debian-bookworm-main
     checksum: sha256:5b8c3db5ed8c5ef65c3a7d0a3c8c3dbd05e8a9b3a5c7f2e1d4b6a8c9e0f1a2b3
     size: 311296
     version: 7.88.1-10+deb12u8
   - url: https://deb.debian.org/debian/pool/main/c/curl/libcurl4_7.88.1-10+deb12u8_amd64.deb
     name: libcurl4
     repoid: debian-bookworm-main
     checksum: sha256:a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2
     size: 345088
     version: 7.88.1-10+deb12u8
   - url: https://deb.debian.org/debian/pool/main/o/openssl/libssl3_3.0.15-1~deb12u1_amd64.deb
     name: libssl3
     repoid: debian-bookworm-main
     checksum: sha256:b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3
     size: 2018304
     version: 3.0.15-1~deb12u1
  source:
    - url: https://deb.debian.org/debian/pool/main/c/curl/curl_7.88.1-10+deb12u8.dsc
      repoid: debian-bookworm-main-source
      name: curl
      version: 7.88.1-10+deb12u8
      size: 2892
```

</details>

#### Checksum Generation

- **Native checksum support**: `Packages.gz` includes `MD5sum`, `SHA1`, and `SHA256` for
  every listed package. SHA256 is the preferred algorithm.
- **Missing checksum handling**: If a lockfile entry has no checksum, the package will still
  be downloaded but will be marked in the SBOM with a `hermeto:missing_hash:in_file`
  property (consistent with the RPM backend behavior).

#### Lockfile Generator Prototype

A prototype lockfile generator tool will be developed as a companion to this backend
(similar to [rpm-lockfile-prototype][rpm-lockfile-prototype]). High-level approach:

1. **Input** (`debs.in.yaml`): package names, repository URLs, base image reference
2. **Resolution**: Fetch `Packages.gz` from configured repositories, resolve transitive
   dependencies (`Depends`, `Pre-Depends`, optionally `Recommends`)
3. **Output**: A valid `debs.lock.yaml` file with all packages, checksums, and URLs

This generator is a separate tool and is out of scope for the Hermeto backend
implementation itself. It will be developed as follow-up work.

### Fetching Content

#### Native vs. Hermeto Fetch

Hermeto will fetch `.deb` packages directly using its own HTTP download infrastructure
(`async_download_files` from `general.py`). We will **not** invoke `apt` or `dpkg` during
the fetch phase, for the following reasons:

1. **No arbitrary code execution**: `apt` runs hooks and triggers during operations, which
   violates Hermeto's ethos
2. **Reproducibility**: Direct URL-based downloading from the lockfile ensures deterministic
   behavior
3. **Consistency**: This is the same approach used by the RPM backend

#### Output Directory Structure

Downloaded packages will be organized under `deps/deb/` in the output directory:

```text
hermeto-output/deps/deb/
├── amd64/
│   ├── debian-bookworm-main/
│   │   ├── curl_7.88.1-10+deb12u8_amd64.deb
│   │   ├── libcurl4_7.88.1-10+deb12u8_amd64.deb
│   │   ├── libssl3_3.0.15-1~deb12u1_amd64.deb
│   │   └── repos.d/
│   │       └── hermeto.list
│   └── hermeto-a1b2c3/
│       └── some-unaffiliated-package_1.0_amd64.deb
└── arm64/
    └── debian-bookworm-main/
        └── ...
```

This mirrors the RPM backend's `deps/rpm/` structure.

#### Network Requirements

- **Registry endpoints**: Standard Debian/Ubuntu mirror URLs (e.g.,
  `https://deb.debian.org/debian/`, `http://archive.ubuntu.com/ubuntu/`)
- **Authentication**: Not required for public mirrors. SSL/TLS client certificate support
  can be added for private mirrors (reusing the RPM backend's `SSLOptions` model)
- **Mirror support**: The lockfile contains full URLs, so any mirror can be used as long as
  the URL is correct

### Build Environment Config

#### Environment Variables

The DEB package manager does not require any environment variables. The `generate-env`
command is not needed for DEB projects (same as RPM).

#### Configuration Files — inject-files

The `inject-files` command will perform two operations:

1. **Repository metadata generation**: Use [`dpkg-scanpackages`][dpkg-scanpackages] to
   generate `Packages.gz` index files for each repository directory. This is the DEB
   equivalent of `createrepo_c` for RPM.

   ```shell
   cd <repo-dir> && dpkg-scanpackages . | gzip > Packages.gz
   ```

2. **APT source configuration**: Generate `hermeto.list` files in each architecture's
   `repos.d/` directory. These configure `apt` to use the local packages:

   ```text
   deb [trusted=yes] file:///tmp/hermeto-output/deps/deb/amd64/debian-bookworm-main ./
   deb [trusted=yes] file:///tmp/hermeto-output/deps/deb/amd64/hermeto-a1b2c3 ./
   ```

   The `[trusted=yes]` option is necessary because the local repository is not GPG-signed.

After `inject-files`, the output directory will contain:

```text
hermeto-output/deps/deb/amd64/debian-bookworm-main/
├── curl_7.88.1-10+deb12u8_amd64.deb
├── libcurl4_7.88.1-10+deb12u8_amd64.deb
├── ...
├── Packages.gz           # Generated by dpkg-scanpackages
└── repos.d/
    └── hermeto.list      # Generated APT source configuration
```

#### Build Process Integration

To build a Debian-based application image using pre-fetched packages:

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto-output/deps/deb/amd64/repos.d)":/etc/apt/sources.list.d:Z \
  --network none \
  --tag my-app
```

The Dockerfile would use standard `apt-get install` commands — the volume mounts redirect
`apt` to use the local pre-fetched packages instead of remote repositories.

### SBOM Generation

SBOM components will be generated using the [PURL specification for `deb`
type][purl-deb]:

```text
pkg:deb/debian/curl@7.88.1-10+deb12u8?arch=amd64&repository_id=debian-bookworm-main
```

- **type**: `deb`
- **namespace**: derived from `lockfileVendor` (`debian` or `ubuntu`)
- **name**: package name
- **version**: full Debian version string
- **qualifiers**: `arch`, `repository_id`, `checksum`, `download_url` (if no `repository_id`)

## Implementation Notes

### Current Limitations

This design covers the initial experimental (`x-deb`) implementation. Known limitations:

- **No automatic dependency resolution**: Hermeto consumes the lockfile; resolution is the
  generator tool's responsibility
- **No GPG verification**: Local repositories use `[trusted=yes]`; signature verification
  of downloaded packages is a future enhancement
- **No virtual package support**: Virtual packages (`Provides` field) are not handled
  during fetch — the lockfile must contain concrete package entries
- **`dpkg-scanpackages` dependency**: The `inject-files` step requires `dpkg-dev` to be
  available. An alternative approach using `apt-ftparchive` could be considered if
  `dpkg-scanpackages` is not available

### Comparison with RPM Backend

| Aspect | RPM Backend | DEB Backend |
|--------|-------------|-------------|
| Lockfile | `rpms.lock.yaml` | `debs.lock.yaml` |
| Vendor | Fixed: `redhat` | `debian` or `ubuntu` |
| Package format | `.rpm` | `.deb` |
| Repo metadata tool | `createrepo_c` | `dpkg-scanpackages` |
| Repo config file | `hermeto.repo` (INI format) | `hermeto.list` (sources.list format) |
| Package query tool | `rpm -q` | `dpkg-deb -W` |
| PURL type | `pkg:rpm` | `pkg:deb` |

## References

- [Debian Binary Package format][deb-format]
- [Debian Repository Format specification][repo-format]
- [Debian Policy Manual — Binary packages][debian-policy]
- [PURL specification — deb type][purl-deb]
- [dpkg-scanpackages man page][dpkg-scanpackages]
- [apt-ftparchive man page][apt-ftparchive]
- [rpm-lockfile-prototype][rpm-lockfile-prototype] — reference implementation for lockfile
  generation
- [Hermeto RPM backend documentation][rpm-doc]

[deb-format]: https://man7.org/linux/man-pages/man5/deb.5.html
[dpkg]: https://man7.org/linux/man-pages/man1/dpkg.1.html
[apt]: https://manpages.debian.org/bookworm/apt/apt.8.en.html
[repo-format]: https://wiki.debian.org/DebianRepository/Format
[debian-policy]: https://www.debian.org/doc/debian-policy/ch-binary.html
[purl-deb]: https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst
[dpkg-scanpackages]: https://manpages.debian.org/bookworm/dpkg-dev/dpkg-scanpackages.1.en.html
[apt-ftparchive]: https://manpages.debian.org/bookworm/apt-utils/apt-ftparchive.1.en.html
[rpm-lockfile-prototype]: https://github.com/konflux-ci/rpm-lockfile-prototype
[rpm-doc]: ../rpm.md
