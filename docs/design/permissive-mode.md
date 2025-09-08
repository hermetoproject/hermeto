# Hermeto Permissive Mode in SBOM

## Overview

We have added a CLI option `--mode` to allow users to have some control over the
strictness of the validation checks. It is set to `strict` by default. The other
option is `permissive`.

This document describes the design for tracking permissive mode violations during
SBOM generation in Hermeto. The goal is to provide granular tracking of these
violations in the SBOM.

## Context

Hermeto, in permissive mode, bypasses certain validation checks and continues
processing even when issues are encountered. However, this information is not
currently captured in the generated SBOMs, making it difficult to audit any
violations bypassed during processing.

The strict mode remains as the default, and the permissive mode is reserved for
rare and deliberately audited exceptions. By embedding detailed information into
the SBOM, we will ensure that a more granular and precise policy can be
implemented in the future.

Validation checks that are currently relaxed in permissive mode:

- `gomod` - vendor directory contents differ from go.mod/go.sum after vendoring
- `cargo` - Cargo.lock is out of sync with Cargo.toml (it must exist though)

## Goals

- To record specific violations, not the whole permissive mode
- To be compatible with both supported SBOM formats - CycloneDX and SPDX
- To track violations in machine-readable format for easy parsing and processing

## Potential solutions

### CycloneDX

- JSON Schema: [https://cyclonedx.org/docs/1.6/json](https://cyclonedx.org/docs/1.6/json)
- Validation can be done using the CycloneDX [CLI](https://github.com/CycloneDX/cyclonedx-cli)
tool.

**NOTE:** We are using CycloneDX version 1.6, which is the latest version
at the time of writing.

```bash
cyclonedx validate --input-file bom.json
```

#### Approach 1 (preferred): Top-level annotation with component references

This approach involves adding top-level annotations that describe permissive mode
violations and referencing the affected components by their IDs.

*This is the preferred approach because it promotes separation of concerns. By placing
violation metadata in the top-level annotations array, we keep the components array
clean and focused purely on package descriptors. This approach is highly scalable
and efficient: a single, centralized annotation entry can reference dozens of affected
components by their bom-ref, reducing data duplication and making the SBOM easier
to audit for all policy violations at a glance.*

<details>
<summary>Example</summary>

```json
{
  "bomFormat": "CycloneDX",
  "annotations": [
    {
      "subjects": ["123abc", "123xyz"],
      "annotator": {
        "organization": {
          "name": "red hat"
        }
      },
      "timestamp": "2025-01-15T10:30:00Z",
      "text": "hermeto:permissive-mode:violation:gomod:vendor-directory-changed-after-vendoring"
    }
  ],
  "components": [
    {
      "bom-ref": "123abc",
      "name": "package",
      "properties": [
        {
          "name": "hermeto:found_by",
          "value": "hermeto"
        }
      ],
      "purl": "pkg:golang/github.com/example/package@v1.2.3",
      "type": "library",
      "version": "v1.2.3"
    },
    {
      "bom-ref": "123xyz",
      "name": "package",
      "properties": [
        {
          "name": "hermeto:found_by",
          "value": "hermeto"
        }
      ],
      "purl": "pkg:golang/github.com/another/package@v2.1.0",
      "type": "library",
      "version": "v2.1.0"
    }
  ],
  "specVersion": "1.6",
  "version": 1,
  "metadata": {
    "tools": [
      {
        "vendor": "red hat",
        "name": "hermeto"
      }
    ]
  }
}
```

</details>

#### Approach 2 (alternative): Properties within the components

This approach involves adding properties that describe the violation directly
to each affected component.

<details>
<summary>Example</summary>

Example:

```json
{
  "bomFormat": "CycloneDX",
  "components": [
    {
      "name": "github.com/gin-gonic/gin",
      "properties": [
        {
          "name": "hermeto:found_by",
          "value": "hermeto"
        },
        {
          "name": "hermeto:permissive-mode:violation",
          "value": "gomod:vendor-directory-changed-after-vendoring"
        }
      ],
      "purl": "pkg:golang/github.com/gin-gonic/gin@v1.9.1",
      "type": "library",
      "version": "v1.9.1"
    },
    {
      "name": "github.com/gorilla/mux",
      "properties": [
        {
          "name": "hermeto:found_by",
          "value": "hermeto"
        },
        {
          "name": "hermeto:permissive-mode:violation",
          "value": "gomod:vendor-directory-changed-after-vendoring"
        }
      ],
      "purl": "pkg:golang/github.com/gorilla/mux@v1.8.0",
      "type": "library",
      "version": "v1.8.0"
    }
  ],
  "specVersion": "1.6",
  "version": 1,
  "metadata": {
    "tools": [
      {
        "vendor": "red hat",
        "name": "hermeto"
      }
    ]
  }
}
```

</details>

### SPDX

- JSON Schema: [https://github.com/spdx/spdx-spec/blob/support/2.3/schemas/spdx-schema.json](https://github.com/spdx/spdx-spec/blob/support/2.3/schemas/spdx-schema.json)
- Validation can be done using the SPDX [online](https://tools.spdx.org/app/validate)
tool.

**NOTE:** We are using SPDX version 2.3. The latest version is 3.0.1 at the time
of writing, but we are not planning to upgrade in the near future.

#### Approach 1 (preferred): Annotations within the packages

This approach involves adding annotations directly to each package that describe
permissive mode violations. Annotations are currently used for converting CycloneDX
component properties.

*While top-level annotations would be the ideal approach for centralized violation
tracking (similar to our CycloneDX approach), SPDX 2.3 limitations prevent us
from implementing this. The annotations field within packages is the most practical
and well-supported method for attaching custom, package-specific metadata in
SPDX version 2.3.*

*Since the annotations field is already used in Hermeto to convert CycloneDX
properties, using it for violation tracking maintains consistency and simplifies
the conversion logic between the two SBOM formats.*

<details>
<summary>Example</summary>

```json
{
  "spdxVersion": "SPDX-2.3",
  "dataLicense": "CC0-1.0",
  "SPDXID": "SPDXRef-DOCUMENT",
  "name": "unknown",
  "documentNamespace": "NOASSERTION",
  "creationInfo": {
    "licenseListVersion": "3.24",
    "creators": ["Organization: Red Hat, Inc", "Tool: hermeto"],
    "created": "2025-01-15T10:30:00Z"
  },
  "packages": [
    {
      "name": "github.com/gin-gonic/gin",
      "downloadLocation": "NOASSERTION",
      "SPDXID": "SPDXRef-Package-golang-github.com-gin-gonic-gin-1.9.1",
      "versionInfo": "1.9.1",
      "filesAnalyzed": false,
      "externalRefs": [
        {
          "referenceCategory": "PACKAGE-MANAGER",
          "referenceType": "purl",
          "referenceLocator": "pkg:golang/github.com/gin-gonic/gin@v1.9.1"
        }
      ],
      "annotations": [
        {
          "annotator": "Tool: hermeto",
          "annotationDate": "2025-01-15T10:30:00Z",
          "annotationType": "OTHER",
          "comment": "hermeto:permissive-mode:violation:gomod:vendor-directory-changed-after-vendoring"
        }
      ]
    },
    {
      "name": "github.com/gorilla/mux",
      "downloadLocation": "NOASSERTION",
      "SPDXID": "SPDXRef-Package-golang-github.com-gorilla-mux-1.8.0",
      "versionInfo": "1.8.0",
      "filesAnalyzed": false,
      "externalRefs": [
        {
          "referenceCategory": "PACKAGE-MANAGER",
          "referenceType": "purl",
          "referenceLocator": "pkg:golang/github.com/gorilla/mux@v1.8.0"
        }
      ],
      "annotations": [
        {
          "annotator": "Tool: hermeto",
          "annotationDate": "2025-01-15T10:30:00Z",
          "annotationType": "OTHER",
          "comment": "hermeto:permissive-mode:violation:gomod:vendor-directory-changed-after-vendoring"
        }
      ]
    }
  ],
  "relationships": [
    {
      "spdxElementId": "SPDXRef-DOCUMENT",
      "relationshipType": "DESCRIBES",
      "relatedSpdxElement": "SPDXRef-Package-golang-github.com-gin-gonic-gin-1.9.1"
    },
    {
      "spdxElementId": "SPDXRef-DOCUMENT",
      "relationshipType": "DESCRIBES",
      "relatedSpdxElement": "SPDXRef-Package-golang-github.com-gorilla-mux-1.8.0"
    }
  ]
}
```

</details>

#### Approach 2 (alternative): Top-level annotations with package references

This approach involves adding top-level annotations that describe permissive mode
violations and referencing the affected components by their IDs. Similar to the
CycloneDX approach 1.

Unfortunately, only since SPDX version 3, annotations can become actual SPDX elements
with a `spdxElementId` [property](https://spdx.github.io/spdx-spec/v3.0.1/model/Core/Classes/Annotation/)
which means that we could use the `relationships` field to link them to the affected
packages.

#### Approach 3 (alternative): External references for violation tracking

This approach uses the `externalRefs` field to add custom reference types that describe
permissive mode violations. This method leverages SPDX's existing external reference
system to embed violation metadata in a structured, machine-readable format.

<details>
<summary>Example</summary>

```json
{
  "spdxVersion": "SPDX-2.3",
  "dataLicense": "CC0-1.0",
  "SPDXID": "SPDXRef-DOCUMENT",
  "name": "unknown",
  "documentNamespace": "NOASSERTION",
  "creationInfo": {
    "licenseListVersion": "3.24",
    "creators": ["Organization: Red Hat, Inc", "Tool: hermeto"],
    "created": "2025-01-15T10:30:00Z"
  },
  "packages": [
    {
      "name": "github.com/gin-gonic/gin",
      "downloadLocation": "NOASSERTION",
      "SPDXID": "SPDXRef-Package-golang-github.com-gin-gonic-gin-1.9.1",
      "versionInfo": "1.9.1",
      "filesAnalyzed": false,
      "externalRefs": [
        {
          "referenceCategory": "PACKAGE-MANAGER",
          "referenceType": "purl",
          "referenceLocator": "pkg:golang/github.com/gin-gonic/gin@v1.9.1"
        },
        {
          "referenceCategory": "OTHER",
          "referenceType": "hermeto:permissive-mode:violation",
          "referenceLocator": "gomod:vendor-directory-changed-after-vendoring"
        }
      ]
    },
    {
      "name": "github.com/gorilla/mux",
      "downloadLocation": "NOASSERTION",
      "SPDXID": "SPDXRef-Package-golang-github.com-gorilla-mux-1.8.0",
      "versionInfo": "1.8.0",
      "filesAnalyzed": false,
      "externalRefs": [
        {
          "referenceCategory": "PACKAGE-MANAGER",
          "referenceType": "purl",
          "referenceLocator": "pkg:golang/github.com/gorilla/mux@v1.8.0"
        },
        {
          "referenceCategory": "OTHER",
          "referenceType": "hermeto:permissive-mode:violation",
          "referenceLocator": "gomod:vendor-directory-changed-after-vendoring"
        }
      ]
    }
  ],
  "relationships": [
    {
      "spdxElementId": "SPDXRef-DOCUMENT",
      "relationshipType": "DESCRIBES",
      "relatedSpdxElement": "SPDXRef-Package-golang-github.com-gin-gonic-gin-1.9.1"
    },
    {
      "spdxElementId": "SPDXRef-DOCUMENT",
      "relationshipType": "DESCRIBES",
      "relatedSpdxElement": "SPDXRef-Package-golang-github.com-gorilla-mux-1.8.0"
    }
  ]
}
```

</details>

#### Approach 4 (alternative): Relationships with phony packages

SPDX allows us to define relationships between any two SPDX elements. What we could
do is to create phony packages to represent violations, which could then be linked
via relationships to the affected packages.

While phony packages are not unusual, this approach is questionable, as it pollutes
the package list with non-package entities and could mislead tools that expect
actual software packages.
