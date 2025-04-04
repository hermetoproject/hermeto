# DNF/RPM Manifest/Lockfile swap

DNF4 - <https://github.com/rpm-software-management/dnf>

DNF5 - <https://github.com/rpm-software-management/dnf5>

RPM - <https://github.com/rpm-software-management/rpm>

## Context

The current solution for RPM manifests is based on the so-called RPM lockfile
[prototype](https://github.com/konflux-ci/rpm-lockfile-prototype)
which is a proof-of-concept tool that generates a lockfile containing all RPMs required to build a
container image. Hermeto parses this file manually and downloads all RPMs locally to enable hermetic
builds.

The `rpm` package manager is not released as a supported feature, thus, it can only be used with
`--dev-package-manager` flag. User documentation does not exist either. Only available documentation
exists on the Konflux CI page - <https://konflux-ci.dev/docs/building/prefetching-dependencies/#rpm>.

### Limitations

- [hermetoproject/hermeto/issues/570](https://github.com/hermetoproject/hermeto/issues/570)

## DNF-native solution

[libpkgmanifest](https://github.com/rpm-software-management/libpkgmanifest)

### Overview

This library provides functionality for parsing and serializing RPM package manifest files in C++
and Python APIs. Currently, there is also a COPR repository available, where the prototype version
of the `dnf-manifest` plugin utilizing the functionality from this library is deployed. See the
usage below. COPR is an easy-to-use automatic build system providing a package repository as its
output.

### Project structure

Like the RPM lockfile prototype, the manifest file is a YAML file generated from the input YAML
file.

```bash
├── packages.input.yaml
├── packages.manifest.yaml
└── Containerfile
```

<details>
<summary>Input Schema</summary>

```json
{
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "RPM Package Input Schema",
    "schemaVersion": "0.0.2",
    "description": "Schema for RPM input files used by libpkgmanifest library (https://github.com/rpm-software-management/libpkgmanifest)",
    "type": "object",
    "required": [
        "document",
        "version",
        "repositories",
        "packages",
        "archs"
    ],
    "properties": {
        "document": {
            "type": "string",
            "enum": [
                "rpm-package-input"
            ],
            "description": "Indicates the type of document."
        },
        "version": {
            "type": "string",
            "pattern": "^[0-9]+(?:\\.[0-9]+)?(?:\\.[0-9]+)?$",
            "description": "The version of the input file. This should follow the semantic versioning format (x.y.z), where x, y, and z are integers."
        },
        "repositories": {
            "type": "array",
            "description": "A list of RPM repositories that serve as sources for all packages and their dependencies defined in the input file.",
            "items": {
                "type": "object",
                "required": [
                    "id"
                ],
                "anyOf": [
                    { "required": ["baseurl"] },
                    { "required": ["metalink"] },
                    { "required": ["mirrorlist"] }
                ],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Represents the ID of the repository as defined in the repo file available to the package manager at the time of package installation."
                    },
                    "baseurl": {
                        "type": "string",
                        "format": "uri",
                        "description": "The base URL of a repository where all content is hosted."
                    },
                    "metalink": {
                        "type": "string",
                        "format": "uri",
                        "description": "Specifies a URL to a metalink file that points to a repomd.xml file, generating a list of repository mirrors as base URLs."
                    },
                    "mirrorlist": {
                        "type": "string",
                        "format": "uri",
                        "description": "Specifies a URL to a file containing a list of base URLs."
                    }
                }
            }
        },
        "packages": {
            "type": "object",
            "description": "A map of packages to be used as input for resolving the manifest file, organized by the action to be applied to the packages listed under each action.",
            "properties": {
                "install": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                },
                "reinstall": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                }
            },
            "required": [
                "install"
            ]
        },
        "modules": {
            "type": "object",
            "description": "A map of modules to be used as input for resolving the manifest file, organized by the action to be applied to the modules listed under each action.",
            "properties": {
                "enable": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                },
                "disable": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                }
            }
        },
        "archs": {
            "type": "array",
            "description": "A list of architectures to be included for the resolution.",
            "items": {
                "type": "string",
                "pattern": "^[a-z0-9_]+$"
            },
            "minItems": 1
        },
        "options": {
            "type": "object",
            "description": "A map of options to be used as input for the package manager when resolving the manifest file.",
            "properties": {
                "allow_erasing": {
                    "type": "boolean",
                    "description": "Allow the package manager to remove installed packages to resolve dependency issues."
                }
            }
        }
    }
}
```

</details>

<details>
<summary>Manifest Schema</summary>

```json
{
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "RPM Package Manifest Schema",
    "schemaVersion": "0.2.2",
    "description": "Schema for RPM manifest files used by libpkgmanifest library (https://github.com/rpm-software-management/libpkgmanifest)",
    "type": "object",
    "required": [
        "document",
        "version",
        "data"
    ],
    "properties": {
        "document": {
            "type": "string",
            "enum": [
                "rpm-package-manifest"
            ],
            "description": "Indicates the type of document."
        },
        "version": {
            "type": "string",
            "pattern": "^[0-9]+(?:\\.[0-9]+)?(?:\\.[0-9]+)?$",
            "description": "The version of the manifest file. This should follow the semantic versioning format (x.y.z), where x, y, and z are integers."
        },
        "data": {
            "type": "object",
            "required": [
                "repositories",
                "packages"
            ],
            "properties": {
                "repositories": {
                    "type": "array",
                    "description": "A list of RPM repositories that serve as sources for all packages defined in the manifest.",
                    "items": {
                        "type": "object",
                        "required": [
                            "id"
                        ],
                        "anyOf": [
                            { "required": ["baseurl"] },
                            { "required": ["metalink"] },
                            { "required": ["mirrorlist"] }
                        ],
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Represents the ID of the repository as defined in the repo file available to the package manager at the time of package installation."
                            },
                            "baseurl": {
                                "type": "string",
                                "format": "uri",
                                "description": "The base URL of a repository where all content is hosted."
                            },
                            "metalink": {
                                "type": "string",
                                "format": "uri",
                                "description": "Specifies a URL to a metalink file that points to a repomd.xml file, generating a list of repository mirrors as base URLs."
                            },
                            "mirrorlist": {
                                "type": "string",
                                "format": "uri",
                                "description": "Specifies a URL to a file containing a list of base URLs."
                            }
                        }
                    }
                },
                "packages": {
                    "type": "object",
                    "description": "A mapping of architecture labels to arrays of packages. Each key represents an architecture (e.g., i686, x86_64, aarch64). The value is an array of package objects associated with that architecture.",
                    "patternProperties": {
                        "^[a-z0-9_]+$": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": [
                                    "name",
                                    "repo_id",
                                    "checksum",
                                    "size",
                                    "evr"
                                ],
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "The name of the package."
                                    },
                                    "repo_id": {
                                        "type": "string",
                                        "description": "The ID of the repository from which this package originates. It references one of the repository IDs listed in the 'repositories' section."
                                    },
                                    "location": {
                                        "type": "string",
                                        "description": "The relative URL within the specified repository used to construct the full URL from which the package can be downloaded."
                                    },
                                    "checksum": {
                                        "type": "string",
                                        "pattern": "^(sha1|sha224|sha256|sha384|sha512|md5|crc32|crc64):[A-Za-z0-9+/]+={0,2}$",
                                        "description": "The checksum of the package in the format <algorithm>:<digest>. The checksum algorithm (e.g., SHA256, MD5) is followed by a colon and the digest (a string of hexadecimal digits). Only lowercase letters are supported."
                                    },
                                    "size": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "description": "The size of the package in bytes. Must be a non-negative integer."
                                    },
                                    "evr": {
                                        "type": "string",
                                        "pattern": "^(?:\\d+:)?(.+)-(.+)$",
                                        "description": "The Epoch, Version and Release (EVR) of the package."
                                    },
                                    "srpm": {
                                        "type": "string",
                                        "pattern": "^([a-z0-9].*)-(?:\\d+:)?(.+)-(.+)\\.(.+)$",
                                        "description": "The Name, Epoch, Version, Release, and Architecture (NEVRA) of the source package used to build this package."
                                    },
                                    "module": {
                                        "type": "string",
                                        "pattern": "^(.+):(.+)$",
                                        "description": "The modular information about the package in the format <name>:<stream>."
                                    },
                                    "parent_archs": {
                                        "type": "array",
                                        "description": "This optional field is used with noarch packages to indicate the base architectures the package was included for.",
                                        "items": {
                                            "type": "string"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
```

</details>

### Usage

### DNF plugin

**NOTE**: The plugin is only available for the `dnf4`.

Installation:

```bash
dnf copr enable rpmsoftwaremanagement/manifest-plugin-testing
dnf4 install 'dnf-command(manifest)'
```

Example:

```bash
dnf4 manifest --help
```

### Python API

Installation:

```bash
dnf copr enable rpmsoftwaremanagement/libpkgmanifest-nightly
dnf install python3-libpkgmanifest
```

Example:

```python
import libpkgmanifest.common
import libpkgmanifest.manifest

parser = libpkgmanifest.manifest.Parser()
manifest = parser.parse("./packages.manifest.yaml")

print("manifest major version is:", manifest.version.major)
print("manifest minor version is:", manifest.version.minor)
print("manifest patch version is:", manifest.version.patch)
```

## Implementation

### Prefetching (approach 1)

Prefetching the packages can be done by simply using the `dnf4 manifest` command:

```bash
dnf4 manifest download --destdir "${output_dir}/deps/rpm"
```

The command will handle all RPMs and allow them to be used during the build stage.

TODO

### Prefetching (approach 2)

Prefetching could also be done by manually parsing the manifest file and downloading all RPMs to the
output directory.

TODO

### Hermetic build

A similar approach as for the `rpm` prototype (using createrepo_c and repo file).
