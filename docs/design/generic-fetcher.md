# Generic Fetcher Design Document

## Overview

The generic fetcher is a Hermeto package-manager backend for downloading
arbitrary files that do not fit within an established package ecosystem Hermeto
could otherwise support. The target audience is users who want Hermeto for
hermetic builds and also need an easy way to include these files, with Hermeto
accounting for them in the SBOM it produces.

Unlike other Hermeto backends, there is no external package manager to integrate
with. Hermeto defines the lockfile format, downloads the declared artifacts
itself, and places them under the output directory for the build to consume.

### Developer Workflow

1. **Prerequisites**: No external package-manager CLI is required. Developers
   author an `artifacts.lock.yaml` lockfile (or supply an alternate path via the
   Hermeto JSON input `lockfile` key).
2. **Adding dependencies**: Add an entry under `artifacts` with a
   `download_url`, a required `checksum`, and an optional `filename`. For
   private resources, add an `auth` block with authentication credentials;
   bump `metadata.version` to `"2.0"` when using `auth`.
3. **Dependency management**: Developers maintain the lockfile by hand (or with
   their own tooling). Hermeto does not resolve or update dependencies.

#### Example: Hermeto invocation

```shell
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  '{"type": "generic"}'
```

#### Example: Mixed-source authenticated fetch

A lockfile mixing private and public artifacts with bearer token
authentication. Note the `version: "2.0"` bump required when using `auth`.

```yaml
# artifacts.lock.yaml
metadata:
  version: "2.0"
artifacts:
  # Private artifact from GitLab (custom header)
  - download_url: "https://gitlab.example.com/api/v4/projects/123/repository/archive.tar.gz"
    checksum: "sha256:..."
    auth:
      bearer:
        header: PRIVATE-TOKEN
        value: "$GITLAB_TOKEN"

  # Public artifact (no auth needed, .netrc used if available)
  - download_url: "https://example.com/public-file.zip"
    checksum: "sha256:..."

  # Private artifact from GitHub (standard Bearer token)
  - download_url: "https://api.github.com/repos/owner/repo/tarball/v1.0.0"
    checksum: "sha256:..."
    auth:
      bearer:
        value: "Bearer $GITHUB_TOKEN"
```

```bash
export GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
export GITHUB_TOKEN="github_pat_xxxxxxxxxxxxxxxxxxxxx"
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  '{"type": "generic"}'
```

### Authentication

Many artifact hosting services require authentication to access private
resources. The sections below describe the authentication schemes relevant
to Hermeto's generic fetcher and the level of Hermeto's involvement in each.

Hermeto currently supports:

- HTTP Basic authentication (via `.netrc`)
- HTTP Bearer token (via per-artifact `auth` block in the lockfile)

The following schemes are out of scope for Hermeto to implement directly:

- OAuth2
- OIDC

#### HTTP Basic Auth

A simple authentication mechanism defined in [RFC 7617][rfc-7617]. HTTP
Basic Auth transmits credentials as a Base64-encoded `username:password`
pair in the `Authorization` header. It **must** be used with TLS due to
the severe security design flaw that credentials are transmitted in
plaintext. Even with TLS, credentials are typically long-lived and not
scoped to specific resources, which is why this method is being gradually
phased out in favour of bearer token authentication. The `.netrc` file
format provides a convenient way to store HTTP Basic Auth credentials and
Hermeto supports this implicitly. Major platforms currently supporting
HTTP Basic Auth are listed below.

| Platform | Username | Password | Source |
|----------|----------|----------|--------|
| Bitbucket | email address | API token | [Bitbucket docs][bitbucket-auth] |
| Gitea | username | access token | [Gitea API docs][gitea-auth] |
| Hugging Face[^1] | username | access token | [Hugging Face docs][huggingface-auth] |
| Sonatype Nexus | token name | token passcode | [Nexus docs][nexus-auth] |
| GitLab[^2] | username | Personal Access Token | [GitLab PAT docs][gitlab-pat-docs] |

#### Bearer Token Auth

Bearer token authentication transmits an opaque token in the
`Authorization` HTTP header, typically prefixed with the `Bearer` string.
Defined in [RFC 6750][rfc-6750] as part of OAuth 2.0, it has become the
de facto standard for API authentication. Unlike HTTP Basic Auth, bearer
tokens are usually short-lived and can be scoped to specific permissions
or resources. Major platforms currently supporting bearer token
authentication are listed below.

| Platform | Header | Value Format | Source |
|----------|--------|--------------|--------|
| GitLab[^2] | `PRIVATE-TOKEN` | `<token>` | [GitLab REST API docs][gitlab-auth] |
| GitLab | `Authorization` | `Bearer <token>` | [GitLab REST API docs][gitlab-auth] |
| GitHub | `Authorization` | `Bearer <token>` | [GitHub REST API docs][github-auth] |
| Gitea | `Authorization` | `token <token>` | [Gitea API docs][gitea-auth] |
| Hugging Face[^1] | `Authorization` | `Bearer <token>` | [Hugging Face docs][huggingface-auth] |
| JFrog Artifactory | `Authorization` | `Bearer <token>` | [JFrog docs][artifactory-auth] |
| Google Artifact Registry | `Authorization` | `Bearer <token>` | [Google Cloud docs][google-auth] |
| RubyGems | `Authorization` | `<api-key>` | [RubyGems docs][rubygems-auth] |

[^1]: Hugging Face documents bearer tokens specifically for *Inference
    Providers*. For general Hub access (model/dataset downloads), HTTP
    Basic Auth is mentioned.

[^2]: GitLab supports HTTP Basic Auth for Git operations ([clone, push,
    pull][gitlab-pat-usage]), but the REST API (including archive
    downloads) only accepts
    [header-based credentials](https://github.com/hermetoproject/hermeto/issues/1224#issuecomment-3728235587).

#### OAuth2

OAuth2 is a complete authorization framework defined in
[RFC 6749][rfc-6749] that enables third-party applications to obtain
limited access to HTTP services. It defines several authorization flows (called "grants"),
including the [Authorization Code Grant][rfc-6749-4-1], which requires
interactive browser-based user consent, and the
[Client Credentials Grant][rfc-6749-4-4] for machine-to-machine
communication both of which are, in principle, the most relevant to Hermeto's use case. However, Hermeto cannot implement OAuth2 flows directly for the
following reasons:

1. They are **credential acquisition** mechanisms — they define how
   tokens are obtained, not how they are attached to requests.
2. The *Authorization Code Grant* is interactive (requiring a browser
   redirect and user input), while the *Client Credentials Grant* would
   require Hermeto to contact a token issuer endpoint to obtain a token before using it —
   and that functionality is out of scope for the project.

#### OIDC

[OpenID Connect][oidc-spec] (OIDC) is an identity layer built on top of OAuth2. At its core, OIDC allows applications to delegate authentication to a trusted third-party Identity Provider (IdP) rather than verifying credentials directly. When a user authenticates with the IdP, the application receives a signed ID token (a JWT) containing standardized identity claims. This enables single sign-on (SSO) across multiple applications and separates identity management from application logic.

In CI/CD contexts, OIDC powers "Trusted Publishing" workflows used by [GitHub Actions][github-oidc],
[GitLab CI][gitlab-oidc], etc., allowing jobs to authenticate to external services without stored secrets. The CI platform acts as the IdP, issuing tokens that assert the workflow's identity (repository, branch, job name), which target services can verify and exchange for short-lived access tokens.

Hermeto cannot implement OIDC directly because it would require detecting
which CI environment it is running on, implementing provider-specific OIDC
token endpoint support, and handling service-specific token exchange APIs
for each target platform. Users acquire tokens externally and supply them
to Hermeto via the `auth` block in the lockfile.

#### Integrating with aiohttp

Bearer token support is implemented by extending the `artifacts.lock.yaml`
schema with an optional per-artifact `auth` block rather than extending the
input JSON. The generic backend has a fundamentally different requirement —
per-URL authentication — that the input JSON cannot express, since it only
supports per-host configuration. Other backends do not share this need.

Integrating with the `aiohttp` library Hermeto uses is straightforward.
The [aiohttp docs][aiohttp-custom-headers] cover explicit header injection
directly:

```python
headers = {"Authorization": "Bearer eyJh...0M30"}
async with ClientSession(headers=headers) as session:
    ...
```

A simplified implementation:

1. Resolve the `auth` configuration map for each artifact in the lockfile
2. Read the referenced environment variables and populate the header name
   (`header` field) and value (`value` field)
3. Inject the resulting headers into the HTTP request

### How the Backend Works

- **Registry/repository model**: N/A.
- **Dependency resolution**: N/A.
- **Configuration options**: Lockfile path defaults to `artifacts.lock.yaml`
  relative to the package `path`, override it via the JSON input `lockfile` key.

#### Project Structure

Developer project (typical):

```
project.git/
├── artifacts.lock.yaml
└── ...
```

Hermeto output after `fetch-deps`:

```
hermeto-output/
└── deps/
    └── generic/
        └── <filename or URL-derived name>
```

## Design

The generic fetcher works by reading a lockfile that lists the artifacts to
download, fetching each one over HTTP(S), verifying its checksum, and writing
it to the output directory. There is no dependency resolution. Hermeto only
downloads what the lockfile explicitly declares. Credential acquisition flows
such as OAuth2 and OIDC are also out of scope.

### Dependency List Format

```yaml
# artifacts.lock.yaml
metadata:
  version: "1.0"          # required; bump to "2.0" when using auth

artifacts:
  - download_url: <url>   # required; HTTP(S) URL of the artifact
    checksum: <alg:hash>  # required; e.g. sha256:abc123...
    filename: <name>      # optional; defaults to the last path segment of the URL
    auth:                 # optional; omit for public resources or when using .netrc
      bearer:             # bearer OR basic
        header: <name>    # optional; defaults to Authorization
        value: <token>    # required; supports $VAR env var interpolation
```

`metadata.version` uses a major/minor scheme so readers can detect incompatible
lockfile changes. A concrete example:

```yaml
metadata:
  version: "1.0"
artifacts:
  - download_url: https://huggingface.co/instructlab/granite-7b-lab/resolve/main/model-00001-of-00003.safetensors?download=true
    filename: granite-model-1.safetensors
    checksum: sha256:d16bf783cb6670f7f692ad7d6885ab957c63cfc1b9649bc4a3ba1cfbdfd5230c
```

#### download_url (required)

A string containing the download URL of the artifact.

#### checksum (required)

A string in the format `algorithm:hash`. Must be provided to ensure the identity
of the artifact.

#### filename (optional)

Provided for user convenience so files end up in expected locations. If not provided,
it is derived from the `download_url`. The filename can also contain a relative
path inside Hermeto's output directory for the generic fetcher
(`{hermeto-output-dir}/deps/generic`). Hermeto verifies that resulting
filenames, including those derived from download URLs, do not overlap.

#### auth (optional)

Not needed for public resources. HTTP Basic Auth credentials can also be
supplied implicitly via `.netrc` without using this block; the explicit
`basic` sub-key is an alternative for cases where `.netrc` is not practical.

An optional per-artifact block that configures HTTP authentication for
private resources. When present, the lockfile must declare
`metadata.version: "2.0"` — a version bump is required because Pydantic
validates the schema strictly.

The `auth` map contains exactly one key identifying the authentication
type. The currently supported types are `bearer` and `basic`.

Schema structure:

```yaml
auth:
  <auth-type>:
    <credential-field>: <value>
```

**`basic`** — HTTP Basic authentication via explicit credentials:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `username` | string | Yes | Username; supports `$VAR` environment variable interpolation |
| `password` | string | Yes | Password or token; supports `$VAR` environment variable interpolation |

**`bearer`** — HTTP header-based token authentication:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `header` | string | No | HTTP header name. Defaults to `Authorization` |
| `value` | string | Yes | Header value; supports `$VAR` environment variable interpolation |

The secret fields (`username`, `password`, `value`) use shell-like `$VAR`
syntax. Hermeto fails with a clear error if any referenced environment
variable is unset.

Full lockfile example with authentication:

```yaml
metadata:
  version: "2.0"
artifacts:
  - download_url: "https://gitlab.example.com/api/v4/projects/123/repository/archive.tar.gz"
    checksum: "sha256:abc123..."
    auth:
      bearer:
        header: PRIVATE-TOKEN
        value: "$GITLAB_TOKEN"
```

Per-type `auth` block examples:

**GitLab** (custom `PRIVATE-TOKEN` header):

```yaml
auth:
  bearer:
    header: PRIVATE-TOKEN
    value: "$GITLAB_TOKEN"
```

**GitHub / most platforms** (standard `Authorization: Bearer` header):

```yaml
auth:
  bearer:
    value: "Bearer $GITHUB_TOKEN"
```

**Gitea** (non-standard `token` prefix):

```yaml
auth:
  bearer:
    value: "token $GITEA_TOKEN"
```

### Checksum Generation

Since there is no upstream package manager to supply checksums, developers
compute or obtain them before writing the lockfile and declare them in
`algorithm:hash` form (e.g. `sha256:...`). Checksums are mandatory. Hermeto
rejects any artifact entry that is missing one.

### File Formats and Metadata

- **Package file formats**: Opaque; any file reachable by URL
- **Naming conventions**: Prefer explicit `filename`; otherwise derive from URL.
- **Version handling**: No version directory layout; each artifact is a single
  file path under `deps/generic/`

### SBOM Components

Artifacts fetched with the generic fetcher are recorded in the SBOM that Hermeto
produces. Given that the lockfile provides a download location, filename, and checksum,
they are always recorded as SBOM components with a purl of type `generic`.

Additionally, the SBOM component contains an [externalReferences] entry of type
`distribution` pointing to the download URL. Without this, the source URL is
only available as a query parameter inside the PURL, which is non-standard for
tooling to rely on. The `externalReferences` field gives vulnerability scanners,
provenance trackers, and other SBOM consumers a dedicated, standardized location
to find where each artifact was fetched from.

Example SBOM for the artifact above:

```json
{
  "bomFormat": "CycloneDX",
  "components": [
    {
      "name": "granite-model-1.safetensors",
      "purl": "pkg:generic/granite-model-1.safetensors?checksum=sha256:d16bf783cb6670f7f692ad7d6885ab957c63cfc1b9649bc4a3ba1cfbdfd5230c&download_url=https://huggingface.co/instructlab/granite-7b-lab/resolve/main/model-00001-of-00003.safetensors",
      "properties": [
        {
          "name": "hermeto:found_by",
          "value": "hermeto"
        }
      ],
      "type": "file",
      "externalReferences": [
        {
          "url": "https://huggingface.co/instructlab/granite-7b-lab/resolve/main/model-00001-of-00003.safetensors",
          "type": "distribution"
        }
      ]
    }
  ],
  "metadata": {
    "tools": [
      {
        "vendor": "red hat",
        "name": "hermeto"
      }
    ]
  },
  "specVersion": "1.6",
  "version": 1
}
```

## Potential Future Extensions For Authentication

### Input JSON Authentication

For backends with homogeneous sources (single registry/index), an input JSON
approach may be appropriate if authentication to private resources is needed:

```json
{
  "packages": [
    {
      "type": "pip",
      "path": ".",
      "options": {
        "auth": {
          "bearer": {
            "value": "Bearer $PRIVATE_PYPI_TOKEN"
          }
        }
      }
    }
  ]
}
```

### AWS Signature Version 4

[AWS Signature Version 4][aws-sigv4] (`AWS4-HMAC-SHA256`) is a more complex
authentication scheme used by AWS. Unlike bearer tokens, it requires:

- Request-specific signature computation (method, path, headers, timestamp)
- AWS credentials (access key ID + signing key)
- Region and service identifiers

The proposed schema could theoretically support this:

```yaml
auth:
  aws4:
    region: us-east-1
    service: s3
    access_key_id: "$AWS4_ACCESS_KEY_ID"
    signing_key: "$AWS4_SIGNING_KEY"
```

AWS Signature Version 4 is significantly more complex than header
injection — it requires computing HMAC-SHA256 signatures over canonicalized
request data, which would likely require implementing a signing algorithm
in Hermeto. If demand warrants it, `aws4-hmac-sha256` could be added as a
future `auth` type.

### Other HTTP Auth Schemes

IANA defines a number of [HTTP auth schemes][iana-auth-schemes] users might
find useful to have supported.

[externalReferences]: https://cyclonedx.org/docs/1.6/json/#components_items_externalReferences
[rfc-7617]: https://datatracker.ietf.org/doc/html/rfc7617
[rfc-6749]: https://datatracker.ietf.org/doc/html/rfc6749
[rfc-6749-4-1]: https://datatracker.ietf.org/doc/html/rfc6749#section-4.1
[rfc-6749-4-4]: https://datatracker.ietf.org/doc/html/rfc6749#section-4.4
[rfc-6750]: https://datatracker.ietf.org/doc/html/rfc6750
[gitlab-auth]: https://docs.gitlab.com/api/rest/authentication/
[gitlab-pat-docs]: https://docs.gitlab.com/user/profile/personal_access_tokens/
[gitlab-pat-usage]: https://docs.gitlab.com/user/profile/personal_access_tokens/#clone-repository-using-personal-access-token
[github-auth]: https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api
[bitbucket-auth]: https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/
[gitea-auth]: https://docs.gitea.com/development/api-usage#authentication
[huggingface-auth]: https://huggingface.co/docs/hub/en/security-tokens#how-to-use-user-access-tokens
[artifactory-auth]: https://jfrog.com/help/r/jfrog-platform-administration-documentation/authorization-headers
[nexus-auth]: https://help.sonatype.com/en/user-tokens.html
[google-auth]: https://docs.cloud.google.com/artifact-registry/docs/repositories/download-files#api
[rubygems-auth]: https://guides.rubygems.org/rubygems-org-api/
[oidc-spec]: https://openid.net/developers/how-connect-works/
[github-oidc]: https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-security-hardening-with-openid-connect
[gitlab-oidc]: https://docs.gitlab.com/ci/secrets/id_token_authentication/
[aiohttp-custom-headers]: https://docs.aiohttp.org/en/stable/client_advanced.html#custom-request-headers
[aws-sigv4]: https://docs.aws.amazon.com/AmazonS3/latest/API/sigv4-auth-using-authorization-header.html
[iana-auth-schemes]: https://www.iana.org/assignments/http-authschemes/http-authschemes.xhtml