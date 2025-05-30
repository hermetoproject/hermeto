# [Cargo][]

## Prerequisites

To use Hermeto with Cargo locally, ensure you have Cargo binary installed on
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
the `--output-dir` option for the `fetch-deps` command. See the help message
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

## Hermetic build

After using the `fetch-deps`, and `inject-files` commands to set up the
directory, you can build your project hermetically. Here is an example of a
Dockerfile with basic instructions to build a Rust project

```dockerfile
FROM docker.io/library/rust:latest

WORKDIR /app

COPY Cargo.toml Cargo.lock .cargo .

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

[Cargo]: https://doc.rust-lang.org/cargo
