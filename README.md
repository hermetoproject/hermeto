<!-- markdownlint-disable-next-line MD054 MD013 MD041 -->
[![coverage badge]][hermeto coverage status] [![container badge]][hermeto container status]

# Hermeto

A CLI tool that pre-fetches your project's dependencies for [hermetic][],
network-isolated container builds and produces accurate SBOMs from the
pre-fetched dependencies.

Without network isolation, builds can silently pull tampered packages, produce
unreproducible artifacts, or accidentally hide dependencies in your audit trail.
Hermeto closes that gap.

## When Hermeto fits

Hermeto is opinionated by design. Your build process must be:

- **Defined**: Hermeto only fetches dependencies explicitly declared in your
  project, typically in a lockfile or equivalent input file
- **Reproducible**: every dependency, including transitive ones, must be pinned
  to an exact version. Hermeto does not resolve dependencies on its own
- **Secure**: when your package manager supports expected checksums, declare
  them so Hermeto can verify downloads against supply-chain tampering

**Prerequisites:** a git repository with committed lockfiles and a container
runtime (Podman or Docker) to run Hermeto and to build in network isolated
environment.

**Hermeto is not for you if** your build resolves or fetches dependencies over
the network at compile time — Hermeto requires all dependencies to be declared
and pinned up front.

## Supported package managers

Dedicated backends read your project's existing lockfiles. Without one, the
generic fetcher can pre-fetch arbitrary URLs, but you must create and maintain
the `artifacts.lock.yaml` file yourself.

- **Go**: gomod
- **Java**: maven (experimental)
- **JavaScript**: npm, pnpm, yarn (classic and berry)
- **Python**: pip
- **Ruby**: bundler
- **Rust**: cargo
- **Other**: rpm, generic

## What Hermeto provides

- **Accurate SBOMs**: generates [CycloneDX v1.6][CycloneDX] or
  [SPDX v2.3][SPDX] manifests covering every prefetched dependency
- **No arbitrary code execution**: dependencies are fetched without running
  untrusted code, keeping the build supply chain safe
- **Configurable**: tunable via YAML config files or `HERMETO_` environment
  variables (see `docs/configuration.md`)

## Quick start

Hermeto is distributed as a container image
`ghcr.io/hermetoproject/hermeto` — it is not a standalone PyPI package.
Pull the image and optionally set up an alias:

```shell
alias hermeto='podman run --rm -ti -v "$PWD:$PWD:z" -w "$PWD" ghcr.io/hermetoproject/hermeto:latest'
```

The typical workflow has four steps:

### 1. Pre-fetch dependencies

```shell
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  '{"path": ".", "type": "gomod"}'  
```

The output directory will contain the downloaded dependencies and a generated
SBOM. For multiple packages in one repo, pass a JSON array or point to a JSON
input file.

### 2. Generate environment variables

```shell
hermeto generate-env ./hermeto-output -o ./hermeto.env --for-output-dir /tmp/hermeto-output
```

Produces a shell-sourceable env file that tells your package manager where to
find the cached dependencies.

### 3. Inject project files

```shell
hermeto inject-files ./hermeto-output --for-output-dir /tmp/hermeto-output
```

Some package managers need configuration files or lockfile adjustments:
this step applies them to your source tree automatically.

### 4. Build with network isolation

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag my-app
```

Inside the Dockerfile, run `source /tmp/hermeto.env` before your package
manager commands.

### Merging SBOMs

When you fetch dependencies for multiple packages
separately, merge the results with
`hermeto merge-sboms sbom_1.json sbom_2.json -o merged.json`.

## Contributing

We welcome contributions — whether that's filing issues, suggesting features, or
submitting code. Check out [CONTRIBUTING.md][] to get started and
[AI_CONTRIBUTION_POLICY.md][] if you plan to use AI-assisted tooling.

Please follow our [CODE_OF_CONDUCT.md][] and report security issues via
[SECURITY.md][].

Hermeto is released under the [GPL-3.0-only][LICENSE] license.

[container badge]: https://img.shields.io/badge/container-latest-blue
[coverage badge]: https://codecov.io/github/hermetoproject/hermeto/graph/badge.svg?token=VJKRTZQBMY
[hermetic]: https://slsa.dev/spec/v0.1/requirements#hermetic
[hermeto container status]: https://github.com/hermetoproject/hermeto/pkgs/container/hermeto/versions?filters%5Bversion_type%5D=tagged
[hermeto coverage status]: https://codecov.io/github/hermetoproject/hermeto
[CycloneDX]: https://cyclonedx.org/docs/1.6/json
[SPDX]: https://spdx.github.io/spdx-spec/v2.3/
[AI_CONTRIBUTION_POLICY.md]: https://github.com/hermetoproject/hermeto/blob/main/AI_CONTRIBUTION_POLICY.md
[CODE_OF_CONDUCT.md]: https://github.com/hermetoproject/hermeto/blob/main/CODE_OF_CONDUCT.md
[CONTRIBUTING.md]: https://github.com/hermetoproject/hermeto/blob/main/CONTRIBUTING.md
[LICENSE]: https://github.com/hermetoproject/hermeto/blob/main/LICENSE
[SECURITY.md]: https://github.com/hermetoproject/hermeto/blob/main/SECURITY.md
