# [RPM][]

- [Prerequisites](#prerequisites)
  - [RPM lockfile](#rpm-lockfile)
    - [RPM lockfile format](#rpm-lockfile-format)
    - [Real world example lockfile](#real-world-example-lockfile)
- [Specifying packages to process](#specifying-packages-to-process)
  - [Input JSON options](#input-json-options)
    - [Basic options](#basic-options)
    - [SSL/TLS configuration](#ssltls-configuration)
    - [DNF repository configuration](#dnf-repository-configuration)
    - [A complete input example](#a-complete-input-example)
- [Using fetched dependencies](#using-fetched-dependencies)
  - [Changes made by the inject-files command](#changes-made-by-the-inject-files-command)
  - [Updated project example](#updated-project-example)
- [Full example walkthrough](#example)

:warning: **This backend is best-effort only and apart from bug fixes we may be
reluctant to introduce new functionality (unless trivially added), especially
one that would require architectural OR `rpms.lock.yaml` schema changes simply
because there's a native
[DNF solution](https://github.com/rpm-software-management/libpkgmanifest) in the
making which we expect will fully replace and hence deprecate this backend.**
:warning:

## Prerequisites

To use Hermeto with RPM packages, ensure you have the following tools installed:

- `rpm` - for querying package metadata
- `createrepo_c` - for generating repository metadata during the inject-files
   step

```bash
# On Fedora/RHEL/CentOS
sudo dnf install rpm-build createrepo_c

# On Debian/Ubuntu
sudo apt-get install rpm createrepo-c
```

### RPM lockfile

For the RPM backend in Hermeto to function properly one needs a `rpms.lock.yaml`
file (i.e. a "lockfile") which is a fully resolved dependency tree of all RPM
packages that need to be downloaded in order to build and run the project. This
file can be generated using the [rpm-lockfile-prototype][] tool. Please follow
the instructions available on the project's GitHub page for detailed usage
information.

#### RPM lockfile format

The `rpms.lock.yaml` file follows a specific YAML schema outlined below that
shows required and optional fields:

```yaml
# Root level - all fields are required
lockfileVersion: 1                    # Required: currently must be 1
lockfileVendor: "redhat"              # Required: currently only "redhat" is allowed
arches:                               # Required: list of architecture objects
  - arch: "string"                    # Required: architecture name (e.g., x86_64, aarch64)

    # **At least one of 'packages' or 'source' list must be present and non-empty**
    packages:                         # Optional: list of binary RPM packages
      - url: "string"                 # Required: download URL for the package
        repoid: "string"              # Optional: repository ID
        checksum: "string"            # Optional: package checksum
        size: integer                 # Optional: file size in bytes

    source:                           # Optional: list of source RPM packages
      - url: "string"                 # Required: download URL for the source package
        repoid: "string"              # Optional: repository ID
        checksum: "string"            # Optional: package checksum
        size: integer                 # Optional: file size in bytes

    # Modular metadata are needed when installing from RHEL-8 module "streams"
    module_metadata:                  # Optional: list of module metadata files
      - url: "string"                 # Required: download URL for the metadata file
        repoid: "string"              # Required: repository ID (**mandatory for module metadata**)
        checksum: "string"            # Optional: file checksum
        size: integer                 # Optional: file size in bytes
```

**Notes on the schema:**

- `repoid` corresponds to the repository ID as found in `.repo` files, if
   missing in the lockfile a random one will be generated following the
  `hermeto-UUID[6](-source)?` pattern
- `checksum` format should be `algorithm:digest` (e.g., `sha256:abc123...`)
- `size` represents file size in bytes
- extra fields may be present in the lockfile (see the example below) that may
  be put in there by the generator tool but nothing on top of the schema above
  is processed or read by Hermeto

#### Real world example lockfile

<details>
  <summary>rpms.lock.yaml</summary>

```yaml
---
lockfileVersion: 1
lockfileVendor: redhat
arches:
- arch: x86_64
  packages:
   - url: https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi9/9/x86_64/appstream/os/Packages/a/apr-1.7.0-12.el9_3.x86_64.rpm
     name: apr
     repoid: ubi-9-appstream-rpms
     checksum: sha256:7a8d216d45355f7b656777fcb874a0803d5e97a3e7575b8b58dc7bf608919459
     size: 129032
     evr: 1.7.0-12.el9_3                         # extra field
     sourcerpm: apr-1.7.0-12.el9_3.src.rpm       # extra field
   - url: https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi9/9/x86_64/appstream/os/Packages/a/apr-util-1.6.1-23.el9.x86_64.rpm
     repoid: ubi-9-appstream-rpms
     size: 99555
     checksum: sha256:fdafa0c878091c68d7e4ff66bdffa2d3a39904351128b55caafc896175651718
     name: apr-util
     evr: 1.6.1-23.el9
     sourcerpm: apr-util-1.6.1-23.el9.src.rpm
   - url: https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi9/9/x86_64/appstream/os/Packages/a/apr-util-bdb-1.6.1-23.el9.x86_64.rpm
     repoid: ubi-9-appstream-rpms
     size: 14447
     checksum: sha256:d996f1e3b3375cd48b9910f31965e0e8c0df99b553dcac8c4368ac6ed5177623
     name: apr-util-bdb
     evr: 1.6.1-23.el9
     sourcerpm: apr-util-1.6.1-23.el9.src.rpm
   - url: https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi9/9/x86_64/appstream/os/Packages/h/httpd-tools-2.4.62-4.el9.x86_64.rpm
     repoid: ubi-9-appstream-rpms
     size: 87953
     checksum: sha256:ca56b898a477472c5de44cc2eaf40f0bdebe54508c131fdf63775851f932eed4
     name: httpd-tools
     evr: 2.4.62-4.el9
     sourcerpm: httpd-2.4.62-4.el9.src.rpm
  source:
    - url: https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi9/9/x86_64/appstream/source/SRPMS/Packages/a/apr-1.7.0-12.el9_3.src.rpm
      repoid: ubi-9-appstream-source
      name: apr
      evr: 1.7.0-12.el9_3
      size: 905784
  module_metadata: []
```

</details>

## Specifying packages to process

Hermeto expects to find an `rpms.lock.yaml` file for each RPM project on the
input. The file must be located in the root directory of the given project
(which can be a subdirectory from the repository root).

Hermeto can be then run as follows:

```shell
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  '<JSON input>'
```

where 'JSON input' is:

```js
{
  // "rpm" tells Hermeto to process RPM packages
  "type": "rpm",
  // path to the directory containing rpms.lock.yaml (relative to the --source directory)
  // defaults to "."
  "path": "."
}
```

or more simply by just invoking `hermeto fetch-deps rpm`.

### Input JSON options

The RPM package manager supports several optional configuration parameters in
the JSON input:

#### Basic options

```js
{
  "type": "rpm",
  "path": ".",
  "include_summary_in_sbom": false    // Include package's `Summary` RPM tag in the SBOM output
}
```

#### SSL/TLS configuration

For downloading packages from repositories requiring TLS authentication (e.g.
Red Hat's CDN for entitled contents):

```js
{
  "type": "rpm",
  "path": ".",
  "options": {
    "ssl": {
      "client_cert": "/path/to/client.crt",    // Path to client certificate file
      "client_key": "/path/to/client.key",     // Path to client private key file
      "ca_bundle": "/path/to/ca-bundle.crt",   // Path to CA certificate bundle
      "ssl_verify": true                       // Enable/disable TLS server certificate and hostname
                                               // verification, DON'T disable unless testing!
    }
  }
}
```

#### DNF repository configuration

You can specify additional DNF repository configuration options (see `dnf.conf`
man page) that will be set for a particular repository in the generated
`hermeto.repo` files:

```js
{
  "type": "rpm",
  "path": ".",
  "options": {
    "dnf": {                            // These need to be set **per-repository**
      "my-repo-id": {                   // Repository ID from the lockfile
        "gpgcheck": "0",                // Disable GPG signature checking
        "enabled": "1",                 // Enable the repository
        "priority": "10",               // Set repository priority
        "sslverify": "false"            // Disable TLS verification for this repo
      }
    }
  }
}
```

#### A complete input example

```js
{
  "type": "rpm",
  "path": ".",
  "include_summary_in_sbom": true,
  "options": {
    "ssl": {
      "client_cert": "/etc/pki/client.crt",
      "client_key": "/etc/pki/client.key",
      "ssl_verify": true
    },
    "dnf": {
      "rhel-8-appstream": {
        "gpgcheck": "1",
        "priority": "10"
      }
    }
  }
}
```

## Using fetched dependencies

See the [Example](#example) for a complete walkthrough of Hermeto usage.

Hermeto downloads the RPM packages and source RPMs into the `deps/rpm/` subpath
of the output directory. The structure is organized by architecture and
repository ID:

```text
hermeto-output/deps/rpm/
├── x86_64/
│   ├── ubi-9-appstream-rpms/
│   │   ├── httpd-tools-2.4.62-4.el9.x86_64.rpm
│   │   ├── apr-1.7.0-12.el9_3.x86_64.rpm
│   │   ├── ...
│   │   └── repos.d/
│   │       └── hermeto.repo
│   └── hermeto-e5ad4c/
│       └── repo-unaffiliated-package-1.0.0-1.x86_64.rpm
└── aarch64/
    └── ubi-9-appstream-rpms/
        ├── httpd-tools-2.4.62-4.el9.x86_64.rpm
        ├── ...
        └── repos.d/
            └── hermeto.repo
```

### Changes made by the inject-files command

The `inject-files` command performs two important operations for RPM packages:

1. **Repository metadata generation**: Uses `createrepo_c` to generate
   repository metadata (repodata) for each repository directory containing RPM
   packages.

2. **Repository configuration files**: Creates `hermeto.repo` files in each
   architecture's `repos.d/` directory. These files configure DNF/YUM to use the
   local packages during installation.

The generated `hermeto.repo` files contain repository definitions like:

```ini
[ubi-9-appstream-rpms]
name=Red Hat Universal Base Image 9 (RPMs) - AppStream
baseurl=file:///tmp/hermeto-output/deps/rpm/x86_64/ubi-9-appstream-rpms
gpgcheck=1

[hermeto-e5ad4c]
name=Packages unaffiliated with an official repository
baseurl=file:///tmp/hermeto-output/deps/rpm/x86_64/hermeto-e5ad4c
gpgcheck=1
```

### Updated project example

After running `inject-files`, your project directory structure will remain
unchanged since RPM packages don't require modification of source files.
However, the output directory will contain the generated repository metadata:

```text
hermeto-output/deps/rpm/x86_64/ubi-9-appstream-rpms/
├── httpd-tools-2.4.62-4.el9.x86_64.rpm
├── ...
├── repodata/
│   ├── repomd.xml
│   ├── primary.xml.gz
│   ├── filelists.xml.gz
│   └── other.xml.gz
└── repos.d/
    └── hermeto.repo
```

## Example

Let's demonstrate Hermeto usage with a sample RPM-based app - ApacheBench. Let's
start with creating the Containerfile/Dockerfile for the sample app (needed for
the lockfile generator tool):

```dockerfile
# Note the base image
FROM registry.access.redhat.com/ubi9:latest
RUN dnf -y install httpd-tools
CMD ["ab", "-V"]
```

We'll assume you have a `rpms.lock.yaml` file generated by the
[rpm-lockfile-prototype][] tool based on the following `rpms.in.yaml` file:

```yaml
contentOrigin:
  repos:
    - repoid: ubi-9-appstream-rpms
      baseurl: https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi9/9/$basearch/appstream/os
packages:
  - name: httpd-tools
    arches:
      only: x86_64
installWeakDeps: false
```

### Pre-fetch dependencies

First, we'll pre-fetch the RPM dependencies specified in the `rpms.lock.yaml`
file:

```shell
hermeto fetch-deps --source ./<your app directory> rpm
```

This command will:

- Parse the `rpms.lock.yaml` file
- Download all specified RPM packages and source RPMs
- Verify checksums and file sizes
- Generate an SBOM with package information

### Generate environment variables

**Note**: The RPM package manager does not require any environment variables, so
the `generate-env` command is not needed for RPM projects. This step can be
skipped.

### Inject project files

Generate repository metadata and configuration files:

```shell
hermeto inject-files ./hermeto-output --for-output-dir /tmp/hermeto-output
```

This command will:

- Create repository metadata using `createrepo_c`
- Generate `hermeto.repo` files for DNF/YUM configuration
- Prepare the package cache for offline installation

### Build the application image

We're now ready to build the application image using network isolation:

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto-output/deps/rpm/x86_64/repos.d)":/etc/yum.repos.d \
  --network none \
  --tag my-ab
```

[rpm-lockfile-prototype]: https://github.com/konflux-ci/rpm-lockfile-prototype
[RPM]: https://rpm.org/about.html
