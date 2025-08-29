# Hermeto Permissive Mode

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
- Try to avoid adding the same property to every component
- Compatibility with both supported SBOM formats - CycloneDX and SPDX

The strict mode remains as default, and the permissive mode is reserved for
rare and deliberately audited exceptions. By embedding detailed information into
the SBOM, we will ensure that a more granular and precise policy can be
implemented in the future.

CycloneDX schema: [http://cyclonedx.org/docs/1.6/json](http://cyclonedx.org/docs/1.6/json)
SPDX schema: [https://spdx.github.io/spdx-spec/v3.0.1/](https://spdx.github.io/spdx-spec/v3.0.1/)

## Solution

### Option 1

#### CycloneDX

Violations are added as properties in the metadata section:
[https://cyclonedx.org/docs/1.6/json/#metadata_properties](https://cyclonedx.org/docs/1.6/json/#metadata_properties)

Example:

```json
{
    "bomFormat": "CycloneDX",
    "specVersion": "1.4",
    "version": 1,
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
    "components": []
  }
```

#### SPDX

Violations are added as document-level annotations:
[https://spdx.github.io/spdx-spec/v3.0.1/model/Core/Classes/Annotation/](https://spdx.github.io/spdx-spec/v3.0.1/model/Core/Classes/Annotation/)

Example:

```json
{
  "spdxVersion": "SPDX-2.3",
  "SPDXID": "SPDXRef-DOCUMENT",
  "dataLicense": "CC0-1.0",
  "name": "my-project",
  "documentNamespace": "NOASSERTION",
  "creationInfo": {
    "created": "2024-01-15T10:30:00Z",
    "creators": ["Tool: hermeto"]
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
- [ ] Document the behavior for each package manager
- [ ] Add at least one integration test with SPDX format (or convert existing one)
- [ ] Make sure Conforma blocks releases with permissive mode
- [ ] Add mode parameter to prefetch-dependencies task
