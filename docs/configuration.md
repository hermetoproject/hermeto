# Configuration

Hermeto's behavior can be tuned with CLI flags, environment variables, and YAML
config files. This page describes operating modes and available settings.

## Modes

Hermeto currently supports two fetch modes. In `strict` mode, unmet input
requirements are errors. In `permissive` mode, the same conditions produce
warnings and Hermeto continues, however resulting SBOM may be incomplete or
inaccurate.

Set the global CLI option `--mode` to one of:

- `strict` (default)
- `permissive`

The permissive mode currently suppresses:

- Go `vendor` directory inconsistencies (see [gomod](gomod.md#vendoring))
- `Cargo.toml` out of sync with `Cargo.lock`
- non-Git sources (e.g., unpacked tarballs)
- Yarn Berry plain git dependencies (see [yarn](yarn.md#git-dependencies));
  strict mode rejects them because `package.json` and `yarn.lock` must be
  rewritten. Patched git dependencies and git dependencies that select a
  workspace inside the cloned repository fail in both modes

Using permissive mode on non-Git trees is mainly useful for smoke tests; SBOM
accuracy is reduced. See [usage](usage.md#general-process) for Git repository
expectations.

For details on how permissive violations are tracked in SBOM output, see
`docs/design/permissive-mode.md`.

## Config sources

Hermeto merges settings from several sources, where higher-priority sources
override lower-priority ones (listed top to bottom):

1. **Environment variables** — prefix `HERMETO_`, use `__` for nested keys
   (e.g., `HERMETO_GOMOD__DOWNLOAD_MAX_TRIES=10`)
2. **CLI** — `--config-file path/to/config.yaml`
3. **Config files** — loaded automatically when present:
   - `~/.config/hermeto/config.yaml`
   - `hermeto.yaml`
   - `.hermeto.yaml`

Hermeto only supports YAML config files.

## Settings

Some settings apply to every package manager. Others apply only to specific
backends.

### `http`

Applies to all package managers.

| Key | Default | Description |
| --- | --- | --- |
| `connect_timeout` | `30` | Connection timeout in seconds |
| `read_timeout` | `300` | Read timeout in seconds; long downloads succeed while data flows |
| `max_retries` | `5` | Maximum HTTP request retries |

### `runtime`

Applies to all package managers.

| Key | Default | Description |
| --- | --- | --- |
| `concurrency_limit` | `5` | Maximum concurrent operations |
| `subprocess_timeout` | `3600` | Subprocess timeout in seconds |

### Proxy settings

Applies to `bundler`, `cargo`, `gomod`, `npm`, `pnpm`, and `yarn` when pulling
dependencies through an artifact repository manager (e.g. Sonatype Nexus or
JFrog Artifactory). The primary use of `proxy_login` and `proxy_password` is
to authenticate with such a registry. Set `proxy_url` to its base URL. Do not
embed credentials in the URL.

| Key | Default | Description |
| --- | --- | --- |
| `proxy_url` | *(unset)* | Registry base URL |
| `proxy_login` | *(unset)* | Registry username (must be set together with `proxy_password`) |
| `proxy_password` | *(unset)* | Registry password (must be set together with `proxy_login`) |

### `bundler`

Supports the [proxy settings](#proxy-settings) above and has no additional
keys. Only rubygems registry dependencies are proxied; git and path
dependencies are unaffected.

### `gomod`

| Key | Default | Description |
| --- | --- | --- |
| `download_max_tries` | `5` | Maximum retry attempts for Go module commands |
| `environment_variables` | `{}` | Default environment variables for the gomod backend |

Go supports the [proxy settings](#proxy-settings) above. The default
`proxy_url` is `https://proxy.golang.org,direct`. When `proxy_login` is set,
`proxy_url` must be a single URL (not a comma-separated list).

### `pip`

| Key | Default | Description |
| --- | --- | --- |
| `ignore_dependencies_crates` | `false` | Legacy option for Rust-based pip dependencies; do not rely on it |

### `yarn`

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Legacy toggle for Yarn v3 and v4 processing; do not rely on it |

Yarn v3 and v4 also support the [proxy settings](#proxy-settings) above. Yarn
Classic ignores this section.

### `cargo`, `npm` and `pnpm`

These package managers support the [proxy settings](#proxy-settings) above and
have no additional keys.
