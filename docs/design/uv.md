# Design document for uv package manager support

Contents:

1. [Overview](#overview)
2. [Design](#design)
3. [Implementation Notes](#implementation-notes)
4. [Delivery plan (10-12 weeks)](#delivery-plan-10-12-weeks)
5. [References](#references)

## Overview

This document proposes native support for the uv ecosystem in Hermeto.

The goal is to support uv project workflows based on:

- `pyproject.toml` dependency declarations
- `uv.lock` as the lockfile of record
- deterministic fetching and SBOM generation without invoking dependency resolution during prefetch

The implementation must follow Hermeto principles:

- prefer explicit, lockfile-based inputs
- avoid arbitrary code execution
- preserve reproducibility
- report all prefetched artifacts in SBOM output

### Developer workflow

Typical uv project flow:

1. Initialize project (`uv init`) and define dependencies in `pyproject.toml`.
2. Add or update dependencies with `uv add` / `uv remove`.
3. Lock dependencies with `uv lock` (or rely on uv auto-lock).
4. Materialize environment with `uv sync`.

For Hermeto, consume only already locked projects. Hermeto must reject missing or out-of-date lockfile conditions that require live resolution.

### uv ecosystem model relevant to Hermeto

- `uv.lock` is a uv-specific TOML lock format and is intended to be checked into VCS.
- Lockfile captures exact resolved versions and source information.
- uv supports multiple source kinds (registry/index, git, url, path, workspace).
- uv can include extras, dependency groups, and markers.

These characteristics align with Hermeto's lockfile-first model, but path/workspace members and other local-only sources require explicit handling.

## Design

### Scope

In scope:

- New backend type: `x-uv` initially (experimental), later promotable to `uv`.
- Parsing and validating `uv.lock`.
- Fetching remote dependencies referenced by lock entries.
- SBOM component generation for all fetched dependencies.
- Build config output (env vars and inject files) so offline builds can consume prefetched content.
- Unit tests and integration tests for supported source kinds.
- User-facing docs for backend usage and caveats.

Out of scope (phase 1):

- Generating or updating `uv.lock` inside Hermeto.
- Supporting unlocked projects.
- Supporting behavior that requires executing arbitrary project code.
- Full parity for every future uv lockfile feature on day one.

### Input model and backend registration

Add a new input model in `hermeto/core/models/input.py`:

- `UvPackageInput` with `type: Literal["x-uv"]` (or `uv` if maintainers choose direct GA).
- Optional explicit lockfile path if needed later, defaulting to `uv.lock` in package path.

Update request accessors and union definitions:

- Include the new package type in `PackageManagerType`.
- Include `UvPackageInput` in `PackageInput`.
- Add a `Request.uv_packages` property.

Register resolver handler in `hermeto/core/resolver.py`:

- Map `x-uv` to a new fetch implementation.

### Lockfile ingestion and validation

Create backend package, e.g. `hermeto/core/package_managers/uv/`:

- `main.py`: Orchestrate the fetch pipeline.
- `lockfile.py`: Implement parsing and schema validation.
- `models.py`: Define typed lockfile entities and normalized dependency records.
- `sources.py`: Implement source-kind specific normalization and fetch planning.

Validation requirements:

- lockfile must exist (`uv.lock`) in package root
- lockfile format version must be recognized
- all resolved dependencies must be concrete and fetchable without re-resolve
- reject unsupported or unsafe entries with actionable error messages

### Fetch strategy and arbitrary code execution posture

Primary strategy: Hermeto-owned fetching from lockfile metadata.

Reasoning:

- calling high-level uv commands can trigger lock/sync behavior and project install semantics
- this may execute build steps or other code paths indirectly via package build/install workflows
- Hermeto should avoid runtime paths that can execute arbitrary code in source repositories

For phase 1:

- do not run `uv sync` or `uv run`
- do not execute project build backends
- fetch artifacts directly via existing Hermeto download primitives where possible
- only use external tools for metadata extraction if they are read-only and do not execute project code

Supported source kinds (planned):

- index/registry artifacts (wheels/sdists) with hash verification when available
- git dependencies pinned to immutable commit
- direct URL artifacts

Local-only sources:

- path/workspace sources should be represented in SBOM when meaningful
- fetching is not required for local files already present in source tree
- document exact handling and limitations

### Output directory and build configuration

Introduce uv output structure under output dependencies directory, e.g.:

- `deps/uv/` for fetched artifacts
- additional metadata files if uv requires index/source indirection for offline mode

Build config integration:

- extend `generate-env` and `inject-files` logic to support uv backend
- produce env vars/config consumed by build flow to force offline usage of prefetched content

Candidate env approach (subject to feasibility verification):

- configure index URL or file-based source mapping to local prefetched artifacts
- disable network lookup where possible

If uv cannot consume local cache solely through env vars, add deterministic injected config files and document required invocation pattern.

### SBOM integration

For each fetched uv dependency:

- create `Component` with name/version/purl when possible
- attach Hermeto properties for:
  - missing user-provided checksums
  - dependency source kind (registry/git/url/path/workspace)
  - build/dev grouping metadata where available

For git dependencies:

- include immutable revision in purl qualifiers when possible

For experimental backend mode:

- ensure document-level experimental annotation behavior remains consistent with existing policy

### Error handling policy

Follow project error guidelines:

- state what failed
- suggest known remediation (for example, regenerate lockfile with `uv lock`)
- include docs pointer for unsupported source mode or lockfile feature

### Testing strategy

Unit tests:

- lockfile parsing and schema/format validation
- unsupported/invalid lockfile cases
- dependency normalization across source kinds
- checksum validation behavior
- SBOM component mapping and properties
- resolver wiring and input model validation

Integration tests:

- minimal uv project with `uv.lock` and registry deps
- project including git dependency pinned to commit
- project with extras/groups markers
- offline verification path using generated env/files

Test data generation:

- add scripts under `hack/mock-unittest-data` for uv fixtures
- pin uv versions used to generate fixtures

### Documentation deliverables

- Add `docs/uv.md` user guide:
  - required files (`pyproject.toml`, `uv.lock`)
  - supported source types
  - limitations
  - offline usage steps
  - troubleshooting
- Update `README.md` package manager table to include uv backend and status.
- Update mkdocs nav when backend becomes user-visible.

## Implementation Notes

### Current limitations (expected for first implementation)

- Some advanced uv source configurations may be unsupported initially.
- Local path/workspace dependencies may have reduced metadata fidelity in SBOM.
- Exact offline uv invocation may require specific env/config contract.

### Risk areas

- uv lockfile schema evolution across uv releases
- source kind edge cases (markers, alternate indexes, conflicting groups)
- balancing strict security posture with practical compatibility

### Mitigations

- lock parser with explicit schema/version checks
- comprehensive fixture matrix with pinned uv version
- start as experimental backend (`x-uv`) to iterate safely

## Delivery plan (10-12 weeks)

Weeks 1-2: Ecosystem and security research

- Perform a deep-dive into uv lockfile and source semantics.
- Verify arbitrary code execution risk boundaries.
- Finalize the design proposal through maintainer feedback.

Weeks 3-4: Input and parsing foundation

- Add input model and resolver registration for `x-uv`.
- Implement lockfile parser and validation.
- Add parser-focused unit tests.

Weeks 5-7: Fetching and output integration

- Implement fetch pipeline for registry/git/url source kinds.
- Integrate checksums and artifact download layout.
- Implement generate-env/inject-files integration.

Weeks 8-9: SBOM and hardening

- Map uv dependencies to SBOM components and properties.
- Improve diagnostics and unsupported-case handling.
- Expand negative-path tests.

Weeks 10-11: Integration tests and docs

- Add end-to-end test fixtures (including Docker/offline scenarios where applicable).
- Write user documentation page and examples.
- Address maintainer review feedback.

Week 12: Stabilization and stretch

- Perform performance tuning and reliability fixes.
- Evaluate additional uv source features.
- Prepare criteria for promoting from `x-uv` to `uv`.

## References

- uv project layout and lockfile docs: https://docs.astral.sh/uv/concepts/projects/layout/
- uv lock/sync docs: https://docs.astral.sh/uv/concepts/projects/sync/
- uv dependency/source model: https://docs.astral.sh/uv/concepts/projects/dependencies/
- uv pip interface and compatibility notes: https://docs.astral.sh/uv/pip/
- Hermeto package manager design template: docs/design/package-manager-template.md
