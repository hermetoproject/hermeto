# Generic fetcher

- [Specifying artifacts to fetch](#specifying-artifacts-to-fetch)
- [Authentication](#authentication)
- [Using fetched dependencies](#using-fetched-dependencies)
- [Full example walkthrough](#example)

## Support scope

Generic fetcher is made specifically for use cases where hermeto will not
implement a full package manager support, or for ecosystems where no such
package manager exists. It is highly discouraged for this feature to be used for
anything already supported by hermeto in other ways (such as e.g. pip packages),
because the produced SBOM component will not be accurate.

## Specifying artifacts to fetch

The generic fetcher requires a lockfile `artifacts.lock.yaml` that specifies
which files to download. This file is expected to be in the source repository.
Alternatively, a different filename or location can be supplied via the
`lockfile` key in the JSON input to hermeto. The value may be either an absolute
path or a path relative to the package `path`.

Below are sections for each type of supported artifact. Several artifacts of
different types can be specified in a single lockfile.

The lockfile must always contain a `metadata` header and a list of `artifacts`.
Supported versions are 1.0 and 2.0. Version 2.0 is required when using
[authentication](#authentication):

```yaml
---
metadata:
  version: "1.0"  # or "2.0" for auth support
artifacts: []
```

Hermeto can be run as follows

```shell

hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  '<JSON input>'
```

where 'JSON input' is

```js
{
  "type": "generic",
  // path to the package (relative to the --source directory)
  // defaults to "."
  "path": ".",
  // option to specify lockfile path: absolute or relative to package path
  // defaults to "artifacts.lock.yaml", resolved relative to package path
  "lockfile": "artifacts.lock.yaml",
}
```

### Arbitrary files

This artifact type is intended for whatever files are needed at build time that
do not fit neatly into other package managers.

```yaml
---
metadata:
  version: "1.0"
artifacts:
  - download_url: "https://example.com/file.zip"
    checksum: "algorithm:hash"
    filename: "optional-custom-name.zip"  # optional
```

Each artifact requires:

- `download_url` The URL to download the file from
- `checksum` In format "algorithm:hash" (e.g., "sha256:123...")
- `filename` Optional custom filename for the downloaded file. If not present,
  it will be derived from the url

#### Arbitrary SBOM component

Since there can't be any assumptions about these files beyond checking their
identity against a checksum, these files will be reported with `pkg:generic`
purl in the output SBOM.

### Maven artifacts

This type is for downloading [maven repository artifacts][]. These are specified
using GAV coordinates that are enumerated in the artifact's attributes in the
lockfile. The download URL will be assembled using this information.

```yaml
---
metadata:
    version: "1.0"
artifacts:
    - type: "maven"
      filename: "ant.jar"
      attributes:
          repository_url: "https://repo1.maven.org/maven2"
          group_id: "org.apache.ant"
          artifact_id: "ant"
          version: "1.10.14"
          type: "jar"
      checksum: "sha256:4cbbd9243de4c1042d61d9a15db4c43c90ff93b16d78b39481da1c956c8e9671"
```

Each artifact requires

- `type` type of the artifact (always `maven`)
- `filename` Optional custom filename for the downloaded file. If not present,
  it will be derived from the url
- `attributes` Maven-specific attributes

  - `repository_url` URL of the Maven repository (required)
  - `group_id` Maven group ID  (required)
  - `artifact_id` Maven artifact ID  (required)
  - `version` Version of the artifact (required)
  - `type` Type of the artifact ("jar" by default)
  - `classifier` Maven classifier (optional)

- `checksum` In format "algorithm:hash" (e.g., "sha256:123...")

#### Maven SBOM component

These files will be reported with `pkg:maven` purl in the output SBOM, because
the URL is fully assembled from the provided attributes and therefore the file
can be assumed to be a maven artifact.

## Authentication

The generic fetcher supports per-artifact authentication for downloading from
private repositories and registries. Authentication requires lockfile version
`"2.0"`. Artifacts without `auth` do not require authentication and will still
use `.netrc` credentials if available.

### Auth types

Each artifact can specify an `auth` block with exactly one auth type.

#### Bearer token

Header-based token authentication. Supports most platforms (GitHub, GitLab,
Gitea, JFrog Artifactory, etc.).

| Field    | Required | Description                                                                 |
|----------|----------|-----------------------------------------------------------------------------|
| `header` | No       | HTTP header name. Defaults to `Authorization`                               |
| `value`  | Yes      | Header value, supports `$VAR` / `${VAR}` environment variable interpolation |

#### HTTP Basic

Username and password authentication encoded as a Base64 `Authorization` header.

| Field      | Required | Description                                        |
|------------|----------|----------------------------------------------------|
| `username` | Yes      | Username, supports `$VAR` / `${VAR}` interpolation |
| `password` | Yes      | Password, supports `$VAR` / `${VAR}` interpolation |

### Environment variable interpolation

Secret values should be provided via environment variables using `$VAR` or
`${VAR}` syntax. Hermeto will fail with a clear error if any referenced variable
is not set. Use `\$` for a literal dollar sign.

### Examples

**GitLab** (custom `PRIVATE-TOKEN` header):

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

**GitHub** (standard Bearer token):

```yaml
metadata:
  version: "2.0"
artifacts:
  - download_url: "https://api.github.com/repos/owner/repo/tarball/v1.0.0"
    checksum: "sha256:abc123..."
    auth:
      bearer:
        value: "Bearer $GITHUB_TOKEN"
```

**Mixed** (authenticated and public artifacts):

```yaml
metadata:
  version: "2.0"
artifacts:
  - download_url: "https://gitlab.example.com/api/v4/projects/123/repository/archive.tar.gz"
    checksum: "sha256:..."
    auth:
      bearer:
        header: PRIVATE-TOKEN
        value: "$GITLAB_TOKEN"

  - download_url: "https://example.com/public-file.zip"
    checksum: "sha256:..."
```

Then run hermeto with the required environment variables set:

```shell
export GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
export GITHUB_TOKEN="github_pat_xxxxxxxxxxxxxxxxxxxxx"
hermeto fetch-deps generic
```

## Using fetched dependencies

Hermeto downloads the files into the `deps/generic/` subpath of the output
directory. Files are named according to the `filename` field if specified,
otherwise derived from the URL. During your build, you would typically mount
hermeto's output directory into your container image and reference the
individual files.

See the [Example](#example) below for a complete walkthrough of Hermeto usage.

### Example

Generic fetcher is a package manager that can fetch arbitrary files. Let's build
a [basic generic project][] that would be inconvenient to build hermetically
otherwise. This image will provide [OWASP Dependency check][] tool, which is available
to install from GitHub releases page.

Get the repo if you want to try for yourself:

```shell
git clone https://github.com/hermetoproject/doc-examples.git --branch=generic-basic && cd doc-examples
```

#### Pre-fetch dependencies

As mentioned above in
[Specifying artifacts to fetch](#specifying-artifacts-to-fetch),
Hermeto pre-fetches using the `fetch-deps` command.

Sources can be fetched with

```shell
hermeto fetch-deps generic
```

The shorthand `generic` defaults `path` to `.`. You can pass a full JSON
object if you need a custom source directory or lockfile path instead.

#### Build the application image

The repo already contains a `Containerfile` for this example. Build it while
mounting the pre-fetched Hermeto output:

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --network none \
  --tag sample-generic-app
```

[basic generic project]: https://github.com/hermetoproject/doc-examples/tree/generic-basic
[maven repository artifacts]: https://maven.apache.org/repositories/artifacts.html
[OWASP Dependency check]: https://github.com/dependency-check/DependencyCheck
