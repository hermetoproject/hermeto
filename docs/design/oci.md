# OCI artifacts design document

## Overview

The [OCI Distribution Specification](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)
defines an HTTP API for pushing and pulling content from registries. The same API serves container
images and [arbitrary artifacts](https://github.com/opencontainers/image-spec/blob/main/manifest.md)
(signatures, SBOMs, Helm charts, Wasm modules, ML models, etc.). This backend allows Hermeto to
prefetch any of these during the dependency prefetch step, so that hermetic builds can consume them
without network access.

**Motivating use case**: build pipelines that need OCI artifacts at build time. A build might need
a Helm chart to template, a Wasm plugin to embed, a base image to layer on top of, or any other
content stored in an OCI registry. In a hermetic build environment, none of these are reachable
over the network, so they must be prefetched and made available on disk.

### Developer workflow

1. The developer identifies the OCI images or artifacts their build needs.
2. The developer resolves each reference to a digest using standard tooling:
   ```sh
   skopeo inspect --raw docker://registry.example.com/my-image:1.0 | sha256sum
   # or
   crane digest registry.example.com/my-image:1.0
   ```
3. The developer declares each dependency in `oci-images.lock.yaml` with its digest
   and repository.
4. Hermeto prefetches the declared images, producing an [OCI Image Layout](https://github.com/opencontainers/image-spec/blob/main/image-layout.md)
   directory for each.
5. The build environment consumes the layouts via `skopeo copy oci:...`,
   `buildah from oci:...`, or similar OCI-aware tooling.

### How OCI registries work

The OCI Distribution API exposes two key endpoints:

- `GET /v2/<name>/manifests/<reference>`: fetches a manifest (image configuration, list of layers,
   or multi-arch index).
- `GET /v2/<name>/blobs/<digest>`: fetches a content-addressable blob (layer tarball, config JSON).

All content is addressed by cryptographic digest (`sha256:<hex>`). A manifest lists the digests of
its blobs. Verifying the manifest digest confirms the manifest is authentic, and the blob digests
listed inside it become the expected checksums. Each blob must still be independently verified
against its descriptor digest after download (see [Checksum generation](#checksum-generation)).

Registries typically require authentication via the [Docker Token Authentication](https://distribution.github.io/distribution/spec/auth/token/)
flow: the client receives a `401` with a `WWW-Authenticate` challenge, exchanges credentials at a
token endpoint, and uses the resulting bearer token for subsequent requests.

## Design

### Scope

**In scope:**

- Fetching OCI images (single-arch and multi-arch) and arbitrary OCI artifacts from any
  OCI-compliant registry
- OCI Image Manifests (`application/vnd.oci.image.manifest.v1+json`)
- Docker V2 Manifests (`application/vnd.docker.distribution.manifest.v2+json`)
- OCI Image Indexes / Docker Manifest Lists (selecting a single platform)
- Arbitrary media types (the client accepts whatever the registry returns)
- Docker Token Authentication (anonymous and authenticated)
- HTTP Basic Authentication for registries that do not use token auth
- Output as OCI Image Layout directories

### Dependency list generation

#### Dependency list toolchain

Hermeto does not resolve image references to digests. The developer must provide a lockfile with
fully resolved digests. How to obtain digests is documented in user-facing docs, not here.

#### Dependency list format

The lockfile is YAML, named `oci-images.lock.yaml`.

> **Note**: The team is discussing whether non-packaging backends (generic, OCI, etc.) should
> share a single lockfile (`artifacts.lock.yaml`) with a type discriminator. This design proposes
> a separate file for now; the lockfile format may change based on that discussion.

```yaml
metadata:
  version: "1.0"
images:
  - repository: docker.io/library/alpine
    digest: sha256:a8560b36e8b8210634f77d9f7f9efd7ffa463e380b75e2e74aff4511df3ef88c
    tag: "3.21"
    media_type: application/vnd.oci.image.index.v1+json
    platform:
      os: linux
      architecture: arm64
      variant: v8
    auth:
      basic:
        username: "$REGISTRY_USER"
        password: "$REGISTRY_PASS"

  - repository: ghcr.io/org/my-artifact
    digest: sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
    platform:
      os: linux
      architecture: amd64
```

**Field reference:**

| Field | Required | Description |
|---|---|---|
| `repository` | yes | Full registry/repository path (e.g. `docker.io/library/alpine`) |
| `digest` | yes | Content-addressable SHA-256 digest (`sha256:<hex>`). Immutable identifier used for fetching. Only SHA-256 is supported. |
| `media_type` | no | Media type hint for content negotiation. When provided, used as the `Accept` header. When omitted, the client sends all supported manifest types (`application/vnd.oci.image.manifest.v1+json`, `application/vnd.oci.image.index.v1+json`, `application/vnd.docker.distribution.manifest.v2+json`, `application/vnd.docker.distribution.manifest.list.v2+json`) and determines the actual type from the response `Content-Type`. |
| `platform` | no | Platform selector for multi-arch image indexes. Required when `digest` points to an image index. |
| `platform.os` | no | Operating system (default: `linux`) |
| `platform.architecture` | yes | CPU architecture (e.g. `amd64`, `arm64`) |
| `platform.variant` | no | CPU variant (e.g. `v8` for arm64). Used to disambiguate image index entries that share the same os/architecture but differ by variant. |
| `tag` | no | Human-readable tag. Informational only, used in SBOM output. Never used for fetching. |
| `auth` | no | Authentication credentials. Mutually exclusive `basic` or `bearer` blocks. `basic` credentials are sent on the token exchange request (step 1 of the fetch procedure). `bearer` provides a pre-obtained token used directly as `Authorization: Bearer <token>`, skipping the token exchange. Supports environment variable expansion (e.g. `$REGISTRY_PASS`). Reuses the existing `LockfileArtifactAuth` model. |

#### Checksum generation

OCI images are content-addressable by design. The `digest` field in the lockfile serves as both
the identifier and the checksum. Hermeto verifies:

1. The manifest bytes hash to the lockfile `digest`.
2. Each blob (config + layers) hashes to the digest declared in the manifest.

This provides a complete chain of trust: the lockfile pins the manifest, and the manifest pins every
blob. No separate checksum generation step is needed.

### Fetching content

#### Fetch implementation

Hermeto fetches directly via the OCI Distribution HTTP API. No external tools (skopeo, crane,
podman) are invoked. The existing `aiohttp` infrastructure in
`hermeto/core/package_managers/general.py` (`async_download_files`) is reused for blob downloads.

#### Fetch procedure

For each image entry in the lockfile:

1. **Authenticate**: Send `GET /v2/` to the registry. If a `401` response includes a
   `WWW-Authenticate: Bearer realm="...",service="...",scope="..."` header, exchange credentials
   at the realm URL. If the lockfile provides `basic` auth, include it on the token request. If
   `bearer` auth is configured, use the provided token directly, skipping the exchange. If no
   auth is configured, request an anonymous token (works for public registries). If the registry
   returns `200` with no challenge (e.g. a private registry behind a VPN), skip token exchange
   entirely and proceed without an `Authorization` header.

2. **Fetch manifest**: `GET /v2/<name>/manifests/<digest>` with the bearer token. If the lockfile
   provides `media_type`, use it as the `Accept` header; otherwise send all supported manifest
   types. Compute `sha256` of the response body and verify it matches `digest` from the lockfile.
   Determine the manifest type from the response `Content-Type` header. Parse the manifest JSON.

3. **Handle image indexes**: If the manifest is an Image Index or Docker Manifest List, find the
   entry matching the specified `platform`. If no `platform` was specified, raise an error asking
   the user to set it. Fetch the platform-specific manifest by its digest, using the child
   descriptor's `mediaType` from the index entry as the `Accept` header (not the parent index type).

4. **Download blobs**: Extract the config descriptor and layer descriptors from the manifest.
   Deduplicate by digest (two descriptors can reference the same blob). For each unique descriptor,
   build the download URL (`/v2/<name>/blobs/<digest>`) and the target path
   (`blobs/<algorithm>/<hex>`). Download to a temporary file, verify its hash against the
   descriptor digest (`sha256`), and
   atomically rename to the final path. Downloads run concurrently, limited by a single semaphore
   that caps the total number of in-flight blob downloads across all images being fetched. The
   limit is configurable via Hermeto's `concurrency_limit` runtime setting.

5. **Write OCI Image Layout**: Write the manifest bytes to `blobs/<algorithm>/<hex>` (the same
   digest verified in step 2). Create `index.json` referencing this manifest blob. Write the
   `oci-layout` file (`{"imageLayoutVersion": "1.0.0"}`). The config and layer blobs are already
   in place from step 4.

#### Project structure (output)

For each image, Hermeto produces an [OCI Image Layout](https://github.com/opencontainers/image-spec/blob/main/image-layout.md)
directory:

```
deps/x-oci/<sanitized-repository>/
  oci-layout                    # {"imageLayoutVersion": "1.0.0"}
  index.json                    # references the fetched manifest
  blobs/
    sha256/
      <manifest-digest-hex>     # image manifest JSON
      <config-digest-hex>       # image config JSON
      <layer1-digest-hex>       # layer tarball
      <layer2-digest-hex>       # layer tarball
      ...
```

The directory name is derived from the repository path by replacing `/` with `_` and appending a
12-char digest hex prefix (e.g. `docker.io_library_alpine_a8560b36e8b8`). Since this
replacement is not injective (e.g. `a/b_c` and `a_b/c` produce the same string), the lockfile
parser validates that all entries produce unique directory names and rejects the lockfile with a
clear error if a collision is detected.

#### Network requirements

- **Registry endpoints**: Any OCI-compliant registry (Docker Hub, Quay, GHCR, etc.)
- **Authentication**: Docker Token Authentication (automatic challenge-response). Credentials come
  from the lockfile's `auth` block with environment variable expansion.
- **Redirects**: Some registries (notably Docker Hub) redirect blob downloads to CDN URLs. The
  client follows redirects with `requote_redirect_url=False` to preserve signed URLs. Per the
  OCI Distribution Spec, `Authorization` headers are stripped on cross-host redirects to prevent
  credential leakage to CDN endpoints.
- **Rate limiting**: Docker Hub imposes pull rate limits. The retry infrastructure handles 429
  responses.

### SBOM representation

Each fetched image is reported as an SBOM component using the
[`pkg:oci` PURL type](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#oci).

**PURL mapping:**

| PURL field | Source | Example |
|---|---|---|
| `type` | fixed | `oci` |
| `name` | last segment of `repo_path` | `alpine` |
| `version` | digest of the fetched manifest (child digest for multi-arch, lockfile digest for single-arch) | `sha256:a8560b36e8b8` |
| `qualifiers.repository_url` | `{registry}/{repo_path}` | `docker.io/library/alpine` |
| `qualifiers.arch` | platform architecture (if platform was specified) | `amd64` |
| `qualifiers.tag` | lockfile `tag` (if present) | `3.21` |

The `pkg:oci` type spec forbids a `namespace` component. The full repository path (including
registry) goes into the `repository_url` qualifier. For multi-arch images, the `version` field
uses the selected child manifest digest (not the parent index digest), so that different platform
fetches produce distinct SBOM components.

Example PURL (percent-encoded per the PURL spec):
```
pkg:oci/alpine@sha256%3Aa8560b36e8b8210634f77d9f7f9efd7ffa463e380b75e2e74aff4511df3ef88c?repository_url=docker.io%2Flibrary%2Falpine&arch=amd64&tag=3.21
```

The SBOM is emitted as part of Hermeto's standard request output. Because `x-oci` is an
experimental backend, all components are annotated with `hermeto:backend:experimental:x-oci`.

## References

- [OCI Distribution Specification](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)
- [OCI Image Specification](https://github.com/opencontainers/image-spec/blob/main/spec.md)
- [OCI Image Layout](https://github.com/opencontainers/image-spec/blob/main/image-layout.md)
- [Docker Token Authentication](https://distribution.github.io/distribution/spec/auth/token/)
- [Package URL `pkg:oci` type](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#oci)
- [Hermeto issue #1641](https://github.com/hermetoproject/hermeto/issues/1641)
- [Hermeto issue #1080 (related, OCI URLs for RPMs)](https://github.com/hermetoproject/hermeto/issues/1080)
