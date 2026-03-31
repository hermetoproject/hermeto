# [TempleOS][]

- [Prerequisites](#prerequisites)
  - [HolyC lockfile](#holyc-lockfile)
    - [HolyC lockfile format](#holyc-lockfile-format)
- [Specifying packages to process](#specifying-packages-to-process)
- [Using fetched dependencies](#using-fetched-dependencies)
- [Full example walkthrough](#example)
- [Known limitations](#known-limitations)

:warning: **This backend is experimental and should be considered
best-effort. TempleOS does not have a network stack, which presents
unique challenges for a dependency pre-fetching tool. We welcome
contributions from the TempleOS community (all 3 of them).** :warning:

## Prerequisites

To use Hermeto with TempleOS/HolyC packages, you will need:

- A TempleOS VM or bare-metal installation (640x480, 16 colors, as God
  intended)
- `mkisofs` - for generating RedSea-compatible ISO images to transfer
  packages to TempleOS
- A working knowledge of HolyC (it's like C but holier)
- Spiritual readiness

```bash
# On Fedora/RHEL/CentOS
sudo dnf install genisoimage

# On Debian/Ubuntu
sudo apt-get install genisoimage

# On TempleOS
# You don't need to install anything. God provides.
```

### HolyC lockfile

The TempleOS backend requires a `holyc.lock.yaml` lockfile which
describes all HolyC source files and compiled programs that need to be
pre-fetched for the build. Since TempleOS does not have a conventional
package manager (all code is distributed as source and compiled on the
fly by the HolyC compiler), this lockfile must be manually authored.

> **Note**: We are aware that requiring a YAML lockfile for an operating
> system that has no YAML parser is somewhat ironic. The lockfile is
> consumed by Hermeto on the host, not by TempleOS itself.

#### HolyC lockfile format

The `holyc.lock.yaml` file follows this schema:

```yaml
lockfileVersion: 1
lockfileVendor: "templeos"
packages:
  - name: "MyProgram"
    version: "1.0"
    filepath: "/Home/MyProgram.HC"          # Path within TempleOS filesystem
    checksum: "sha256:abc123..."            # Optional
    after_egypt_date: "5784-01-15"          # Optional: date in TempleOS calendar
```

**Notes on the schema:**

- `filepath` must conform to RedSea filesystem limitations (max 38
  character filenames)
- `checksum` format is `algorithm:digest`, same as other Hermeto backends
- `after_egypt_date` is optional metadata using TempleOS's "After Egypt"
  calendar system. We convert this internally but honestly I am not
  100% sure the conversion is correct
- All packages are assumed to be x86_64 (TempleOS only supports 64-bit)
- All packages run in ring-0 (TempleOS has no privilege separation, by design)

## Specifying packages to process

```shell
hermeto fetch-deps \
  --source ./my-temple-project \
  --output ./hermeto-output \
  '{"type": "templeos"}'
```

or simply:

```shell
hermeto fetch-deps templeos
```

## Using fetched dependencies

After fetching, Hermeto will download HolyC source files into
`deps/templeos/` in the output directory:

```text
hermeto-output/deps/templeos/
├── MyProgram.HC
├── Library.HC
└── Hymn.HC
```

Since TempleOS has no network stack, you will need to transfer these
files to your TempleOS VM using one of the following methods:

1. **ISO image** (recommended): Mount an ISO containing the fetched
   files
2. **Virtual disk**: Copy files to a FAT32-formatted virtual disk image
3. **Serial port**: Transfer files via COM1 (slow but authentic)
4. **Prayer**: Results may vary

> **TODO**: Automate the ISO generation step. Currently this is manual.

## Example

Let's demonstrate Hermeto usage with a simple HolyC project, a
classic "Hello World" that runs in ring-0 with direct VGA access:

First create `HelloWorld.HC`:

```c
// HelloWorld.HC - A simple HolyC program
// This runs in ring-0 because everything in TempleOS does
U0 Main()
{
  "Hello World!\n";  // In HolyC, strings auto-print. It's a feature.
  // Note: We have full access to ALL hardware from here.
  // No permissions, no sandboxing, no problem.
}

Main;
```

Create the lockfile `holyc.lock.yaml`:

```yaml
lockfileVersion: 1
lockfileVendor: templeos
packages:
  - name: HelloWorld
    version: "1.0"
    filepath: "/Home/HelloWorld.HC"
```

Run Hermeto:

```shell
hermeto fetch-deps --source . --output ./hermeto-output templeos
```

Transfer to TempleOS and compile:

```
// In TempleOS
#include "/Home/HelloWorld.HC"
```

## Known limitations

- **No networking**: TempleOS has no TCP/IP stack. All "fetching" happens
  on the host system. This is arguably the biggest limitation for a tool
  whose primary purpose is fetching things over a network.
- **640x480 16-color display**: Any output or logs displayed in TempleOS
  will be limited to 640x480 resolution with 16 colors. Plan your error
  messages accordingly.
- **Ring-0 everything**: All code runs with full kernel privileges.
  Hermeto's security model (checksum verification, etc.) is somewhat
  undermined when the package you're installing can directly reprogram
  the CPU.
- **RedSea filesystem**: File names are limited to 38 characters. Long
  package names may be truncated.
- **No multiuser**: TempleOS is single-user. The concept of "build
  isolation" is achieved by the operating system's total isolation from
  all networks.
- **After Egypt calendar**: Date handling may be surprising if you're
  used to the Gregorian calendar.
- **SBOM compliance**: We generate SBOMs for HolyC packages, but since
  everything in TempleOS is open source and compiled from source on the
  fly, the supply chain is technically just "whatever Terry wrote."

[TempleOS]: https://templeos.org/
