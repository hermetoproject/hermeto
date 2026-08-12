# Add generic fetcher

## Context

Some users need to download arbitrary files that don't fit within an established package ecosystem hermeto could
potentially otherwise support. The target audience is users that want to use hermeto to achieve hermetic builds
and also want an easy way to include these arbitrary files, that hermeto will account for in the SBOM it produces.

## Decision

A new package manager for generic artifacts must be introduced. This package manager utilizes a custom
lockfile based on which it will download files, save them into a requested location, and verify checksums.
Below is a more detailed overview of the implementation.

### Lockfile format

Hermeto expects the lockfile to be named `artifacts.lock.yaml`.
In order to account for possible future breaking changes, the lockfile will contain a `metadata` section with a `version`
field that will indicate the version of the lockfile format. It will also contain a list of artifacts (files) to download,
each of the artifacts to having a URL, a checksum, and optionally output filename specified.

```yaml
metadata:
  # uses X.Y semantic versioning
  version: "1.0"
artifacts:
  - download_url: https://huggingface.co/instructlab/granite-7b-lab/resolve/main/model-00001-of-00003.safetensors?download=true
    filename: granite-model-1.safetensors
    checksum: sha256:d16bf783cb6670f7f692ad7d6885ab957c63cfc1b9649bc4a3ba1cfbdfd5230c
```

#### Lockfile properties

Below is an explanation of individual properties of the lockfile.

##### download_url (required)

Specified as a string containing the download url of the artifact.

##### checksum (required)

Specified as string in the format of "algorithm:hash". Must be provided to ensure the identity of the artifact.

#### filename (optional)

This key is provided mainly for the users convenience, so the files end up in expected locations. It is optional and if
not specified, it will be derived from the download_url. Filename here is a path inside hermeto's output directory for
the generic fetcher (`{hermeto-output-dir}/deps/generic`). Hermeto will verify that the resulting filenames, including those
derived from download urls do not overlap.

### SBOM components

Artifacts fetched with the generic fetcher will all be recorded in the SBOM hermeto produces. Given the inability to derive
any extra information about these files beyond a download location and a filename, these files will always be recorded
as SBOM components with purl of type generic.

Additionally, the SBOM component will contain [externalReferences] of type `distribution` to indicate the url used to download
the file to allow for easier handling for tools that might process the SBOM.

Here's an example SBOM generated for above file.

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

## Consequences

As mentioned before, this package manager enables users to fetch arbitrary files with hermeto and have them accounted for
in the SBOM.

[externalReferences]: https://cyclonedx.org/docs/1.6/json/#components_items_externalReferences
