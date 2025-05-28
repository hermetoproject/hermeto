# Support for different purl types in the generic artifact fetcher
The generic artifact package manager is being added to Cachi2 as a means for users to introduce files that do not belong to traditional package manager ecosystems (e.g. pip, npm, golang) to their hermetic container builds. Since Cachi2 does not have any extra information about the file that's being fetched, the purls are always reported as [pkg:generic](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#generic).

There are use cases that would benefit from more accurate purls, though, such as the recent Maven artifacts [proposal](https://github.com/containerbuildsystem/cachi2/pull/663). Considering that the purl specification already identifies several types of packages that don't fit into traditional package manager (e.g. github, docker, huggingface; see the [purl types spec](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst) for more info), this proposal builds on top of the fundamentals of the generic fetcher to provide an extensible mechanism that would allow Cachi2 to fetch files from specific sources and report them with matching purl types.

## Generating purls of a specific type
A purl is simply a way to represent a unique package and its location on the internet. When fetching a generic artifact, Cachi2 will generate a purl of type `generic` by default, since there's no further info on the artifact's type. With this proposal, the lockfile will be extended to support a `type` field for each artifact, and for each supported purl type, a set of attributes that are necessary for resolving both the download URL and generating the purl for that artifact.

This is a summary of how the resolution of a single package will look like:
- Check if the purl type is supported
- Read and validate the attributes
- Resolve the download URL
- Fetch the artifact
- In case the artifact is a file, verify that at least one of the provided checksums match
- Generate a purl based on the input attributes
- Generate a SBOM component containing the type-specific purl and the validated checksums as qualifiers

### What confidence Cachi2 has in the reported SBOM components
- The attributes were resolved to a valid download URL where the artifact was fetched from
- At least one of the provided checksums was validated
- There is no guarantee over the reported purl type, Cachi2 will trust the user input

## Adding support for new purl types
Although the proposal shows this feature as extensible to pretty much any purl type that exists, the decision on which types will be supported will be done on a case-by-case basis. The initial implementation will be done for Maven artifacts, and support for new purl types will be analyzed by the maintainers when the need for them arises from the community.

There are specific details regarding each purl type, such as extending the feature to fetch Git repositories or OCI artifacts instead of regular files, and also a potential overlap with existing or planned fully supported package managers, such as pip or npm. All of this needs to be taken in consideration before there's a decision of supporting a new purl type in the generic fetcher.

## Examples of workflows for different purl types

### Maven

#### artifacts.lock.yaml
```yaml
metadata:
  version: 1.0.0
artifacts:
  - type: maven
    options:
      group_id: io.quarkus
      artifact_id: quarkus-core
      type: jar
      classifier: null # optional, can be omitted if null
      repository_url: https://maven.repository.redhat.com/ga
    checksums:
      sha256: e4ca5fad
    target: quarkus.jar
```

#### processing the lockfile
First, we need to determine the file extension by using the rules defined [here](https://maven.apache.org/repositories/artifacts.html#but-where-do-i-set-artifact-extension).

```python
extension = find_extension(type, classifier)

# generate the download URL
{repository_url}/{as_dir(group_id)}/{artifact_id}/{version}/{artifact_id}-{version}.{extension}

# generate the purl
pkg:maven/{group_id}/{artifact_id}@{version}[-{classifier}]?type={type}&repository_url={repository_url}
```

#### SBOM component
```json
{
  "name": "quarkus-core",
  "version": "3.8.5.redhat-00004",
  "purl": "pkg:maven/io.quarkus/quarkus-core@3.8.5.redhat-00004?type=jar&repository_url=https://maven.repository.redhat.com/ga&checksum=sha256%3Ae4ca5fad",
  "properties": [
    {
      "name": "cachi2:found_by",
      "value": "cachi2:generic"
    }
  ],
  "externalReferences": [
    {
      "type": "distribution",
      "url": "https://maven.repository.redhat.com/ga/io/quarkus/quarkus-core/3.8.5.redhat-00004/quarkus-core-3.8.5.redhat-00004.jar"
    }
  ],
  "type": "file"
}
```

### Nuget

#### artifacts.lock.yaml
```yaml
metadata:
  version: 1.0.0
artifacts:
  - type: nuget
    attributes:
      namespace: Google
      package: Protobuf
      version: 3.28.3
      repository_url: https://globalcdn.nuget.org/packages # default value, can be omitted
    checksums:
      sha256: e4ca5fad
```

#### processing the lockfile
```python
# generate the download URL
{repository_url}{lowercase(namespace + '.' + package)}.{version}.nupkg?packageVersion={version}

# generate the purl
pkg:nuget/[{namespace}.]{package}@{version}[?repository_url={repository_url}]
```

#### SBOM component
```json
{
  "name": "Google.Protobuf",
  "version": "3.28.3",
  "purl": "pkg:nuget/Google.Protobuf@3.28.3?checksum=sha256%3Ae4ca5fad&repository_url=https://globalcdn.nuget.org/packages",
  "properties": [
    {
      "name": "cachi2:found_by",
      "value": "cachi2:generic"
    }
  ],
  "externalReferences": [
    {
      "type": "distribution",
      "url": "https://globalcdn.nuget.org/packages/google.protobuf.3.28.3.nupkg?packageVersion=3.28.3"
    }
  ],
  "type": "file"
}
```

### Hugging Face

#### artifacts.lock.yaml
```yaml
  - type: huggingface
    attributes:
      namespace: nvidia
      project: Llama-3.1-Nemotron-70B-Instruct-HF
      commit_hash: 043235d6088ecd3dd5fb5ca3592b6913fd516027
      repository_url: https://huggingface.co # default value, can be omitted
    checksums:
      sha256: e4ca5fad
  - type: huggingface
    attributes:
      namespace: nvidia
      project: Llama-3.1-Nemotron-70B-Instruct-HF
      commit_hash: 043235d6088ecd3dd5fb5ca3592b6913fd516027
      file_name: model-00001-of-00030.safetensors
      repository_url: https://huggingface.co # default value, can be omitted
    checksums:
      sha256: e4ca5fad
```

#### processing the lockfile
```python
# download URL (in case a file_name is present)
{repository_url}/{namespace}/{name}/blob/{commit_hash}/{file_name}

# git clone command (in case file_name is absent) 
git clone {repository_url}/{namespace}/{name}
git checkout {commit_hash}

# generating the purl
pkg:huggingface/{namespace}/{name}@{commit_hash}[?repository_url={repository_url}][&file_name={file_name}]
```

#### SBOM component
```json
[
  {
    "name": "Llama-3.1-Nemotron-70B-Instruct-HF",
    "purl": "pkg:huggingface/nvidia/Llama-3.1-Nemotron-70B-Instruct-HF@043235d6088ecd3dd5fb5ca3592b6913fd516027",
    "properties": [
      {
        "name": "cachi2:found_by",
        "value": "cachi2:generic"
      }
    ],
    "externalReferences": [
      {
        "type": "distribution",
        "url": "https://huggingface.co/nvidia/Llama-3.1-Nemotron-70B-Instruct-HF"
      }
    ],
    "type": "library"
  },
  {
    "name": "Llama-3.1-Nemotron-70B-Instruct-HF",
    "purl": "pkg:huggingface/nvidia/Llama-3.1-Nemotron-70B-Instruct-HF@043235d6088ecd3dd5fb5ca3592b6913fd516027?file_name=model-00001-of-00030.safetensors&checksum=sha256%3Ad16bf783cb6670f7f692ad7d6885ab957c63cfc1b9649bc4a3ba1cfbdfd5230c",
    "properties": [
      {
        "name": "cachi2:found_by",
        "value": "cachi2:generic"
      }
    ],
    "externalReferences": [
      {
        "type": "distribution",
        "url": "https://huggingface.co/nvidia/Llama-3.1-Nemotron-70B-Instruct-HF/blob/fac73d3507320ec1258620423469b4b38f88df6e/model-00001-of-00030.safetensors"
      }
    ],
    "type": "file"
  }
]
```

### OCI

#### artifacts.lock.yaml
```yaml
metadata:
  version: 1.0.0
artifacts:
  - type: oci
    attributes:
      namespace: konflux-ci
      artifact: buildah-task
      digest: sha256:b2d6c32d1e05e91920cd4475b2761d58bb7ee11ad5dff3ecb59831c7572b4d0c
      repository_url: quay.io
    checksums:
      sha256: e4ca5fad
```

#### processing the lockfile
```python
# generate the download command
podman pull {repository_url_and_namespace}/{name}@{digest}

# generate the purl
pkg:oci/{name}@{encode(digest)}?repository_url={repository_url_and_namespace}
```

#### SBOM component
```json
{
    "name": "buildah-task",
    "purl": "pkg:oci/buildah-task@sha256%3Ab2d6c32d1e05e91920cd4475b2761d58bb7ee11ad5dff3ecb59831c7572b4d0c?repository_url=quay.io/konflux-ci&arch=amd64&tag=latest",
    "properties": [
        {
            "name": "cachi2:found_by",
            "value": "cachi2:generic"
        }
    ],
    "externalReferences": [
        {
            "type": "distribution",
            "url": "quay.io/konflux-ci/buildah-task@sha256:b2d6c32d1e05e91920cd4475b2761d58bb7ee11ad5dff3ecb59831c7572b4d0c"
        }
    ],
    "type": "container"
}
```

## Dropped alternative: consume purls as input

We initially considered the idea of consuming purls as part of a package input, instead of relying on a decomposed attributes. This idea was abandoned because the following advantages were identified on the attribute-based approach:
- More readable and predictable format, since the purl spec provides much flexibility on how the purls can look like
- Better user experience, specially for users who are not familiar with purls
- Attributes can be added or changed as needed without the need to follow the purl spec

#### artifacts.lock.yaml
```yaml
metadata:
  version: 1.0.0
artifacts:
  - purl: pkg:maven/io.quarkus/quarkus-core@3.8.5.redhat-00004?type=jar&repository_url=https://maven.repository.redhat.com/ga
    checksums:
      sha256: d16bf783cb6670f7f692ad7d6885ab957c63cfc1b9649bc4a3ba1cfbdfd5230c
    target: quarkus.jar
```

**Resulting purl**
```
pkg:maven/io.quarkus/quarkus-core@3.8.5.redhat-00004?type=jar&repository_url=https://maven.repository.redhat.com/ga&checksum=sha256%3Ae4ca5fad
```
