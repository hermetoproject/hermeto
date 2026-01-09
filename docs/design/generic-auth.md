# Generic Fetcher - Support for HTTP Authentication

## Background

The generic fetcher downloads artifacts from arbitrary URLs specified in the
`artifacts.lock.yaml` lockfile. Many artifact hosting services require
authentication/authorization credentials to access private resources. This
document describes the type of API authentication mechanism implemented in
Hermeto's generic fetcher backend beyond the already existing `.netrc` support
(HTTP Basic authentication).

### Current Industry Authentication/Authorization trends

The sections below provide a detailed overview of the most commonly available
authentication schemes (in no particular order) and the level of Hermeto's
involvement in the process.

* HTTP Basic authentication
* HTTP Bearer token
* OAuth2
* OIDC
* other

#### HTTP Basic Auth

A simple authentication mechanism defined in [RFC 7617][rfc-7617]. HTTP
Basic Auth transmits credentials as a Base64-encoded `username:password` pair in
the `Authorization` header. It **must** be used with TLS due to the severe
security design flaw that credentials are transmitted in plaintext.
However, even with TLS the problem with HTTP Basic Auth is that the credentials
are typically long-lived (often user's actual password) and not scoped to
specific resources which is why this method is being gradually phased out by
major platform players on the market (see the tables below) in favour of bearer
token authentication.
The `.netrc` file format provides a convenient way to store the HTTP Basic Auth
credentials and Hermeto already supports this implicitly. Major resource
providing platforms currently (at the time of writing) supporting HTTP Basic
Auth are listed below.

| Platform | Username | Password | Source |
|----------|----------|----------|--------|
| Bitbucket | email address | API token | [Bitbucket docs][bitbucket-auth] |
| Gitea | username | access token | [Gitea API docs][gitea-auth] |
| Hugging Face[^1] | username | access token | [Hugging Face docs][huggingface-auth] |
| Sonatype Nexus | token name | token passcode | [Nexus docs][nexus-auth] |
| GitLab[^2] | username | Personal Access Token | [GitLab PAT docs][gitlab-pat-docs] |

#### Bearer Token Auth

Bearer token authentication transmits an opaque token in the `Authorization`
HTTP header, typically prefixed with the `Bearer` string. Defined in [RFC
6750][rfc-6750] as part of OAuth 2.0, it has become the de facto standard for
API authentication. Unlike HTTP Basic Auth, bearer tokens are usually
short-lived and can be scoped to specific permissions or resources.
Major resource providing platforms currently (at the time of writing) supporting
bearer token authentication are listed below.

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

[^1]: Hugging Face documents bearer tokens specifically for *Inference Providers*.
    For general Hub access (model/dataset downloads), HTTP Basic Auth is mentioned.

[^2]: GitLab supports HTTP Basic Auth for Git operations ([clone, push,
    pull][gitlab-pat-usage]), but the REST API (including archive downloads) only
    accepts [header-based credentials](https://github.com/hermetoproject/hermeto/issues/1224#issuecomment-3728235587).

#### OAuth2

OAuth2 is a complete authorization framework defined in [RFC 6749][rfc-6749] that
enables third-party applications to obtain limited access to HTTP services. It
defines several authorization flows (called "grants"), including the [Authorization Code
Grant][rfc-6749-4-1] which requires interactive browser-based user consent, and
the [Client Credentials Grant][rfc-6749-4-4] for machine-to-machine
communication both of which are, in principle, the most relevant to Hermeto's
use case. However, Hermeto cannot implement OAuth2 flows directly for the
following reasons:
1. They are **credential acquisition** mechanisms—they define how tokens are
   obtained, not how they are attached to requests.
2. The different complexities of the flows: the *Authorization Code Grant* is
   interactive (i.e. most commonly requiring a browser redirection and user
   input to obtain a token), while the *Client Credentials Grant* would require
   Hermeto to contact a token issuer endpoint to obtain a token before using
   it—functionality that is out of scope for the project.

#### OIDC

[OpenID Connect][oidc-spec] (OIDC) is an identity layer built on top of OAuth2.
At its core, OIDC allows applications to delegate authentication to a trusted
third-party Identity Provider (IdP) rather than verifying credentials directly.
When a user authenticates with the IdP, the application receives a
signed ID token (a JWT) containing standardized identity claims. This enables
single sign-on (SSO) across multiple applications and separates identity
management from application logic.

In CI/CD contexts, OIDC powers "Trusted Publishing" workflows used by [GitHub
Actions][github-oidc], [GitLab CI][gitlab-oidc], etc., allowing jobs to
authenticate to external services without stored secrets. The CI platform acts
as the IdP, issuing tokens that assert the workflow's identity (repository,
branch, job name), which target services can verify and exchange for
short-lived access tokens.

Hermeto cannot implement OIDC directly because it would require detecting
which CI environment it's running on, implementing provider-specific OIDC token
endpoint support, and handling service-specific token exchange APIs for each
target platform. This complexity is simply out of scope - like with plain OAuth2
users will continue acquiring tokens externally and provide them to Hermeto in
the exact same manner (implementation specifics explained in the sections
below).


## Implementation

The implementation described below revolves around extending the generic
backend's `artifacts.lock.yaml` schema. Alternatively, this could have been
approached in a uniform manner by extending the input JSON's capabilities to
carry credential related data. However, the generic backend has a fundamentally
different requirement - per-URL (instead of per-host) authentication which the
input JSON simply cannot express and for the rest of the backends realistically
doesn't even need to.

### Lockfile Schema

The `artifacts.lock.yaml` schema is extended with an optional `auth` field per
URL resource as follows:

```yaml
auth:
  <auth-type>:
    <credential-field>: <value>
    ...
```

The `<auth-type>` key (e.g., `bearer`) acts as a discriminator that determines
which `<credential-field>` entries are valid within that block. This gives us
flexibility to extend authentication support beyond HTTP Bearer Auth in the
future.

An example of a fully defined `artifacts.lock.yaml` with bearer token
authentication then looks like:

```yaml
metadata:
  version: "2.0" # version bump needed, Pydantic field validation is strict
artifacts:
  - download_url: "https://gitlab.example.com/api/v4/projects/123/repository/archive.tar.gz"
    checksum: "sha256:abc123..."
    auth:
      bearer:
        header: PRIVATE-TOKEN
        value: "$GITLAB_TOKEN"
```

#### Auth Configuration Fields

The `auth` map contains exactly one key that identifies the authentication type.
Each *type* has its own set of required and optional fields.

##### Types:

**`bearer`** - HTTP header-based token authentication:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `header` | string | No | HTTP header name (e.g., `PRIVATE-TOKEN`). Defaults to `Authorization` |
| `value` | string | Yes | HTTP Header value using environment variable placeholders and custom strings |


The `value` field expects environment variable interpolation using shell-like
`$VAR` syntax for any secrets to be attached to a request along with any strings
required by a given platform REST API spec (e.g. [Gitea][gitea-auth]). Hermeto
fails with a clear error message if any of the referenced environment variables
is unset.

#### Examples

**GitLab** (custom PRIVATE-TOKEN header):
```yaml
auth:
  bearer:
    header: PRIVATE-TOKEN  # custom header, not standard Authorization
    value: "$GITLAB_TOKEN"
```

**GitHub / most platforms** (standard Bearer token):
```yaml
auth:
  bearer:
    # header defaults to "Authorization"
    value: "Bearer $GITHUB_TOKEN"
```

**Gitea** (non-standard token prefix):
```yaml
auth:
  bearer:
    value: "token $GITEA_TOKEN"  # Gitea uses "token" instead of "Bearer"
```

### Integrating with aiohttp

Integrating bearer token support with the `aiohttp` library we use is fairly
straightforward since this topic is explicitly covered in the
[docs][aiohttp-custom-headers] and requires an explicit header injection:

```python
headers = {"Authorization": "Bearer eyJh...0M30"}
async with ClientSession(headers=headers) as session:
    ...
```

A simplified implementation looks like this (considering the above):
1. Resolve the `auth` configuration maps for each lockfile's artifact in the
   generic backend
2. Read the corresponding environment variables to extract the values based
   on the `header` field and populate the header information with the `value`
   field's content
3. Inject the modified headers into the HTTP request


## Usage

The full end-user bearer authentication workflow might look like this:

```yaml
# MIXED SOURCE AUTHENTICATION EXAMPLE
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

  # Public artifact (no auth, uses .netrc if available)
  - download_url: "https://example.com/public-file.zip"
    checksum: "sha256:..."

  # Private artifact from GitHub (standard Bearer token)
  - download_url: "https://api.github.com/repos/owner/repo/tarball/v1.0.0"
    checksum: "sha256:..."
    auth:
      bearer:  # no header provided, 'Authorization' is used by default
        value: "Bearer $GITHUB_TOKEN"
```

```bash
export GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
export GITHUB_TOKEN="github_pat_xxxxxxxxxxxxxxxxxxxxx"
hermeto fetch-deps generic
```


## Potential Future Extensions
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
authentication scheme used by AWS. Unlike Bearer tokens, it requires:

- Request-specific signature computation (method, path, headers, timestamp)
- AWS credentials (access key ID + signing key [optional])
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

However, AWS Signature Version 4 is significantly more complex than header injection—it
requires computing HMAC-SHA256 signatures over canonicalized request data. This
would likely require implementing a signing algorithm in Hermeto. If the demand
warrants, `aws4-hmac-sha256` could be added as a future `auth` type.

### Other HTTP Auth schemes

IANA defines a number of [HTTP schemes][iana-auth-schemes] users might find
useful to have supported.

[iana-auth-schemes]: https://www.iana.org/assignments/http-authschemes/http-authschemes.xhtml
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
[aws-sigv4]: https://docs.aws.amazon.com/AmazonS3/latest/API/sigv4-auth-using-authorization-header.html
[google-auth]: https://docs.cloud.google.com/artifact-registry/docs/repositories/download-files#api
[rubygems-auth]: https://guides.rubygems.org/rubygems-org-api/
[oauth2-rfc]: https://datatracker.ietf.org/doc/html/rfc6749
[oidc-spec]: https://openid.net/developers/how-connect-works/
[github-oidc]: https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-security-hardening-with-openid-connect
[gitlab-oidc]: https://docs.gitlab.com/ci/secrets/id_token_authentication/
[aiohttp-custom-headers]: https://docs.aiohttp.org/en/stable/client_advanced.html#custom-request-headers
