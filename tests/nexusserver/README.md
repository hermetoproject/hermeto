# Nexus Repository Server

Manages a local [Sonatype Nexus](https://www.sonatype.com/products/sonatype-nexus-repository) container for integration testing. Nexus acts as a caching proxy between hermeto and upstream registries (e.g., npmjs.org), allowing tests to verify that hermeto produces correct SBOMs when fetching dependencies through a proxy.

## Usage

### Standalone

```bash
python tests/nexusserver/start.py
```

Starts a Nexus container, configures it, and waits for Ctrl+C. Useful for manual testing and debugging. All options can be set via CLI flags or environment variables:

| CLI flag | Env var | Default |
|---|---|---|
| `--image` | `NEXUS_IMAGE` | `docker.io/sonatype/nexus3:latest` |
| `--host` | `NEXUS_HOST` | `127.0.0.1` |
| `--port` | `NEXUS_PORT` | `8082` |
| `--admin-password` | `NEXUS_ADMIN_PASSWORD` | `admin123` |
| `--container-name` | `NEXUS_CONTAINER_NAME` | `hermeto-nexus` |
| `--volume-name` | `NEXUS_VOLUME_NAME` | `hermeto-nexus-data` |
| `--startup-timeout` | `NEXUS_STARTUP_TIMEOUT` | `300` |
| `--subprocess-timeout` | `NEXUS_SUBPROCESS_TIMEOUT` | `300` |
| `--http-timeout` | `NEXUS_HTTP_TIMEOUT` | `10` |
| `--log-level` | `NEXUS_LOG_LEVEL` | `INFO` |
