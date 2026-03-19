# Nexus Repository Server

Manages a local [Sonatype Nexus](https://www.sonatype.com/products/sonatype-nexus-repository) container for integration testing. Nexus acts as a caching proxy between hermeto and upstream registries (e.g., npmjs.org), allowing tests to verify that hermeto produces correct SBOMs when fetching dependencies through a proxy.

## Usage

### As part of integration tests

Set `HERMETO_TEST_LOCAL_NEXUS_PROXY=1` to enable local Nexus proxy mode (done automatically by `nox -s all-integration-tests`).

### Standalone

```bash
bash tests/nexusserver/run.sh
```

Starts Nexus via podman-compose, configures it, and attaches to logs. Press Ctrl+C to stop and clean up. Configuration options for `start.py` can be set via CLI flags or environment variables:

| CLI flag | Env var | Default |
|---|---|---|
| `--host` | `NEXUS_HOST` | `127.0.0.1` |
| `--port` | `NEXUS_PORT` | `8082` |
| `--admin-password` | `NEXUS_ADMIN_PASSWORD` | `admin123` |
| `--container-name` | `NEXUS_CONTAINER_NAME` | `hermeto-nexus` |
| `--startup-timeout` | `NEXUS_STARTUP_TIMEOUT` | `300` |
| `--http-timeout` | `NEXUS_HTTP_TIMEOUT` | `10` |
| `--log-level` | `NEXUS_LOG_LEVEL` | `INFO` |
