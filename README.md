# Hermeto
<!-- markdownlint-disable-next-line link-image-style line-length -->
[![coverage badge]][hermeto coverage status] [![container badge]][hermeto container status]

Hermeto is a CLI tool that pre-fetches your project's dependencies to aid in
making your build process [hermetic][].

To see if Hermeto supports your package manager(s), check the
[package managers](#package-managers) section.

The primary intended use of Hermeto's outputs is for network-isolated container
builds.

<!-- markdownlint-disable-next-line descriptive-link-text -->
Full documentation could be found [here](https://hermetoproject.github.io/hermeto/latest/).

## Goals

Hermeto is rather picky by design. It aims to:

- encourage or enforce best practices in how projects declare and lock
  dependencies
- avoid arbitrary code execution during prefetch
- keep Hermeto's own implementation simple

To use Hermeto, your build process must be:

- **Defined** — Hermeto only fetches dependencies explicitly declared in your
  project, typically in a lockfile or equivalent input file
- **Reproducible** — every dependency, including transitive ones, must be pinned
  to an exact version. Hermeto does not resolve dependencies on its own
- **Secure** — when your package manager supports expected checksums, declare
  them so Hermeto can verify downloads against supply-chain tampering

> [!WARNING]
> Hermeto will verify checksums if present, but doesn't require them by default.
> This may change in the future.

In return, Hermeto will help make your build **auditable** by generating an SBOM
of all prefetched dependencies.

## Quick start

Container image:

```text
ghcr.io/hermetoproject/hermeto
```

See [hermeto container status][] for tags and updates.

```shell
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  --sbom-output-type cyclonedx \
  gomod
```

Hermeto is not distributed as a standalone PyPI package — run it from the
container image above. You may wish to set up an alias to make local usage more
convenient:

```shell
alias hermeto='podman run --rm -ti -v "$PWD:$PWD:z" -w "$PWD" ghcr.io/hermetoproject/hermeto:latest'
```

> [!NOTE]
> The alias mounts the current working directory — the container will have
> access to files in that directory and nowhere else.

## Documentation

The guides below explain each topic in detail.

- `docs/usage.md` — workflow and examples
- `docs/sbom.md` — SBOM structure and formats
- `docs/configuration.md` — modes and settings
- [CONTRIBUTING.md][] — development setup
- [AI_CONTRIBUTION_POLICY.md][] — AI-assisted contributions
- [CODE_OF_CONDUCT.md][]
- [SECURITY.md][]

## Package managers

| Package manager        | Ecosystem  | User doc                                |
|------------------------|------------|-----------------------------------------|
| bundler                | Ruby       | `docs/bundler.md`                       |
| cargo                  | Rust       | `docs/cargo.md`                         |
| generic                | N/A        | `docs/generic.md`                       |
| gomod                  | Go         | `docs/gomod.md`                         |
| maven (experimental)   | Java       | N/A                                     |
| npm                    | JavaScript | `docs/npm.md`                           |
| pip                    | Python     | `docs/pip.md`                           |
| rpm                    | RPM        | `docs/rpm.md`                           |
| yarn (classic / berry) | JavaScript | `docs/yarn_classic.md` / `docs/yarn.md` |

> [!WARNING]
> Experimental package managers may change without prior notice.

[container badge]: https://img.shields.io/badge/container-latest-blue
[coverage badge]: https://codecov.io/github/hermetoproject/hermeto/graph/badge.svg?token=VJKRTZQBMY
[hermetic]: https://slsa.dev/spec/v0.1/requirements#hermetic
[hermeto container status]: https://github.com/hermetoproject/hermeto/pkgs/container/hermeto/versions?filters%5Bversion_type%5D=tagged
[hermeto coverage status]: https://codecov.io/github/hermetoproject/hermeto
[CONTRIBUTING.md]: https://github.com/hermetoproject/hermeto/blob/main/CONTRIBUTING.md
[AI_CONTRIBUTION_POLICY.md]: https://github.com/hermetoproject/hermeto/blob/main/AI_CONTRIBUTION_POLICY.md
[CODE_OF_CONDUCT.md]: https://github.com/hermetoproject/hermeto/blob/main/CODE_OF_CONDUCT.md
[SECURITY.md]: https://github.com/hermetoproject/hermeto/blob/main/SECURITY.md
