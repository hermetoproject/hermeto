# SBOM

Hermeto produces a Software Bill of Materials (SBOM) describing the dependencies
it fetches. This document explains how Hermeto structures the SBOM, what custom
fields it uses, how it maps between supported formats, and how conversion works.

Hermeto supports the following SBOM formats:

- **CycloneDX** [v1.6][] (default)
- **SPDX** [v2.3][]

The output format can be selected with `--sbom-output-type cyclonedx` or
`--sbom-output-type spdx` when running `hermeto fetch-deps` or
`hermeto merge-sboms`.

Hermeto represents SBOMs internally as CycloneDX and can convert them to SPDX.
Both formats use only a subset of their respective specifications; the subsets
map into each other. The mapping is documented in
[CycloneDX to SPDX mapping](#cyclonedx-to-spdx-mapping).

## Scheme

Hermeto produces a flat list of components (CycloneDX) or packages (SPDX). There
is no nested tree of dependencies in either format.

### CycloneDX

All packages processed by Hermeto are represented by CycloneDX components. In a
SBOM produced by Hermeto components form a single flat top-level `components` array,
no hierarchy is preserved withing it. Of all possible component types available
in CycloneDX spec Hermeto uses only two component types: `library` (default, used
for components produced by all package managers except generic) and `file` (used
for URL-based generic artifacts such as archives when the generic package manager
fetches a zip/tarball).

### SPDX

At the time of writing, SBOMs are represented internally as CycloneDX. SPDX is
produced with a converter (see [CycloneDX to SPDX mapping](#cyclonedx-to-spdx-mapping)).
SPDX SBOMs provided by Hermeto have the following properties:

- The output has a `packages` array and a `relationships` array.
- `packages`: the first entry is a synthetic root with
  `SPDXID`:&nbsp;`SPDXRef-DocumentRoot-File-`
  (empty `name` and `versionInfo`); the rest are SPDX package entries for actual
  packages. The root is required by the SPDX specification.
- `relationships`: the document describes the root
  (`SPDXRef-DOCUMENT` &rarr; `SPDXRef-DocumentRoot-File-` via `DESCRIBES`);
  every real package is linked to the root via `CONTAINS`.
- Unlike CycloneDX SPDX does not have direct equivalents to "library" or "file"
  component types; all packages are listed without that distinction. As a result,
  a `file` component in CycloneDX that is converted to SPDX and back will become
  a `library` component &mdash; this is a known limitation.

## Custom data Hermeto reports

### Dependency properties

Hermeto attaches properties from a fixed set to all dependencies. These
properties carry metadata about dependencies they are attached to. These
properties are stored in the CycloneDX `components[].properties` array; in SPDX
they are represented as package annotations (see
[CycloneDX to SPDX mapping](#cyclonedx-to-spdx-mapping)). Property names use
`hermeto:` as a prefix, except for the two [standard CycloneDX npm properties][]
which use the `cdx:` prefix defined by the CycloneDX specification. The table
below lists all custom properties:

| Property name | Meaning | When it appears |
| ------------- | ------- | --------------- |
| `hermeto:found_by` | Tool that added the dependency | Always |
| `hermeto:missing_hash:in_file` | Path to a lockfile with a missing checksum | When there is a lockfile with some checksums missing |
| `hermeto:bundler:package:binary` | Bundler gem is a platform-specific binary | When the gem is a platform-specific binary |
| `cdx:npm:package:bundled` | Npm package is bundled | When the package is bundled within another |
| `cdx:npm:package:development` | Npm package is a dev dependency | When the package is a development dependency |
| `hermeto:pip:package:binary` | Pip package is a binary wheel | When the package is a binary wheel |
| `hermeto:pip:package:build-dependency` | Pip package is a build-time dependency | When the package is required at build time |
| `hermeto:rpm_modularity_label` | RPM modularity label | When the RPM has a modularity label |
| `hermeto:rpm_summary` | RPM summary | [When you set `include_summary_in_sbom` to `true`](rpm.md#basic-options) |

### Backend and source information

Unlike the dependency properties above, which are stored as flat key/value pairs
in `components[].properties` (CycloneDX) or package annotations (SPDX), backend
and source information use other SBOM fields because they describe where a
component came from, not arbitrary key/value metadata on the component.

**Backends** &mdash; lets consumers know which backend (e.g. gomod, pip, npm)
produced each dependency. Experimental backends (name starting with `x-`) are
tagged as experimental. Supported SBOM types represent them like follows:

- **CycloneDX**: Top-level annotations. Text format `hermeto:backend:<backend_name>`
  (e.g. `hermeto:backend:gomod`); for experimental backends,
  `hermeto:backend:experimental:<backend_name>`. Each annotation references a
  component by `bom-ref` (which equals the component's `purl`).
- **SPDX**: Those backend tags become package annotations on the matching
  packages (see [CycloneDX to SPDX mapping](#cyclonedx-to-spdx-mapping)).

**Actual source URL** &mdash; When a dependency was downloaded via an artifact
repository manager (e.g. JFrog Artifactory or Sonatype Nexus), Hermeto records
the real download location as well as the usual distribution reference. If
several URLs are known, all are recorded; the SBOM does not say which URL was
actually used (some backends cannot provide that). It is represented as follows:

- **CycloneDX**: An external reference with `type`:&nbsp;`"distribution"`,
  `comment`:&nbsp;`"proxy URL"`, and `url` set to the download location. Multiple
  URLs are multiple such references.
- **SPDX**: The same information is mapped to the package field `sourceInfo`
  (semicolon-separated when there are several).

## Main package representation

The SBOM does **not** mark which dependency is the main package. All are
listed as peers. The SBOM alone does not distinguish "the app" from its
dependencies.

## Versioning

Hermeto provides several versioning mechanisms in SBOMs:

- **Version**: Each dependency can have a version, taken from the package
  manager's data (lockfile, metadata, or similar). Some dependencies have no
  version; Hermeto omits the field in that case. For further details see
  [Reasons for lack of versions in some dependencies](#reasons-for-lack-of-versions-in-some-dependencies).
- **PURL**: The main identifier for each dependency is its PURL (package URL).
  The PURL can still include version-like information (e.g. a Git commit) even
  when the version field is missing.
- **SBOM format version**: The SBOM file itself carries a format version
  &mdash; the schema version, not the application's version.
  CycloneDX:&nbsp;`specVersion` (e.g. `"1.6"`) and a top-level integer `version`
  (BOM revision). SPDX:&nbsp;`spdxVersion` (e.g. `"SPDX-2.3"`).

### Reasons for lack of versions in some dependencies

Some dependencies in the SBOM may have no version; when and why depends on the
ecosystem:

- **Go (gomod)**: The standard library version is tied to the Go runtime, but
  Hermeto omits it because the Go version used during pre-fetching may differ
  from the one used in the actual hermetic build; recording it could lead
  vulnerability analyzers to incorrectly flag versions and to incorrectly apply
  a particular provenance policy. Modules referenced via `replace` directives
  pointing to local paths may also lack a version; other modules get one when
  resolved.
- **Yarn**: Workspace and linked packages often have no version.
- **Cargo**: Missing if a local path dependency omits it, or if a workspace
  member inherits from `[workspace.package]` and that field is unset.
- **npm**: Version is taken from the lock file, however in rare cases it could
  be missing.
- **pip**: PyPI packages always have a version; VCS or local path sources may
  not. May also be absent if the main project uses dynamic versioning
  (e.g. setuptools-scm).

## Merging SBOMs

Merging is only meant for **SBOMs produced by Hermeto** (for example from
`fetch-deps` or an earlier successful merge) due to the fact that Hermeto
supports only subsets of SBOM formats. Input files must match Hermeto's
CycloneDX or SPDX model; arbitrary SBOMs from other tools are not supported and
will be rejected as invalid.

## CycloneDX to SPDX mapping

Hermeto forms the SBOM as CycloneDX first; this section describes the
translation used when SPDX output is requested (e.g. via
`--sbom-output-type spdx`).

| CycloneDX | SPDX |
| --------- | ---- |
| `Component` | `SPDXPackage` |
| Component identity | `SPDXID`: `SPDXRef-Package-{name}-{version}-{hash}` (or `SPDXRef-Package-{name}-{hash}`). The hash is the SHA-256 hex digest of a JSON object with keys `name`, `version`, and `purl`, serialized with keys sorted. The idstring is sanitized per SPDX rules (only letters, digits, `.`, `-`). `versionInfo` is set to the component's version or omitted when `None`. |
| `purl` | `externalRefs`: one entry with `referenceCategory=PACKAGE-MANAGER`, `referenceType=purl`, `referenceLocator=<purl>` |
| `properties` | Package `annotations`: each property encoded as JSON `{"name":"...","value":"..."}` in `comment`; `annotator` is `Tool: hermeto:jsonencoded`. |
| Top-level `annotations` (by bom-ref) | Per-package annotations containing the same values as CycloneDX top-level annotations: `comment` = annotation text |
| ExternalReference (`type=distribution`, `comment=proxy URL`) | `sourceInfo` (semicolon-separated if multiple) |
| (no root in CycloneDX) | A synthetic root package `SPDXRef-DocumentRoot-File-` is created with `name=""` and `versionInfo=""`. The document describes the root; the root contains every package. |
| `metadata.tools` | `creationInfo.creators` (`Tool:` / `Organization:`) |

## SPDX to CycloneDX

When SPDX is converted to CycloneDX (e.g. when merging an SPDX SBOM with a
CycloneDX one), the following applies:

- Each SPDX package becomes one or more CycloneDX components. Because CycloneDX
  allows only one PURL per component, an SPDX package with multiple PURLs in
  `externalRefs` becomes multiple components.
- `versionInfo` is passed through as the component's `version`.
- Annotations whose `annotator` ends with `:jsonencoded` are parsed as
  properties; others are stored as top-level annotations.
- `sourceInfo` is converted back to ExternalReferences with `type=distribution`
  and `comment=proxy URL`.

**Limitation**: An SPDX package that has multiple PURLs in `externalRefs` becomes
multiple CycloneDX components when converted to CycloneDX, because CycloneDX does
not support multiple PURLs on one component.

[v1.6]: https://cyclonedx.org/docs/1.6/json
[v2.3]: https://spdx.github.io/spdx-spec/v2.3/
[standard CycloneDX npm properties]: https://github.com/CycloneDX/cyclonedx-property-taxonomy/blob/main/cdx/npm.md#cdxnpmpackage-namespace-taxonomy
