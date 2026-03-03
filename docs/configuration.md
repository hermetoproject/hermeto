# Configuration

## Modes

Hermeto can be run in two modes using the global CLI option `--mode`.

- strict
- permissive

The default mode is `strict`. In this mode, *some* input requirements that are
not met are treated as errors. On the other hand, the `permissive` mode treats
them as warnings. This means that Hermeto will proceed and generate an SBOM, but
it may not be complete or accurate.

The permissive mode can currently suppress the following:

- go `vendor` directory inconsistencies (See [gomod](gomod.md) on vendoring
  information)
- cargo manifest file `Cargo.toml` is out of sync with `Cargo.lock`

## Settings

Settings can be provided via the following sources (highest priority first):

1. **Environment variables**: prefixed with `HERMETO_`, using `__` for nested
   settings (e.g., `HERMETO_GOMOD__DOWNLOAD_MAX_TRIES=10`)
2. **CLI option**: `--config-file path/to/config.yaml`
3. **Config files** (automatically loaded if present): `~/.config/hermeto/config.yaml`,
   `hermeto.yaml`, `.hermeto.yaml`

Any settings specified will override the default values present in the
[config.py][] module. The only supported format for config files is YAML.

- `gomod.download_max_tries` max retry attempts for go commands.
- `gomod.environment_variables` default environment variables for gomod.
- `gomod.proxy_url` sets the GOPROXY variable that Hermeto uses internally when
  downloading Go modules. See [Go environment variables][].
- `http.connect_timeout` connection timeout (seconds) for HTTP requests
  (default: 30).
- `http.read_timeout` read timeout (seconds) for HTTP requests (default: 300).
  Long-running downloads can take arbitrarily long as long as bytes keep
  flowing.
- `http.timeout` (deprecated) automatically migrated to `http.read_timeout`.
- `runtime.concurrency_limit` max concurrent operations.
- `runtime.subprocess_timeout` timeout (seconds) for subprocess commands.

[config.py]: https://github.com/hermetoproject/hermeto/blob/main/hermeto/core/config.py
[Go environment variables]: https://go.dev/ref/mod#environment-variables
