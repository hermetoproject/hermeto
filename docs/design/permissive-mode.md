# Hermeto Permissive Mode in SBOM

## Overview

This document describes the design for tracking permissive mode violations during
SBOM generation in Hermeto. The goal is to provide granular tracking of
violations that occur when running in permissive mode.

## Context

Hermeto, in permissive mode, bypasses certain validation checks and continues
processing even when issues are encountered. However, this information is not
currently captured in the generated SBOMs, making it difficult to audit what
violations occurred during processing.

The strict mode remains as the default, and the permissive mode is reserved for
rare and deliberately audited exceptions. By embedding detailed information into
the SBOM, we will ensure that a more granular and precise policy can be
implemented in the future.

Validation checks that are currently relaxed in the permissive mode:

- `gomod` - vendor directory contents differ from go.mod/go.sum after vendoring
- `cargo` - Cargo.lock is out of sync with Cargo.toml (it must exist though)

## Goals

- Record specific violation, not the whole permissive mode
- Compatibility with both supported SBOM formats - CycloneDX and SPDX
- Machine-readable format for easy parsing and processing

## Solution

### CycloneDX

JSON Schema: [https://cyclonedx.org/docs/1.4/json](https://cyclonedx.org/docs/1.4/json)

Validation can be done using the CycloneDX [CLI](https://github.com/CycloneDX/cyclonedx-cli)
tool.

```bash
cyclonedx validate --input-file bom.json
```

#### Approach 1: Linked-Component Properties

This approach involves adding properties or annotations directly to each component
that describes the violation.

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
          "name": "hermeto:permissive-mode:violation:gomod:vendor-mismatch",
          "value": "vendor directory changed after vendoring"
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
          "name": "hermeto:permissive-mode:violation:gomod:vendor-mismatch",
          "value": "vendor directory changed after vendoring"
        }
      ],
      "purl": "pkg:golang/github.com/gorilla/mux@v1.8.0",
      "type": "library",
      "version": "v1.8.0"
    }
  ],
  "specVersion": "1.4",
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

#### Approach 2: Top-Level Annotation with Component References

This approach involves adding top-level annotations that describe permissive mode
violations and referencing the affected components by their IDs. **This requires
an upgrade to CycloneDX version 1.6 schema.**

Example:

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
      "text": "vendor directory changed after vendoring"
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

### SPDX

JSON Schema: [https://github.com/spdx/spdx-spec/blob/support/2.3/schemas/spdx-schema.json](https://github.com/spdx/spdx-spec/blob/support/2.3/schemas/spdx-schema.json)

Validation can be done using the SPDX [online](https://tools.spdx.org/app/validate)
tool.

**Note:** Relationships cannot be used for tracking permissive mode violations.
The fundamental issue is that relationships require proper SPDX elements to
relate to, but permissive mode violations are not first-class objects in the SPDX
specification. While it would be possible to create artificial elements to represent
violations, this would require crafting non-standard objects that don't align with
SPDX's intended data model and could confuse existing SPDX tooling.

An alternative approach would be to create "phony packages" that represent violations
as SPDX package elements, which could then be linked via relationships. While phony
packages are not non-standard objects, this approach is questionable as it pollutes
the package list with non-package entities and could mislead tools that expect
actual software packages.

#### Approach 1: Linked-Component Annotations

This approach involves adding annotations directly to each package that describe
permissive mode violations. **Annotations are currently used for converting CycloneDX
component properties.**

Example:

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
          "comment": "vendor directory changed after vendoring"
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
          "comment": "vendor directory changed after vendoring"
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

#### Approach 2: Top-Level Annotation with Package References

This approach involves adding top-level annotations that describe permissive mode
violations, and referencing the affected packages by their IDs.

Blocked by [https://github.com/spdx/spdx-spec/issues/1147](https://github.com/spdx/spdx-spec/issues/1147).
Currently, it is not possible to link the annotations to the packages or any
other elements in the document.

#### Approach 3: External References for Violation Tracking

This approach uses the `externalRefs` field to add custom reference types that describe
permissive mode violations. This method leverages SPDX's existing external reference
system to embed violation metadata in a structured, machine-readable format.

Example:

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

## Implementation

- Record permissive violations in the SBOM
- CycloneDX: bump the schema version to 1.6
- Document the behavior for each package manager
- Add at least one integration test with SPDX format (or convert existing ones)
- Add SPDX schema validation for integration tests
- Add mode parameter to prefetch-dependencies task in Konflux
