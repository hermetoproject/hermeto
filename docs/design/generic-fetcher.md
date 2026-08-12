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
communication both of which are, in principle, the most relevant to Hermeto's use case.
However, Hermeto cannot implement OAuth2 flows directly for the following reasons:

1. They are **credential acquisition** mechanisms — they define how
   tokens are obtained, not how they are attached to requests.
2. The *Authorization Code Grant* is interactive (requiring a browser
   redirect and user input), while the *Client Credentials Grant* would
   require Hermeto to contact a token issuer endpoint to obtain a token before using it —
   and that functionality is out of scope for the project.

#### OIDC

[OpenID Connect][oidc-spec] (OIDC) is an identity layer built on top of OAuth2.
At its core, OIDC allows applications to delegate authentication to a trusted
third-party Identity Provider (IdP) rather than verifying credentials directly.
When a user authenticates with the IdP, the application receives a
signed ID token (a JWT) containing standardized identity claims.
This enables single sign-on (SSO) across multiple applications and separates
identity management from application logic.

In CI/CD contexts, OIDC powers "Trusted Publishing" workflows used by [GitHub Actions][github-oidc],
[GitLab CI][gitlab-oidc], etc., allowing jobs to authenticate to external services without stored
secrets. The CI platform acts as the IdP, issuing tokens that assert the workflow's identity
(repository, branch, job name), which target services can verify and exchange for short-lived access
tokens.

Hermeto cannot implement OIDC directly because it would require detecting
which CI environment it is running on, implementing provider-specific OIDC
token endpoint support, and handling service-specific token exchange APIs
for each target platform. Users acquire tokens externally and supply them
to Hermeto via the `auth` block in the lockfile.

## Design

The generic fetcher works by reading a lockfile that lists the artifacts to
download, fetching each one over HTTP(S), verifying its checksum, and writing
it to the output directory. There is no dependency resolution. Hermeto only
prefetch what the lockfile explicitly declares.

### Lockfile Format

Hermeto expects the lockfile to be named `artifacts.lock.yaml` and located in the project root (or
supply an alternate path via the Hermeto JSON input `lockfile` key). To accommodate potential future
breaking changes, the lockfile MUST contain all fields required by the applicable lockfile format.

#### Lockfile - v1

The lockfile will contain a metadata section with a version field that will indicate the version of
the lockfile format. It will also contain a list of artifacts (files) to download, each of the
artifacts to having a URL, a checksum, and optionally output filename specified.

<details>
<summary>Lockfile v1 schema</summary>

```yaml
# artifacts.lock.yaml
metadata:
  version: "1.0"          # required; major/minor format

artifacts:
  - download_url: <url>   # required; HTTP(S) URL of the artifact
    checksum: <alg:hash>  # required; e.g. sha256:abc123...
    filename: <name>      # optional; defaults to the last path segment of the URL
```

</details>

#### Lockfile - v2

Version `"2.0"` retains all fields defined in version "1.0" and introduces the auth block. When
authentication is required or a private authentication method is used, `metadata.version` MUST be
set to "2.0". The authentication method and its corresponding values MUST be specified within the
auth block. See [per-type `auth` block examples](#per-type-auth-examples) for details.

<details>
<summary>Lockfile v2 schema</summary>

```yaml
# artifacts.lock.yaml
metadata:
  version: "2.0"          # required; bump to "2.0" while using auth

artifacts:
  - download_url: <url>   # required; HTTP(S) URL of the artifact
    checksum: <alg:hash>  # required; e.g. sha256:abc123...
    filename: <name>      # optional; defaults to the last path segment of the URL
    auth:                 # optional; omit for public resources or when using .netrc
      bearer:             # bearer OR basic
        header: <name>    # optional; defaults to Authorization
        value: <token>    # required; supports $VAR env var interpolation
```

</details>

#### Examples

Examples of lockfiles for Simple and Mix resources, covering both public and private resources.

<details id="simple-example">
<summary>Simple example (public artifact, no authentication)</summary>

```yaml
metadata:
  version: "1.0"
artifacts:
  - download_url: https://huggingface.co/instructlab/granite-7b-lab/resolve/main/model-00001-of-00003.safetensors?download=true
    filename: granite-model-1.safetensors
    checksum: sha256:d16bf783cb6670f7f692ad7d6885ab957c63cfc1b9649bc4a3ba1cfbdfd5230c
```

</details>

<details>
<summary>Example with both public and authenticated artifacts</summary>

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

</details>

### Lockfile properties

#### download_url (required)

A string containing the download URL of the artifact.

#### checksum (required)

A string in the format `algorithm:hash`. Must be provided to ensure the identity
of the artifact.

#### filename (optional)

Provided for user convenience to name the files as they want. If not provided,
it is derived from the `download_url`. The filename can also contain a relative
path inside Hermeto's output directory for the generic fetcher
(`{hermeto-output-dir}/deps/generic`). Hermeto verifies that resulting
filenames, including those derived from download URLs, do not overlap.

#### auth (optional)

The auth block MUST be included when the resource is private and requires authentication, or when
any private authentication method is used. In such cases, `metadata.version` MUST be set to `"2.0"`.

The auth block is optional when the resource is publicly accessible and does not require
authentication, or when authentication can be provided through a supported .netrc configuration.

Schema of the `auth` block inside `artifacts.lock.yaml`:

```yaml
auth:
  <auth-type>:
    <credential-field>: <value>
```

**`basic`** — HTTP Basic authentication via explicit credentials:

| Field | Type | Required |
|-------|------|----------|
| `username` | string | Yes |
| `password` | string | Yes |

**`bearer`** — HTTP header-based token authentication:

| Field | Type | Required |
|-------|------|----------|
| `header` | string | No |
| `value` | string | Yes |

<a id="per-type-auth-examples"></a>

#### Per-type `auth` block examples

Each platform expects a different header and value in the `auth` block.
Refer [Bearer Token Auth](#bearer-token-auth) for all the supported formats.

<details>
<summary>GitLab (custom <code>PRIVATE-TOKEN</code> header)</summary>

```yaml
auth:
  bearer:
    header: PRIVATE-TOKEN
    value: "$GITLAB_TOKEN"
```

</details>

<details>
<summary>GitHub / most platforms (standard <code>Authorization: Bearer</code> header)</summary>

```yaml
auth:
  bearer:
    value: "Bearer $GITHUB_TOKEN"
```

</details>

<details>
<summary>Gitea (non-standard <code>token</code> prefix)</summary>

```yaml
auth:
  bearer:
    value: "token $GITEA_TOKEN"
```

</details>

### Integrating with aiohttp

As discussed above Bearer token support is implemented by extending the `artifacts.lock.yaml`
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

### SBOM Components

Artifacts fetched with the generic fetcher are recorded in the SBOM that Hermeto
produces. Given that the lockfile provides a download location, filename, and checksum,
they are always recorded as SBOM components with a purl of type `generic`.

Example Of SBOM generated for the
[simple example](#simple-example):

<details>
<summary>SBOM example (CycloneDX JSON)</summary>

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

</details>

## Appendix A. Potential Future Extensions For Authentication

The schemes below are not implemented, but are considered for future expansion.

### Input JSON Authentication

For backends with homogeneous sources (single registry/index), an input JSON
approach may be appropriate if authentication to private resources is needed:

<details>
<summary>Input JSON authentication example</summary>

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

</details>

### AWS Signature Version 4

[AWS Signature Version 4][aws-sigv4] (`AWS4-HMAC-SHA256`) is a more complex
authentication scheme used by AWS. Unlike bearer tokens, it requires:

- Request-specific signature computation (method, path, headers, timestamp)
- AWS credentials (access key ID + signing key)
- Region and service identifiers

The proposed schema could theoretically support this:

<details>
<summary>AWS Signature Version 4 schema example</summary>

```yaml
auth:
  aws4:
    region: us-east-1
    service: s3
    access_key_id: "$AWS4_ACCESS_KEY_ID"
    signing_key: "$AWS4_SIGNING_KEY"
```

</details>

AWS Signature Version 4 is significantly more complex than header
injection — it requires computing HMAC-SHA256 signatures over canonicalized
request data, which would likely require implementing a signing algorithm
in Hermeto. If demand warrants it, `aws4-hmac-sha256` could be added as a
future `auth` type.

### Other HTTP Auth Schemes

IANA defines a number of [HTTP auth schemes][iana-auth-schemes] users might
find useful to have supported.

## References

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