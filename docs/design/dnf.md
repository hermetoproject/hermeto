# DNF/RPM Manifest/Lockfile swap

DNF4 - <https://github.com/rpm-software-management/dnf>

DNF5 - <https://github.com/rpm-software-management/dnf5>

RPM - <https://github.com/rpm-software-management/rpm>

## Context

The current solution for RPM manifests is based on the so-called RPM lockfile
[prototype](https://github.com/konflux-ci/rpm-lockfile-prototype)
which is a proof-of-concept tool that generates a lockfile containing all RPMs required to build a
container image. Hermeto parses this file manually and downloads all RPMs locally to enable hermetic
builds.

The `rpm` package manager is not released as a supported feature, thus, it can only be used with
`--dev-package-manager` flag. User documentation does not exist either. Only available documentation
exists on the Konflux page -<https://konflux-ci.dev/docs/building/prefetching-dependencies/#rpm>.

### Limitations

TODO

- [hermetoproject/hermeto/issues/570](https://github.com/hermetoproject/hermeto/issues/570)

## DNF-native solution

[libpkgmanifest](https://github.com/rpm-software-management/libpkgmanifest)

### Overview

This library provides functionality for parsing and serializing RPM package manifest files in C++
and Python APIs. Currently, there is also a COPR repository available, where the prototype version
of the `dnf-manifest` plugin utilizing the functionality from this library is deployed. See the
usage below. COPR is an easy-to-use automatic build system providing a package repository as its
output.

### Project structure

Like the RPM lockfile prototype, the manifest file is a YAML file generated from the input YAML
file.

```bash
├── packages.input.yaml
├── packages.manifest.yaml
└── Containerfile
```

Link to input schema:
<https://github.com/rpm-software-management/libpkgmanifest/blob/main/schemas/input.json>

Link to manifest schema:
<https://github.com/rpm-software-management/libpkgmanifest/blob/main/schemas/manifest.json>

### Usage

#### CLI

Installation:

```bash
dnf copr enable rpmsoftwaremanagement/manifest-plugin-testing
dnf4 install 'dnf-command(manifest)'
```

**NOTE**: The plugin is only available for `dnf4`.

Example:

```yaml
# packages.input.yaml
document: rpm-package-input
version: 0.0.2
repositories:
    - id: fedora
      metalink: https://mirrors.fedoraproject.org/metalink?repo=fedora-40&arch=$arch
packages:
    install:
        - vim
archs:
    - x86_64
```

```bash
dnf4 manifest new --input packages.input.yaml
```

There is also an option to generate a manifest file without an input file by providing requested
packages and repositories directly in the command line:

```bash
dnf4 manifest new vim
```

```yaml
# packages.manifest.yaml
```

```bash
dnf4 manifest download
```

#### Python bindings

Installation:

```bash
dnf copr enable rpmsoftwaremanagement/libpkgmanifest-nightly
dnf4 install python3-libpkgmanifest
```

Example:

```python
import libpkgmanifest.common
import libpkgmanifest.manifest

parser = libpkgmanifest.manifest.Parser()
manifest = parser.parse("./packages.manifest.yaml")

print("manifest major version is:", manifest.version.major)
print("manifest minor version is:", manifest.version.minor)
print("manifest patch version is:", manifest.version.patch)
```

## Hermeto implementation

### Prefetching (approach 1)

TODO

### Prefetching (approach 2)

Prefetching could also be done by manually parsing the manifest file and downloading all RPMs to the
output directory.

Prefetching the packages can be done by simply using the `dnf4 manifest` command:

```bash
dnf4 manifest download --destdir "${output_dir}/deps/rpm"
```

The command will handle all RPMs and allow them to be used during the build stage.

### Hermetic build

The same approach as for the `rpm` prototype (using createrepo_c and repo file).
