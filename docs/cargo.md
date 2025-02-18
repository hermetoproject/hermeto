# Cargo

<https://doc.rust-lang.org/cargo/>

## Prerequisites

To use Cachi2 with Cargo locally, ensure you have Cargo binary installed on your
system. Then, ensure that the **Cargo.toml** and **Cargo.lock** are in your
project directory.

## Usage

Run the following commands in your terminal to prefetch your project's
dependencies specified in the **Cargo.lock**. It must be synchronized with the **Cargo.toml**
file. Otherwise, the command will fail.

```bash
cd path-to-your-rust-project
cachi2 fetch-deps cargo
```

The default output directory is `cachi2-output`. You can change it by passing
the `--output-dir` option for the `fetch-deps` command. See the help message
for more information.

After prefetching the dependencies, you can use the prefetched dependencies
to build your project. Make sure to run the following command to update the
`.cargo/config.toml` to use them. (If the file does not exist in your repository,
it will be created).

```bash
cachi2 inject-files --for-output-dir /tmp/cachi2-output cachi2-output
```

Use `--for-output-dir` to specify the output directory you want to mount or copy
to the container. The command will update the `.cargo/config.toml` file to use the
prefetched dependencies from the specified directory.

_There are no environment variables that need to be set for the build phase._

## Hermetic build

After using the `fetch-deps`, and `inject-files` commands to set up the directory,
you can build your project hermetically. Here is an example of a Dockerfile with
basic instructions to build a Rust project:

```Dockerfile
FROM docker.io/library/rust:latest

WORKDIR /app

COPY Cargo.toml Cargo.lock .

...

RUN cargo build --release
```

Do not forget to mount the `cachi2-output` directory to the container build environment.

```bash
podman build . \
  --volume "$(realpath ./cachi2-output)":/tmp/cachi2-output:Z \
  --network none \
  --tag my-rust-app
```
