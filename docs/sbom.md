# SBOM

Hermeto produces a Software Bill of Materials (SBOM) describing the dependencies
it fetches. This document explains how Hermeto structures the SBOM, what custom
fields it uses, how it maps between supported formats, and how conversion works.

Hermeto supports the following SBOM formats:

- **CycloneDX** [v1.6][] (default)
- **SPDX** [v2.3][] &mdash; Hermeto supports only a *subset* of the SPDX
  specification; the output includes the fields and structure described in this
  document.

The output format could be selected with `--sbom-output-type cyclonedx` or
`--sbom-output-type spdx` when running `hermeto fetch-deps` or
`hermeto merge-sboms`.

## Scheme

Hermeto produces a flat list of components (CycloneDX) or packages (SPDX). There
is no nested tree of dependencies in either format.

### CycloneDX

- The output has a single top-level `components` array.
- There is no `dependencies` array and no parent/child hierarchy.
- All components are peers in one list.
- Hermeto uses only two component types: `library` (default, used for components
  produced by all package managers except generic) and `file` (used for generic
  artifacts such as archives when the generic package manager fetches a
  zip/tarball).

### SPDX

- The output has a `packages` array and a `relationships` array.
- `packages`: the first entry is a synthetic root with
  `SPDXID`:&nbsp;`SPDXRef-DocumentRoot-File-`
  (empty `name` and `versionInfo`); the rest are actual packages. The root is
  required by the SPDX specification. Every SPDX document must have a document
  root element that the rest of its contents are related to.
- `relationships`: every real package is linked directly to the root via
  `CONTAINS` and `DESCRIBES` relationships.
- SPDX does not have an equivalent "library" vs "file" component type; all
  packages are listed without that distinction. As a result, a `file` component
  in CycloneDX that is converted to SPDX and back will become a `library`
  component — this is a known limitation of the round-trip conversion.

## Custom data Hermeto reports

### Dependency properties

Hermeto attaches properties from a fixed set to all dependencies. These
properties carry metadata about dependencies they are attached to. These
properties are stored in the CycloneDX `components[].properties` array; in SPDX
they are represented as package annotations (see
[CycloneDX to SPDX mapping](#cyclonedx-to-spdx-mapping)). Property names use
`hermeto:` as a prefix, except for the two [standard CycloneDX npm properties][]
which use the `cdx:` prefix defined by the CycloneDX specification.

| Property name | Meaning | When it appears |
| ------------- | ------- | --------------- |
| `hermeto:found_by` | Tool that added the dependency | Always |
| `hermeto:missing_hash:in_file` | Path to a lockfile with a missing checksum | When there is a lockfile with some checksums missing |
| `hermeto:bundler:package:binary` | Bundler gem is a platform-specific binary | When the gem is a platform-specific binary |
| `cdx:npm:package:bundled` | Npm package is bundled | When the package is bundled within another |
| `cdx:npm:package:development` | Npm package is a dev dependency | When the package is a development dependency |
| `hermeto:pip:package:binary` | Pip package is a binary wheel | When the package is a binary wheel |
| `hermeto:pip:package:build-dependency` | Pip package is a build-time dependency | When the package is required at build time |
| `hermeto:rpm_modularity_label` | RPM modularity label | When applicable for RPM dependencies |
| `hermeto:rpm_summary` | RPM summary | When the RPM summary is included in the SBOM |

### Backend and source information

Unlike the dependency properties above, which are stored as flat key/value pairs
in `components[].properties` (CycloneDX) or package annotations (SPDX), the
following two kinds of information use different SBOM constructs because they
describe the component's provenance rather than its attributes.

Hermeto attaches two kinds of provenance information to dependencies:

1. **Which package manager processed the dependency**: Consumers can identify
   which backend (e.g. gomod, pip, npm) produced each dependency. Experimental
   backends (name starting with `x-`) are tagged as experimental.

2. **Actual source URL**: When a dependency was downloaded via an artifact
   repository manager (like JFrog Artifactory or Sonatype Nexus), Hermeto records
   the actual download location in addition to any normal distribution reference.
   When multiple URLs are available, all are included in the SBOM, but there is
   no indication of which one was actually used (some package managers cannot
   provide this information).

### Backend and source representation in SBOMs

**CycloneDX**:

- **Package manager**: Hermeto uses top-level annotations to tag which backend
  produced which components. Format: `hermeto:backend:<backend_name>` (e.g.
  `hermeto:backend:gomod`). For experimental backends:
  `hermeto:backend:experimental:<backend_name>`. Annotations reference
  components by `bom-ref` (which equals the component's `purl`).
- **Actual source URL**: Hermeto adds an external reference with
  `type`:&nbsp;`"distribution"`, `comment`:&nbsp;`"proxy URL"`, and the `url`
  set to the actual download location. Multiple URLs are represented as multiple
  such external references.

**SPDX**:

- **Package manager**: The backend annotations become package annotations on the
  corresponding packages (see [CycloneDX to SPDX mapping](#cyclonedx-to-spdx-mapping)).
- **Actual source URL**: Mapped to the package field `sourceInfo`
  (semicolon-separated if there are several).

## Main package and versioning

### No designated "main" package in the SBOM

The SBOM does **not** mark which dependency is the main application. All are
listed as peers. The SBOM alone does not distinguish "the app" from its
dependencies.

### Versioning

- **Version**: Each dependency can have a version, taken from the package
  manager's data (lockfile, metadata, or similar). Some dependencies have no
  version; Hermeto omits the field in that case. For further details see
  [Reasons for lack of versions in dependencies](#reasons-for-lack-of-versions-in-dependencies).
- **Purl**: The main identifier for each dependency is its purl (package URL).
  The purl can still include version-like information (e.g. a Git commit) even
  when the version field is missing.
- **SBOM format version**: The SBOM file itself carries a format version
  &mdash; the schema version, not the application's version.
  CycloneDX:&nbsp;`specVersion` (e.g. `"1.6"`) and a top-level integer `version`
  (BOM revision). SPDX:&nbsp;`spdxVersion` (e.g. `"SPDX-2.3"`).

### Reasons for lack of versions in dependencies

Some dependencies in the SBOM may have no version; when and why depends on the
ecosystem:

- **Go (gomod)**: The standard library has no version. Modules referenced via
  `replace` directives pointing to local paths may also lack one; other modules
  get one when resolved.
- **Yarn**: Workspace and linked packages often have no version; Hermeto omits
  placeholders.
- **Cargo**: Missing if a local path dependency omits it, or if a workspace
  member inherits from `[workspace.package]` and that field is unset.
- **npm**: Taken from the lockfile; rarely missing.
- **pip**: PyPI packages always have a version; VCS or local path sources may
  not. May also be absent if the main project uses dynamic versioning
  (e.g. setuptools-scm).

## CycloneDX to SPDX mapping

When SPDX output is requested (e.g. via `--sbom-output-type spdx`), CycloneDX
data is mapped to SPDX as follows.

| CycloneDX | SPDX |
| --------- | ---- |
| `Component` | `SPDXPackage` |
| Component identity | `SPDXID`: `SPDXRef-Package-{name}-{version}-{hash}` (or `SPDXRef-Package-{name}-{hash}` when version is `None`). The hash is the SHA-256 hex digest of the JSON-serialized object `{"name", "version", "purl"}` with keys sorted. The idstring is sanitized per SPDX rules (only letters, digits, `.`, `-`). `versionInfo` is set to the component's version or omitted when `None`. |
| `purl` | `externalRefs`: one entry with `referenceCategory=PACKAGE-MANAGER`, `referenceType=purl`, `referenceLocator=<purl>` |
| `properties` | Package `annotations`: each property encoded as JSON `{"name":"...","value":"..."}` in `comment`; `annotator` is `Tool: hermeto:jsonencoded`. |
| Top-level `annotations` (by bom-ref) | Same package annotations: `comment` = annotation text |
| ExternalReference (`type=distribution`, `comment=proxy URL`) | `sourceInfo` (semicolon-separated if multiple) |
| (no root in CycloneDX) | A synthetic root package `SPDXRef-DocumentRoot-File-` is created with `name=""` and `versionInfo=""`. The document describes the root; the root contains every package. |
| `metadata.tools` | `creationInfo.creators` (`Tool:` / `Organization:`) |

## SPDX to CycloneDX

When SPDX is converted to CycloneDX (e.g. when merging an SPDX SBOM with a
CycloneDX one), the following applies:

- Each SPDX package becomes one or more CycloneDX components. Because CycloneDX
  allows only one purl per component, an SPDX package with multiple purls in
  `externalRefs` becomes multiple components.
- `versionInfo` is passed through as the component's `version`.
- Annotations whose `annotator` ends with `:jsonencoded` are parsed as
  properties; others are stored as top-level annotations.
- `sourceInfo` is converted back to ExternalReferences with `type=distribution`
  and `comment=proxy URL`.

**Limitation**: An SPDX package that has multiple purls in `externalRefs` becomes
multiple CycloneDX components when converted to CycloneDX, because CycloneDX does
not support multiple purls on one component.

## Merging SBOMs

When SBOMs are merged, merging is performed in CycloneDX form. If an SPDX SBOM
is passed to `merge-sboms`, it is converted to CycloneDX first, then merged.
The merged result can be output as either CycloneDX or SPDX via
`--sbom-output-type`.

[v1.6]: https://cyclonedx.org/docs/1.6/json
[v2.3]: https://spdx.github.io/spdx-spec/v2.3/
[standard CycloneDX npm properties]: https://github.com/CycloneDX/cyclonedx-property-taxonomy/blob/main/cdx/npm.md#cdxnpmpackage-namespace-taxonomy
