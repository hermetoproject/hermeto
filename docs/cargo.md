# [Cargo][]

## Prerequisites

To use Hermeto with Cargo locally, ensure you have the Cargo binary installed on
your system. Then, ensure that the **Cargo.toml** and **Cargo.lock** are in your
project directory.

## Usage

Run the following commands in your terminal to prefetch your project's
dependencies specified in the **Cargo.lock**. It must be synchronized with the
**Cargo.toml** file. Otherwise, the command will fail.

```bash
cd path-to-your-rust-project
hermeto fetch-deps cargo
```

The default output directory is `hermeto-output`. You can change it by passing
the `--output` option for the `fetch-deps` command. See the help message
for more information.

After prefetching the dependencies, you can use the `hermeto inject-files`
command to update the `.cargo/config.toml` file in your project directory. If it
does not exist, it will be created. The file will contain instructions for Cargo
to use the prefetched dependencies when compiling a project.

Use the `--for-output-dir` option to specify the location where you want to
mount the `hermeto-output` in your container build environment. See the next
section.

**Do not forget to copy `.cargo/config.toml` when building your container
image.**

```bash
hermeto inject-files --for-output-dir /tmp/hermeto-output hermeto-output
```

*There are no environment variables that need to be set for the build phase.*

## Prefetching only what a build uses

**Cargo.lock** pins every optional dependency of every crate in the graph,
whether or not a feature turns it on. By default Hermeto prefetches and reports
all of it, so the SBOM of a project built with, say, an OpenSSL-only feature set
still lists `rustls` and `ring`.

To prefetch only what the build can use, name the workspace packages you build,
the feature flags you pass to `cargo build`, and optionally the target
platforms:

```json
{
  "type": "cargo",
  "path": ".",
  "packages": [
    {"name": "my-server", "no_default_features": true, "features": ["openssl"]},
    {"name": "my-operator", "no_default_features": true, "features": ["openssl"]}
  ],
  "platforms": ["x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"]
}
```

- `packages`: each entry takes `name`, `features` (default `[]`),
  `no_default_features` (default `false`) and `all_features` (default `false`),
  matching the `cargo build` flags of the same names. All entries must use the
  same `no_default_features` and `all_features` values.
- `platforms`: rustc target triples to keep dependencies for. Without it,
  dependencies for every platform are kept.

Hermeto resolves which locked crates those builds can reach with
[cargo-vendor-filterer][], using `cargo tree`, and replaces every other crate
with a stub that keeps its manifest. **Cargo.lock** still resolves offline, and
the tool reports which crates it stubbed, so only the crates that stay real are
reported in the SBOM. Dev-dependencies are never kept.

If the input declares fewer packages, features or platforms than your
Containerfile builds with, and the build reaches a crate that was stubbed, the
hermetic build fails to compile instead of producing an SBOM that misses it.
Declaring more than you build only reports more.

Requires `cargo-vendor-filterer` and `rustc` on `PATH` in addition to `cargo`;
both are included in the Hermeto container image. Neither compiles dependencies
nor runs their build scripts during the prefetch. `cargo tree` asks `rustc`
which `cfg` values a target has, which is why the image carries a compiler even
though the prefetch never builds anything.

## Hermetic build

After using the `fetch-deps`, and `inject-files` commands to set up the
directory, you can build your project hermetically. Here is an example of a
Dockerfile with basic instructions to build a Rust project

```dockerfile
FROM docker.io/library/rust:latest

WORKDIR /app

COPY Cargo.toml Cargo.lock ./
COPY .cargo .cargo
COPY src src

RUN cargo build --release
```

Do not forget to mount the `hermeto-output` directory to the container build
environment.

```bash
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --network none \
  --tag my-rust-app
```

## Limitations

### Resolver v3 and MSRV-aware resolution

Hermeto configures Cargo to work without requiring `rustc` in the container. To
achieve this, Hermeto sets `CARGO_RESOLVER_INCOMPATIBLE_RUST_VERSIONS=allow`
when running `cargo vendor`.

**Impact**: None. Hermeto uses `cargo vendor --locked` which vendors the exact
versions from your Cargo.lock file. Any MSRV-aware resolution choices you made
when generating the lock file are fully preserved.

> [!NOTE]
> The only exception is PERMISSIVE mode when Cargo.lock is out-of-sync
> with Cargo.toml. In this case, Hermeto regenerates the lock file without
> MSRV-aware resolution, potentially selecting newer dependency versions than
> your `rust-version` supports.

[Cargo]: https://doc.rust-lang.org/cargo
<!-- Package selection needs flags that are not in coreos/cargo-vendor-filterer
     yet; the image builds the fork branch carrying them. -->
[cargo-vendor-filterer]: https://github.com/wseaton/cargo-vendor-filterer/tree/hermeto-integration
