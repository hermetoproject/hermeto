# Generic fetcher

* [Specifying artifacts to fetch](#specifying-artifacts-to-fetch)
* [Using fetched dependencies](#using-fetched-dependencies)

## Support scope

Generic fetcher is made specifically for use cases where hermeto will not
implement a full package manager support, or for ecosystems where no such
package manager exists. It is highly discouraged for this feature to be used for
anything already supported by hermeto in other ways (such as e.g. pip packages),
because the produced SBOM component will not be accurate.

## Specifying artifacts to fetch

The generic fetcher requires a lockfile `artifacts.lock.yaml` that specifies
which files to download. This file is expected to be in the source repository.
Alternatively, it can be supplied as an absolute path via the `lockfile` key in
the JSON input to hermeto.

Below are sections for each type of supported artifact. Several artifacts of
different types can be specified in a single lockfile.

The lockfile must always contain a `metadata` header and a list of `artifacts`.
Currently, the only supported version is 1.0:

```yaml
---
metadata:
  version: "1.0"
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

```jsonc
{
  "type": "generic",
  // path to the package (relative to the --source directory)
  // defaults to "."
  "path": ".",
  // option to specify lockfile path, must be an absolute path if specified
  // defaults to "artifacts.lock.yaml", relative to path
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

* `download_url` The URL to download the file from
* `checksum` In format "algorithm:hash" (e.g., "sha256:123...")
* `filename` Optional custom filename for the downloaded file. If not present,
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

* `type` type of the artifact (always `maven`)
* `filename` Optional custom filename for the downloaded file. If not present,
  it will be derived from the url
* `attributes` Maven-specific attributes

  * `repository_url` URL of the Maven repository (required)
  * `group_id` Maven group ID  (required)
  * `artifact_id` Maven artifact ID  (required)
  * `version` Version of the artifact (required)
  * `type` Type of the artifact ("jar" by default)
  * `classifier` Maven classifier (optional)

* `checksum` In format "algorithm:hash" (e.g., "sha256:123...")

#### Maven SBOM component

These files will be reported with `pkg:maven` purl in the output SBOM, because
the URL is fully assembled from the provided attributes and therefore the file
can be assumed to be a maven artifact.

## Using fetched dependencies

Hermeto downloads the files into the `deps/generic/` subpath of the output
directory. Files are named according to the `filename` field if specified,
otherwise derived from the URL. During your build, you would typically mount
hermeto's output directory into your container image and reference the
individual files. For a detailed example, see [usage.md][].

[usage.md]: usage.md#example-generic-fetcher

[maven repository artifacts]: https://maven.apache.org/repositories/artifacts.html
