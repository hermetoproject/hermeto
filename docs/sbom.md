# SBOM documentation

Hermeto produces a Software Bill of Materials (SBOM) describing the dependencies
it fetches. This document explains how the SBOM is structured, what custom
fields we use, how we map between supported formats, and how conversion works.

Hermeto supports the following SBOM formats:

- **CycloneDX** [v1.6][] (default)
- **SPDX** [v2.3][] &mdash; we support only a *subset* of the SPDX specification;
  our output includes the fields and structure described in this
  document.

You can choose the output format with `--sbom-output-type cyclonedx` or
`--sbom-output-type spdx` when running `hermeto fetch-deps` or `hermeto merge-sboms`.

## Scheme

Hermeto produces a flat list of components (CycloneDX) or packages (SPDX). There
is no nested tree of dependencies in either format.

### CycloneDX

- The output has a single top-level `components` array.
- There is no `dependencies` array and no parent/child hierarchy.
- All components are peers in one list.

### SPDX

- The output has a `packages` array and a `relationships` array.
- `packages`: the first entry is a synthetic root with
  `SPDXID`:&nbsp;`SPDXRef-DocumentRoot-File-`
  (empty `name` and `versionInfo`); the rest are actual packages.
- `relationships`: shows that every real package in the list is directly linked
  to the root, using simple "contains" and "describes" relationships without
  referencing the exact field values.

## Custom fields we report

### Dependency properties

We attach a fixed set of custom properties to dependencies. All are stored in
the CycloneDX `components[].properties` array; in SPDX they are represented
as package annotations (see [CycloneDX to SPDX mapping](#cyclonedx-to-spdx-mapping)).
Property names use the application name as prefix (e.g. `hermeto:*`).

| Property name | Meaning | When it appears |
| ------------- | ------- | --------------- |
| `<app>:found_by` | Tool that added the dependency | Always |
| `<app>:missing_hash:in_file` | Lockfile path where a hash was missing | When checksum was missing in that file |
| `<app>:bundler:package:binary` | Bundler gem is a platform-specific binary | For platform-specific binary gems |
| `cdx:npm:package:bundled` | Npm package is bundled | When the package is bundled within another |
| `cdx:npm:package:development` | Npm package is a dev dependency | For development dependencies |
| `<app>:pip:package:binary` | Pip package is a binary wheel | For binary wheel distributions |
| `<app>:pip:package:build-dependency` | Pip package is a build-time dependency | For packages required at build time |
| `<app>:rpm_modularity_label` | RPM modularity label | For RPM dependencies when applicable. |
| `<app>:rpm_summary` | RPM summary | For RPM dependencies when included in SBOM. |

### Backend and source information we add

We attach two kinds of custom information to dependencies:

1. **Which package manager processed the dependency**: So consumers know which
   backend (e.g. gomod, pip, npm) produced each dependency. For experimental
   backends (name starting with `x-`), we tag them as experimental.

2. **Actual source (proxy URL)**: When a dependency was downloaded via a proxy,
   we record the actual download location (proxy URL), in addition to any
   normal distribution reference. Multiple proxy URLs are represented when
   applicable.

#### How it is represented

**CycloneDX**:

- **Package manager**: We use top-level annotations to tag which backend
  produced which components. The prefix is the application name (e.g.
  `hermeto`). Format: `<app>:backend:<backend_name>` (e.g.
  `hermeto:backend:gomod`). For experimental backends:
  `<app>:backend:experimental:<backend_name>`. Annotations reference components
  by `bom-ref` (which equals the component's `purl`).
- **Proxy URL**: We add an external reference with `type`:&nbsp;`"distribution"`,
  `comment`:&nbsp;`"proxy URL"`, and the `url` set to the actual download location.
  Multiple proxy URLs are represented as multiple such external references.

**SPDX**:

- **Package manager**: The backend annotations become package annotations on the
  corresponding packages (see [CycloneDX to SPDX mapping](#cyclonedx-to-spdx-mapping)).
- **Proxy URL**: Mapped to the package field `sourceInfo` (semicolon-separated
  if there are several).

## How we report dependencies (library vs file)

**CycloneDX**: We use only two component types in the `components` array:

- `library` (default): used for almost all components (gomod, npm, pip,
  cargo, bundler, rpm, etc.).
- `file`: used for generic artifacts such as archives (e.g. when the
  generic package manager fetches a zip/tarball).

We do not use other CycloneDX component types (e.g. `application`, `container`,
`firmware`).

**SPDX**: The SPDX output does not encode an
equivalent “library” vs “file” type per package; all packages are listed
without that distinction.

## Main package and versioning

### No designated “main” package in the SBOM

The SBOM does **not** mark which dependency is the
main application. All are listed as peers. You cannot tell from the SBOM alone
which one is "the app" and which are its dependencies.

### Versioning

- **Version**: Each dependency can have a version, taken from the package
  manager’s data (lockfile, metadata, or similar). Some dependencies can have no
  version, we report no version in such case. For further details see
  [the corresponding section](#when-a-dependency-has-no-version).
- **Purl**: The main identifier for each dependency is its purl (package URL).
  The purl can still include version-like information (e.g. a Git
  commit) even when the version field is missing.
- **SBOM format version**: The SBOM file itself has a format version
  &mdash; the schema version, not your application’s version.
  CycloneDX:&nbsp;`specVersion` (e.g. `"1.6"`) and a top-level integer `version`
  (BOM revision). SPDX:&nbsp;`spdxVersion` (e.g. `"SPDX-2.3"`).

### When a dependency has no version

Some dependencies in the SBOM may have no version shown. Common reasons:

- **The ecosystem doesn’t provide one**: e.g. Go’s standard library packages
  have no version in the Go toolchain.
- **We don’t treat the value as a version**: e.g. Yarn uses a placeholder for
  workspace or linked packages; we omit it so the SBOM doesn’t show a fake
  version.
- **The source doesn’t define it**: e.g. a dependency installed from a VCS URL
  might not have a tagged version.

Per ecosystem:

- **Go (gomod)**: Standard library packages have no version. Other modules
  get a version when resolved.
- **Yarn**: Workspace and linked packages often have no version; we omit
  placeholder values.
- **Cargo**: Version comes from the package definition or workspace; it can be
  missing if neither specifies it.
- **npm**: Version comes from the lockfile; rarely missing.
- **pip**: PyPI dependencies have a version; dependencies from VCS or other
  sources may not. The main project’s version comes from its metadata
  (pyproject.toml, setup.py, etc.).

## CycloneDX to SPDX mapping

When we convert from CycloneDX to SPDX (e.g. via `--sbom-output-type spdx`),
we use the following mapping.

| CycloneDX | SPDX |
| --------- | ---- |
| `Component` | `SPDXPackage` |
| Component identity | `SPDXID`: `SPDXRef-Package-{name}-{version}-{hash}` (or `SPDXRef-Package-{name}-{hash}` when version is None). The hash is the SHA-256 hex digest of the JSON-serialized object `{"name", "version", "purl"}` with keys sorted; the idstring is sanitized per SPDX rules (only letters, digits, `.`, `-`). `versionInfo` is set to the component’s version or is omitted when `None`. |
| `purl` | `externalRefs`: one entry with `referenceCategory=PACKAGE-MANAGER`, `referenceType=purl`, `referenceLocator=<purl>` |
| `properties` | Package `annotations`: each property as JSON `{"name":"...","value":"..."}` in `comment`; `annotator` is `Tool: <app_name>:jsonencoded` (e.g. `hermeto` or `cachi2`). |
| Top-level `annotations` (by bom-ref) | Same package annotations: `comment` = annotation text |
| ExternalReference (type=distribution, comment=proxy URL) | `sourceInfo` (semicolon-separated if multiple) |
| (no root in CycloneDX) | We create a synthetic root package `SPDXRef-DocumentRoot-File-` with `name=""`, `versionInfo=""`; DOCUMENT DESCRIBES root; root CONTAINS every package. |
| `metadata.tools` | `creationInfo.creators` (Tool: / Organization:) |

## SPDX to CycloneDX

When converting SPDX back to CycloneDX (e.g. when merging an SPDX SBOM with a
CycloneDX one):

- Each SPDX package becomes one or more CycloneDX components (because
CycloneDX allows only one purl per component; an SPDX package with multiple
purls in `externalRefs` becomes multiple components).
- `versionInfo` is passed through as the component’s `version`.
- Annotations whose `annotator` ends with `:jsonencoded` are parsed as
properties; others are stored as top-level annotations.
- `sourceInfo` is converted back to ExternalReferences with type
“distribution” and comment “proxy URL”.

**Limitation**: An SPDX package that has multiple purls in `externalRefs`
becomes multiple CycloneDX components when converted to CycloneDX, because
CycloneDX does not support multiple purls on one component.

## Merging SBOMs

When you merge SBOMs, we merge in CycloneDX form. If you pass
an SPDX SBOM to `merge-sboms`, it is converted to CycloneDX first, then
merged. The merged result can be output as either CycloneDX or SPDX via
`--sbom-output-type`.

[v1.6]: https://cyclonedx.org/docs/1.6/json
[v2.3]: https://spdx.github.io/spdx-spec/v2.3/
