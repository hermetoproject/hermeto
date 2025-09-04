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

Current validation checks that are relaxed in the permissive mode:

- `gomod` - the output directory is changed after vendoring
- `cargo` - the lockfile is not up to date with the project file

## Goals

- Record specific violation, not the whole permissive mode
- Compatibility with both supported SBOM formats - CycloneDX and SPDX
- Machine-readable format for easy parsing and processing

The strict mode remains as default, and the permissive mode is reserved for
rare and deliberately audited exceptions. By embedding detailed information into
the SBOM, we will ensure that a more granular and precise policy can be
implemented in the future.

Currently supported SBOM formats and their respective versions in Hermeto:

- CycloneDX schema [1.4](http://cyclonedx.org/docs/1.4/json)
- SPDX schema [2.3](https://spdx.github.io/spdx-spec/v2.3/)

## Solution

### Approach 1: Linked-Component Properties

This approach involves adding properties or annotations directly to each component
that violates a policy. This means that if a single software package has multiple
issues, each issue would be documented within that component's metadata.

#### Pros

- **Clarity and Readability:** This method is highly transparent, allowing
  anyone reviewing the SBOM to immediately see which specific component is associated
  with a violation.
- **Automated Analysis:** It is more beneficial for automated tools and analyzers,
  as they can easily identify and act on the violation data without needing to
  cross-reference multiple sections of the document.
- **Formal and Robust:** It aligns with a more formal and structured approach,
  making the SBOM more robust and easier to process programmatically.

#### Cons

- **Data Duplication:** If a policy violation affects a large number of components,
this could lead to significant data duplication, creating a verbose document.

### Approach 2: Top-Level Annotation

This approach involves adding a single, top-level property or annotation to the
SBOM that lists all the components that are in violation.

#### Pros

- **Simplicity and Conciseness:** It avoids the data duplication of the
linked-component approach, potentially making the SBOM smaller and cleaner.

#### Cons

- **Reduced Context:** This method provides less context for automated analyzers,
as they would have to read a general list and then find the corresponding components,
which is less efficient.
- **Less Intuitive:** It's less intuitive for a user or a tool to see a component
and its associated violation together.

### CycloneDX

#### Violations are added as properties in the metadata section

[https://cyclonedx.org/docs/1.4/json/#metadata_properties](https://cyclonedx.org/docs/1.4/json/#metadata_properties)

Example:

```json
{
    "bomFormat": "CycloneDX",
    "components": [],
    "metadata": {
      "tools": [
        {
          "vendor": "red hat",
          "name": "hermeto"
        }
      ],
      "properties": [
        {
          "name": "hermeto:permissive_mode:cargo_lockfile",
          "value": "Cargo.lock is not synced with Cargo.toml"
        },
        {
          "name": "hermeto:permissive_mode:gomod_vendor_dir",
          "value": "Go vendor directory inconsistent with go.mod"
        },
      ]
    },
    "specVersion": "1.4",
    "version": 1
  }
```

#### Violations are added as annotations in the metadata section

(Assuming we will use version 1.6 of the CycloneDX schema)

[https://cyclonedx.org/docs/1.6/json/#annotations](https://cyclonedx.org/docs/1.6/json/#annotations)

Annotations contain comments, notes, explanations, or similar textual content
which provide additional context to the object(s) being annotated. They are
often automatically added to a BOM via a tool or as a result of manual review
by individuals or organizations. Annotations can be independently signed and
verified using digital signatures [^1].

[^1]: [https://cyclonedx.org/specification/overview/](https://cyclonedx.org/specification/overview/)

Example:

```json
{
    "bomFormat": "CycloneDX",
    "components": [],
    "annotations": [
      {
        "bom-ref": "hermeto:permissive_mode:cargo_lockfile",
        "subjects": [
          {
            "bom-ref": "hermeto:permissive_mode:cargo_lockfile"
          }
        ],
        "annotator": "Tool: hermeto",
        "timestamp": "2025-01-15T10:30:00Z",
        "text": "Cargo.lock is not synced with Cargo.toml",
        "signature": null
      },
      {
        "bom-ref": "hermeto:permissive_mode:gomod_vendor_dir",
        "subjects": [
          {
            "bom-ref": "hermeto:permissive_mode:gomod_vendor_dir"
          }
        ],
        "annotator": "Tool: hermeto",
        "timestamp": "2025-01-15T10:30:00Z",
        "text": "Go vendor directory inconsistent with go.mod",
        "signature": null
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

### SPDX

#### Violations are added as document-level annotations

[https://spdx.github.io/spdx-spec/v3.0.1/model/Core/Classes/Annotation/](https://spdx.github.io/spdx-spec/v3.0.1/model/Core/Classes/Annotation/)

Example:

```json
{
  "spdxVersion": "SPDX-2.3",
  "dataLicense": "CC0-1.0",
  "SPDXID": "SPDXRef-DOCUMENT",
  "name": "alpine",
  "documentNamespace": "NOASSERTION",
  "creationInfo": {
    "licenseListVersion": "3.22",
    "creators": [
      "Tool: hermeto"
    ],
    "created": "2025-01-15T10:30:00Z"
  },
  "packages": [],
  "relationships": [],
  "annotations": [
    {
      "annotator": "Tool: hermeto",
      "annotationDate": "2025-01-15T10:30:00Z",
      "annotationType": "OTHER",
      "comment": "Cargo.lock is not synced with Cargo.toml"
    },
    {
      "annotator": "Tool: hermeto",
      "annotationDate": "2025-01-15T10:30:00Z",
      "annotationType": "OTHER",
      "comment": "Go vendor directory inconsistent with go.mod"
    }
  ]
}
```

## Implementation

- [ ] Record permissive violations in the SBOM
- [ ] Investigate bumping the schema version (with backward compatibility)
- [ ] Document the behavior for each package manager
- [ ] Add at least one integration test with SPDX format (or convert existing one)
- [ ] Make sure Conforma blocks releases with permissive mode
- [ ] Add mode parameter to prefetch-dependencies task
