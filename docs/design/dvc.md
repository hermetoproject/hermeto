# DVC Package Manager Design

## Overview

This design introduces support for DVC (Data Version Control) as a package manager in Hermeto, enabling hermetic builds for ML/AI applications that use DVC to track models, datasets, and other large files as external dependencies.

**Status**: Experimental (`x-dvc`)

## Motivation

Modern ML/AI applications depend on large binary artifacts (models, datasets, etc.) that are impractical to store in git repositories. While most ML/AI projects still use unmanaged, ad hoc dependencies, DVC is one tool that provides version control semantics for tracking these artifacts while storing the actual files externally.

### Problem Statement

ML engineers need to:
- Track ML models and datasets with version control semantics
- Build applications hermetically (without network access)
- Generate accurate SBOMs for AI/ML dependencies
- Support multiple artifact sources (HuggingFace, S3, HTTP, etc.)

### Why DVC?

Unlike the original HuggingFace implementation (PR #1141) which used a custom lockfile format, DVC provides:
- **Avoid custom lockfile**: Leverage an existing tool rather than creating bespoke formats
- **Standard tooling**: DVC provides established commands and workflows
- **Multi-source support**: Works with HuggingFace, S3, GCS, HTTP, etc.
- **Git integration**: Familiar version control semantics
- **Independent development**: Tool with ongoing development outside Hermeto

## Design Principles

1. **Leverage Existing Tools**: Use `dvc fetch` instead of reimplementing fetching logic
2. **Minimal Custom Code**: Let DVC handle the heavy lifting (checksums, retries, etc.)
3. **Clear Separation**: Hermeto fetches (with network), build pulls (without network)
4. **Source Agnostic**: Support all DVC-compatible sources, not just HuggingFace
5. **Experimental Status**: Mark as `x-dvc` to signal it's not production-ready

## Architecture

### Component Overview

```
┌─────────────┐
│  dvc.lock   │  (User's repository)
└──────┬──────┘
       │ 1. Parse
       ▼
┌─────────────────────┐
│  Hermeto x-dvc      │
│  - Parse lockfile   │
│  - Validate schema  │
│  - Check checksums  │
│  - Generate SBOM    │
└──────┬──────────────┘
       │ 2. Run dvc fetch
       ▼
┌─────────────────────┐
│  DVC CLI            │
│  Downloads to cache │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│  Hermeto output     │
│  - DVC cache        │
│  - SBOM with PURLs  │
│  - ENV vars         │
└─────────────────────┘
```

### Workflow

**Phase 1: Dependency Fetching (with network)**

```
hermeto fetch-deps x-dvc
  ↓
1. Parse dvc.lock (validate schema 2.0+)
2. Extract external deps (skip local files)
3. Validate checksums (strict/permissive mode)
4. Run `dvc fetch` with DVC_CACHE_DIR
5. Generate SBOM from parsed lockfile
6. Output DVC_CACHE_DIR environment variable
```

**Phase 2: Hermetic Build (no network)**

```
User build process
  ↓
1. Set DVC_CACHE_DIR from hermeto output
2. Run `dvc pull` (uses cache, no network)
3. Files available for build
```

## Implementation

### Directory Structure

```
hermeto/core/package_managers/dvc/
├── __init__.py          # Export fetch_dvc_source()
├── main.py              # Main entry point, runs dvc fetch
├── models.py            # Pydantic models for dvc.lock
└── parser.py            # Lockfile parsing, SBOM generation
```

### Data Models

```python
class DVCDep(BaseModel):
    """A DVC dependency with URL and checksum."""
    path: str
    md5: Optional[str]
    size: Optional[int]
    hash: Optional[str]

    @property
    def is_external_url(self) -> bool:
        """Check if this is an external dependency."""
        return self.path.startswith(("http://", "https://", "s3://", ...))

class DVCStage(BaseModel):
    """A DVC pipeline stage."""
    cmd: Optional[str]
    deps: Optional[list[DVCDep]]
    outs: Optional[list[DVCOut]]

class DVCLockfile(BaseModel):
    """Root dvc.lock structure (schema 2.0+)."""
    schema_: str = Field(alias="schema")
    stages: dict[str, DVCStage]

    def get_all_external_deps(self) -> list[tuple[str, DVCDep]]:
        """Extract all external URL dependencies."""
        ...
```

### Main Logic

```python
def fetch_dvc_source(request: Request) -> RequestOutput:
    # 1. Load and validate dvc.lock
    lockfile = load_dvc_lockfile(dvc_lock_path)

    # 2. Validate checksums
    validate_checksums_present(lockfile, strict=request.mode == Mode.STRICT)

    # 3. Setup cache directory
    cache_dir = output_dir / "deps/dvc/cache"

    # 4. Run dvc fetch
    subprocess.run(
        ["dvc", "fetch"],
        env={"DVC_CACHE_DIR": str(cache_dir)},
        cwd=source_dir
    )

    # 5. Generate SBOM
    components = generate_sbom_components(lockfile)

    # 6. Return with environment variable
    return RequestOutput.from_obj_list(
        components=components,
        environment_variables=[
            EnvironmentVariable(
                name="DVC_CACHE_DIR",
                value="${output_dir}/deps/dvc/cache"
            )
        ]
    )
```

### SBOM Generation

SBOM components are generated by parsing URLs in `dvc.lock`:

**HuggingFace URLs**:
```
https://huggingface.co/{repo}/resolve/{revision}/{file}
  ↓
pkg:huggingface/{namespace}/{name}@{revision}
```

**All Other URLs**:
```
https://example.com/file.tar.gz
s3://bucket/data.csv
  ↓
pkg:generic/{filename}?checksum={algo}:{hash}&download_url={url}
```

**Local Files**: Skipped (already in repository)

### Checksum Validation

```python
def validate_checksums_present(lockfile: DVCLockfile, strict: bool):
    external_deps = lockfile.get_all_external_deps()

    for stage_name, dep in external_deps:
        if not dep.checksum_value:
            if strict:
                raise PackageRejected("Missing checksum for {dep.path}")
            else:
                log.warning("Missing checksum (permissive mode)")
```

## Input Model

```python
class DVCPackageInput(_PackageInputBase):
    """Accepted input for experimental DVC package."""
    type: Literal["x-dvc"]
    # path inherited from base (defaults to ".")
```

**Usage**:
```bash
# CLI (simple)
hermeto fetch-deps x-dvc

# CLI (with path)
hermeto fetch-deps '{"type": "x-dvc", "path": "ml-project"}'

# Permissive mode
hermeto --mode=permissive fetch-deps x-dvc
```

## Output Structure

```
output/
├── deps/
│   └── dvc/
│       └── cache/              # DVC content-addressed cache
│           ├── files/
│           │   └── md5/
│           │       └── ab/
│           │           └── abc123...
│           └── ...
└── bom.json                     # SBOM with components
```

**Environment Variables** (in SBOM):
```json
{
  "environmentVariables": [
    {
      "name": "DVC_CACHE_DIR",
      "value": "${output_dir}/deps/dvc/cache"
    }
  ]
}
```

## Comparison: Original HuggingFace vs x-dvc

| Aspect | x-huggingface (PR #1141) | x-dvc |
|--------|--------------------------|-------|
| **Lockfile** | Custom `huggingface.lock.yaml` | Standard `dvc.lock` |
| **Scope** | HuggingFace only | Multi-source (HF, S3, HTTP, etc.) |
| **File Selection** | Glob patterns | Explicit file list from DVC |
| **Fetching** | Custom HTTP downloads | `dvc fetch` command |
| **Output Structure** | HF cache format | DVC cache format |
| **PURL Types** | `pkg:huggingface` only | `pkg:huggingface` + `pkg:generic` |
| **Tool Integration** | None | DVC ecosystem |
| **User Workflow** | Manual lockfile creation | Standard DVC commands |

## Advantages of DVC Approach

1. **Less Code**: ~50% less code than original HuggingFace implementation
2. **More Robust**: DVC handles edge cases, retries, error handling
3. **Multi-Source**: Works with any DVC-supported storage
4. **Existing Tool**: Leverage DVC rather than reinventing similar functionality
5. **Potential Integration**: Can integrate with ML workflows that already use DVC

## Limitations

### Current Scope

- ✅ External URL dependencies (http, https, s3, gs, azure)
- ✅ HuggingFace Hub URLs (with PURL detection)
- ✅ Schema 2.0+ lockfiles
- ❌ DVC remote configurations (requires direct URLs)
- ❌ DVC pipeline execution (never runs pipelines)
- ❌ SSH URLs (untested, may work)

### By Design

- **Never executes pipelines**: Hermeto only reads `dvc.lock`, never runs `dvc repro`
- **Requires explicit URLs**: DVC remote shortcuts not supported
- **Git context required**: DVC depends on git (acceptable for Hermeto)

## Security Considerations

### Code Execution

- **Safe**: `dvc fetch` only downloads files, doesn't execute code
- **No pipeline execution**: Hermeto never runs `dvc repro` or `dvc run`
- **No setup.py**: Unlike pip, DVC doesn't execute user code

### Checksum Verification

- **DVC handles verification**: Checksums verified by DVC during fetch
- **Hermeto validates presence**: Ensures checksums exist before fetching
- **Strict mode**: Errors on missing checksums (default)

### Dependency Confusion

- URLs are explicit in `dvc.lock`, no namespace confusion possible
- No package registry to confuse with private sources

## Testing Strategy

### Unit Tests

- Pydantic model validation
- Lockfile parsing (valid/invalid YAML)
- URL classification (HuggingFace vs generic)
- SBOM generation
- Checksum validation (strict/permissive)

### Integration Tests

Integration tests would require:
- Running actual `dvc` commands
- Network access for downloads
- Git repository context

**Decision**: Skip integration tests initially. Unit tests with mocked `dvc fetch` provide sufficient coverage.

## Future Enhancements

### Potential Improvements

1. **DVC Remote Support**: Resolve remote configurations
2. **SSH URL Support**: Test and document SSH-based URLs
3. **Filtering**: Add include/exclude patterns for files
4. **Caching**: Reuse cache across hermeto runs
5. **Parallel Fetching**: Leverage DVC's parallel download capabilities

### Not Planned

- Pipeline execution (violates hermeto principles)
- Dynamic dependency resolution (requires static lockfile)
- Local file tracking (unnecessary for hermetic builds)

## Migration from x-huggingface

For users of the original `x-huggingface` implementation:

### Convert Lockfile

```yaml
# huggingface.lock.yaml (old)
metadata:
  version: "1.0"
models:
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    include_patterns: ["*.safetensors"]
```

```yaml
# dvc.lock (new)
schema: '2.0'
stages:
  fetch_gpt2:
    deps:
    - path: https://huggingface.co/gpt2/resolve/e7da7f221ccf5f2856f4331d34c2d0e82aa2a986/model.safetensors
      md5: <actual_checksum>
```

### Convert Workflow

**Old** workflow:
```bash
# Create custom lockfile manually
hermeto fetch-deps x-huggingface
```

**New** workflow:
```bash
# Use standard DVC commands
dvc import-url https://huggingface.co/gpt2/resolve/.../model.safetensors models/gpt2/model.safetensors
hermeto fetch-deps x-dvc
```

## Dependencies

### New Dependencies

- `dvc`: Data Version Control tool (added to pyproject.toml)

### DVC Installation

DVC will be installed as a regular Python dependency. Users running hermeto will automatically have DVC available.

## Rollout Plan

1. **Initial Release**: Mark as experimental (`x-dvc`)
2. **Feedback Period**: Gather user feedback for 2-3 releases
3. **Stabilization**: Address issues, improve documentation
4. **Promotion**: Remove `x-` prefix when mature

## Documentation

### User Documentation

- Complete usage guide (docs/dvc.md)
- Example workflows
- Troubleshooting section
- Migration guide from x-huggingface

### Design Documentation

- This document (docs/design/dvc.md)
- Architecture decisions
- Comparison with alternatives

## Success Criteria

The `x-dvc` package manager is successful if:

1. ✅ Users can build ML apps hermetically
2. ✅ SBOM accurately represents dependencies
3. ✅ Less code than custom implementation
4. ✅ Works with multiple sources (not just HuggingFace)
5. ✅ Integrates with existing DVC workflows

## Ecosystem Monitoring and Long-Term Strategy

This implementation is experimental (`x-dvc`) in part because DVC's adoption in the ML/AI ecosystem remains uncertain. While most ML/AI projects currently use unmanaged, ad hoc dependencies, the ecosystem may evolve in several directions:

### Monitoring Strategy

We should actively monitor:

1. **DVC Adoption Trends**: Track whether DVC gains meaningful adoption in production ML/AI workflows
2. **Competing Tools**: Watch for alternative tools that provide similar functionality with better adoption
3. **Ecosystem Patterns**: Observe whether the ML/AI community converges on standard dependency management practices

### Decision Points

**If DVC gains wider adoption:**
- Promote `x-dvc` to stable status (remove `x-` prefix)
- Invest in additional features (remote support, filtering, etc.)
- Consider it a successful bet on ecosystem tooling

**If DVC does not achieve wide adoption:**
- **Retire `x-dvc`** rather than promoting it to stable
- Avoid sunk cost fallacy - don't maintain support for a tool the ecosystem has abandoned
- Implement new experimental plugins (`x-<successor>`) for emerging tools
- Learn from the DVC implementation to inform successor plugins

### Why This Approach?

The experimental (`x-`) prefix signals to users that:
- This plugin is a bet on a specific tool's future adoption
- We may retire it if the ecosystem moves in a different direction
- They should not depend on long-term stability of this specific integration

By keeping the implementation minimal and delegating to DVC itself, we minimize maintenance burden and make it easier to pivot to alternatives if needed.

## Related Work

- [DVC Documentation](https://dvc.org/doc)
- [Hermeto PR #1141](https://github.com/hermetoproject/hermeto/pull/1141) - Original HuggingFace implementation
- [SLSA Hermetic Builds](https://slsa.dev/spec/v0.1/requirements#hermetic)
- [HuggingFace PURL Spec](https://github.com/package-url/purl-spec/pull/204)
