# Hermeto
<!-- markdownlint-disable-next-line link-image-style line-length -->
[![coverage badge]][hermeto coverage status] [![container badge]][hermeto container status]

Hermeto is a CLI tool that pre-fetches your project's dependencies to aid in
making your build process [hermetic][]. The primary intended use of Hermeto's
outputs is for network-isolated container builds.

## Goals

Please note that Hermeto is rather picky, aiming to

- encourage or enforce best practices
- never execute arbitrary code [^pip-download-example]
- keep the implementation simple

To play nicely with Hermeto, the build process for your project must be

- **Defined** - dependencies are explicitly declared, typically in a lockfile
- **Reproducible** - all dependencies (including transitive) are pinned to exact
  versions
- **Secure** - checksums are verified when present

  ⚠ Hermeto will verify checksums if present, but doesn't require them by
  default. This may change in the future.

In return, Hermeto will help make your build

- **Auditable** - by generating a manifest of all the dependencies that go into
  your build.

## Installation

### Container image

<!-- markdownlint-disable-next-line link-image-style -->
[![container badge]][hermeto container status]

```text
ghcr.io/hermetoproject/hermeto
```

You may wish to set up an alias to make local usage more convenient

```shell
alias hermeto='podman run --rm -ti -v "$PWD:$PWD:z" -w "$PWD" ghcr.io/hermetoproject/hermeto:latest'
```

Note that the alias mounts the current working directory — the container will
have access to files in that directory and nowhere else.

To install Hermeto for local development, see CONTRIBUTING.md.

## Basic usage

```shell
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  --sbom-output-type cyclonedx \
  gomod
```

The `fetch-deps` command fetches your project's dependencies and stores them on
your disk. Hermeto also produces a detailed SBOM containing information about
all the project's components and packages. You can find the SBOM in the output
directory.

See `docs/usage.md` for a more detailed, practical example of Hermeto usage.
You might also like to check out `hermeto --help` and the `--help` texts of the
available subcommands.

## Configuration

Hermeto supports `strict` (default) and `permissive` modes via the `--mode`
CLI option. Settings can be provided through environment variables, CLI options,
or config files. See `docs/configuration.md` for full details.

## Package managers

| Package manager  | Ecosystem  |
|------------------|------------|
| [bundler][]      | Ruby       |
| [cargo][]        | Rust       |
| [generic][]      | N/A        |
| [gomod][]        | Go         |
| [npm][]          | JavaScript |
| [pip][]          | Python     |
| [rpm][]          | RPM        |
| [yarn][]         | JavaScript |

Each package manager has a dedicated documentation page under `docs/`.

## Contributing

- [CONTRIBUTING.md][] - Development setup and guidelines
- [CODE_OF_CONDUCT.md][] - Community standards
- [SECURITY.md][] - Vulnerability reporting
- [AI_CONTRIBUTION_POLICY.md][] - AI-assisted contribution guidelines

## Project status

Hermeto was derived from (but is not a direct fork of) [Cachito][].

[^pip-download-example]: See for example this [python.org discussion][]

[AI_CONTRIBUTION_POLICY.md]: https://github.com/hermetoproject/hermeto/blob/main/AI_CONTRIBUTION_POLICY.md
[bundler]: https://bundler.io
[Cachito]: https://github.com/containerbuildsystem/cachito
[CODE_OF_CONDUCT.md]: https://github.com/hermetoproject/hermeto/blob/main/CODE_OF_CONDUCT.md
[CONTRIBUTING.md]: https://github.com/hermetoproject/hermeto/blob/main/CONTRIBUTING.md
[cargo]: https://doc.rust-lang.org/cargo
[container badge]: https://img.shields.io/badge/container-latest-blue
[coverage badge]: https://codecov.io/github/hermetoproject/hermeto/graph/badge.svg?token=VJKRTZQBMY
[generic]: https://github.com/hermetoproject/hermeto/blob/main/docs/generic.md
[gomod]: https://go.dev/ref/mod
[hermetic]: https://slsa.dev/spec/v0.1/requirements#hermetic
[hermeto container status]: https://github.com/hermetoproject/hermeto/pkgs/container/hermeto/versions?filters%5Bversion_type%5D=tagged
[hermeto coverage status]: https://codecov.io/github/hermetoproject/hermeto
[npm]: https://docs.npmjs.com
[pip]: https://pip.pypa.io/en/stable
[python.org discussion]: https://discuss.python.org/t/pip-download-just-the-source-packages-no-building-no-metadata-etc/4651
[rpm]: https://rpm.org/about.html
[SECURITY.md]: https://github.com/hermetoproject/hermeto/blob/main/SECURITY.md
[yarn]: https://yarnpkg.com
