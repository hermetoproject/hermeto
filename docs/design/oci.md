# OCI artifacts design document

## Overview

The [OCI Distribution Specification](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)
defines an HTTP API for pushing and pulling container images and other content from registries.
Container images are the primary content type, but the same API is used to store
[arbitrary artifacts](https://github.com/opencontainers/image-spec/blob/main/manifest.md)
(signatures, SBOMs, Helm charts, Wasm modules, etc.).

This backend allows Hermeto to prefetch OCI container images from registries during the dependency
prefetch step, so that hermetic builds can consume them without network access. Support for arbitrary
OCI artifacts (ORAS-style custom media types) may be added in future work.

**Motivating use case**: build pipelines that consume base images or pre-built container images from
registries (e.g. Red Hat UBI images, NVIDIA CUDA images).
In a hermetic build environment, these images must be fetched in advance and made available on disk.

### Developer workflow

1. The developer identifies the OCI images or artifacts their build needs.
2. The developer resolves each reference to a digest using standard tooling:
   ```sh
   skopeo inspect --raw docker://registry.example.com/my-image:1.0 | sha256sum
   # or
   crane digest registry.example.com/my-image:1.0
   ```
3. The developer declares each dependency in `oci-images.lock.yaml` with its digest,
   repository, and media type.
4. Hermeto prefetches the declared images, producing an [OCI Image Layout](https://github.com/opencontainers/image-spec/blob/main/image-layout.md)
   directory for each.
5. The build environment consumes the layouts via `podman load`, `skopeo copy oci:...`, or
   `buildah from oci:...`.

### How OCI registries work

The OCI Distribution API exposes two key endpoints:

- `GET /v2/<name>/manifests/<reference>`: fetches a manifest (image configuration, list of layers,
   or multi-arch index).
- `GET /v2/<name>/blobs/<digest>`: fetches a content-addressable blob (layer tarball, config JSON).

All content is addressed by cryptographic digest (`sha256:<hex>`). A manifest lists the digests of
its blobs, so verifying the manifest digest implicitly verifies the integrity of the entire image.

Registries typically require authentication via the [Docker Token Authentication](https://distribution.github.io/distribution/spec/auth/token/)
flow: the client receives a `401` with a `WWW-Authenticate` challenge, exchanges credentials at a
token endpoint, and uses the resulting bearer token for subsequent requests.

## Design

### Scope

**In scope:**

- Fetching OCI images (single-arch and multi-arch) from any OCI-compliant registry
- OCI Image Manifests (`application/vnd.oci.image.manifest.v1+json`)
- Docker V2 Manifests (`application/vnd.docker.distribution.manifest.v2+json`)
- OCI Image Indexes / Docker Manifest Lists (selecting a single platform)
- Docker Token Authentication (anonymous and authenticated)
- Output as OCI Image Layout directories

**Out of scope (may be addressed in future work):**

- Basic auth for registries that do not use token auth
- OCI artifacts with arbitrary/custom media types (ORAS-style)
- Signature verification (cosign, Notary)
- Image attestation fetching
- Pushing images or artifacts to registries
- Building or modifying images

### Dependency list generation

#### Dependency list toolchain

Hermeto does not resolve image references to digests. The developer must provide a lockfile with
fully resolved digests. Standard tools to obtain digests:

- `skopeo inspect --raw docker://<image>:<tag>` (pipe through `sha256sum`)
- `crane digest <image>:<tag>`
- `podman inspect --format '{{.Digest}}' <image>:<tag>`

#### Dependency list format

The lockfile is YAML, named `oci-images.lock.yaml`.

```yaml
metadata:
  version: "1.0"
images:
  - repository: registry.redhat.io/rhel9/rhel-bootc
    digest: sha256:7c4e86de0c80e1c773e1a4e2dae5a6c2c42049e5e2f3d8fc7eeb38adcb0c711a
    media_type: application/vnd.oci.image.manifest.v1+json
    tag: "9.4"
    auth:
      basic:
        username: "$REGISTRY_USER"
        password: "$REGISTRY_PASS"

  - repository: ghcr.io/org/my-artifact
    digest: sha256:deadbeef...
    media_type: application/vnd.oci.image.index.v1+json
    platform:
      os: linux
      architecture: amd64
```

**Field reference:**

| Field | Required | Description |
|---|---|---|
| `repository` | yes | Full registry/repository path (e.g. `docker.io/library/alpine`) |
| `digest` | yes | Content-addressable digest (e.g. `sha256:abc...`). Immutable identifier used for fetching. |
| `media_type` | no | Media type of the manifest at the given digest. Defaults to `application/vnd.oci.image.manifest.v1+json`. |
| `platform` | no | Platform selector for multi-arch image indexes. Required when `digest` points to an image index. |
| `platform.os` | no | Operating system (default: `linux`) |
| `platform.architecture` | yes | CPU architecture (e.g. `amd64`, `arm64`) |
| `tag` | no | Human-readable tag. Informational only, used in SBOM output. Never used for fetching. |
| `auth` | no | Authentication credentials. Mutually exclusive `basic` or `bearer` blocks. Supports environment variable expansion (e.g. `$REGISTRY_PASS`). Same model as the generic fetcher's `LockfileArtifactAuth`. |

#### Checksum generation

OCI images are content-addressable by design. The `digest` field in the lockfile serves as both
the identifier and the checksum. Hermeto verifies:

1. The manifest bytes hash to the lockfile `digest`.
2. Each blob (config + layers) hashes to the digest declared in the manifest.

This provides a complete chain of trust: the lockfile pins the manifest, and the manifest pins every
blob. No separate checksum generation step is needed.

### Fetching content

#### Native vs. Hermeto fetch

Hermeto handles all fetching directly via the OCI Distribution HTTP API. No external tools
(skopeo, crane, podman) are invoked, consistent with the principle of avoiding arbitrary code
execution.

The OCI Distribution API is HTTP-based, so the existing `aiohttp` infrastructure in
`hermeto/core/package_managers/general.py` (`async_download_files`) can be reused for blob
downloads.

#### Fetch procedure

For each image entry in the lockfile:

1. **Authenticate**: Send `GET /v2/` to the registry. If a `401` response includes a
   `WWW-Authenticate: Bearer realm="...",service="...",scope="..."` header, exchange credentials
   at the realm URL. If the lockfile provides `basic` auth, include it on the token request. If no
   auth is configured, request an anonymous token (works for public registries).

2. **Fetch manifest**: `GET /v2/<name>/manifests/<digest>` with `Accept: <media_type>` and the
   bearer token. Compute `sha256` of the response body and verify it matches `digest` from the
   lockfile. Parse the manifest JSON.

3. **Handle image indexes**: If the manifest is an Image Index or Docker Manifest List, find the
   entry matching the specified `platform`. If no `platform` was specified, raise an error asking
   the user to set it. Fetch the platform-specific manifest by its digest (repeating step 2).

4. **Download blobs**: Extract the config descriptor and layer descriptors from the manifest. For
   each descriptor, build the download URL (`/v2/<name>/blobs/<digest>`) and the target path
   (`blobs/sha256/<hex>`). Download concurrently. After download, verify each file's sha256
   against its descriptor digest.

5. **Write OCI Image Layout**: Create the output directory with the standard structure.

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

The directory name is derived from the repository (replacing `/` and `:` with `_`) plus a 12-char
digest prefix to avoid collisions when the same repository appears at different digests
(e.g. `registry.redhat.io/rhel9/rhel-bootc@sha256:7c4e86...` becomes
`registry.redhat.io_rhel9_rhel-bootc_7c4e86de0c80`).

#### Network requirements

- **Registry endpoints**: Any OCI-compliant registry (Docker Hub, Quay, GHCR, registry.redhat.io, etc.)
- **Authentication**: Docker Token Authentication (automatic challenge-response). Credentials come
  from the lockfile's `auth` block with environment variable expansion.
- **Redirects**: Some registries (notably Docker Hub) redirect blob downloads to CDN URLs. The
  `aiohttp` client handles redirects with `requote_redirect_url=False` to preserve signed URLs.
- **Rate limiting**: Docker Hub imposes pull rate limits. The retry infrastructure handles 429
  responses.

### Build environment config

#### Environment variables

| Variable Name | Purpose | Example Value | Required |
|---|---|---|---|
| `HERMETO_OCI_LAYOUT_DIR` | Root directory containing all OCI Image Layout directories | `deps/x-oci` | Yes |

#### Configuration files

No additional configuration files are injected. The OCI Image Layout is self-contained and can be
consumed directly by container tools:

```sh
# Load into podman's local storage
podman load --oci-dir deps/x-oci/registry.redhat.io_rhel9_rhel-bootc

# Copy to a local registry or Docker daemon
skopeo copy oci:deps/x-oci/registry.redhat.io_rhel9_rhel-bootc docker-daemon:my-image:latest

# Use as a buildah source
buildah from oci:deps/x-oci/registry.redhat.io_rhel9_rhel-bootc
```

## Implementation notes

### Current limitations

- **No signature verification**: cosign and Notary signatures are not fetched or verified.
- **No attestation support**: SLSA provenance and other attestation artifacts are not fetched.
- **No custom media types**: Only standard OCI and Docker V2 image manifests are supported.
  ORAS-style artifacts with arbitrary media types are not yet handled.
- **Single lockfile per package input**: Each `x-oci` package input processes one lockfile. Multiple
  lockfiles require multiple package input entries.

## References

- [OCI Distribution Specification](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)
- [OCI Image Specification](https://github.com/opencontainers/image-spec/blob/main/spec.md)
- [OCI Image Layout](https://github.com/opencontainers/image-spec/blob/main/image-layout.md)
- [Docker Token Authentication](https://distribution.github.io/distribution/spec/auth/token/)
- [Package URL `pkg:oci` type](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#oci)
- [Hermeto issue #1641](https://github.com/hermetoproject/hermeto/issues/1641)
- [Hermeto issue #1080 (related, OCI URLs for RPMs)](https://github.com/hermetoproject/hermeto/issues/1080)
