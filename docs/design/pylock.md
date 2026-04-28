# PEP 751 pylock.toml Support

## Background

Hermeto currently supports `requirements.txt` for Python dependencies. However,
users increasingly generate lockfiles with tools like poetry, pipenv, and uv,
which produce incompatible formats (`poetry.lock`, `Pipfile.lock`, `uv.lock`).

This forces Hermeto users to:

- Generate `requirements.txt` from their tool's lockfile as an intermediate
  step
- Lose metadata that requirements.txt cannot represent (e.g. per-artifact
  hashes)
- Manually maintain two files (lockfile + requirements.txt)

Additionally, using `requirements.txt` as a pseudo-lockfile has limitations:

- No format versioning (Hermeto cannot detect schema changes)
- Hashes are line-based, not per-artifact (cannot distinguish wheel vs sdist
  hashes)

### Why Add pylock.toml Support

Adding pylock.toml as a supported format alongside requirements.txt is
valuable for several reasons:

- New projects may prefer an ecosystem-agnostic lockfile that will
  ultimately be supported by all packaging tools, rather than
  vendor-locking themselves to a particular tool — including Hermeto
  itself.
- Existing projects still using requirements files might switch directly
  to pylock.toml as the natural choice when transitioning to a more
  modern way of declaring their dependencies.
- The format follows a well-defined, versioned schema (unlike
  tool-specific lockfiles), so the parser is unlikely to break due to
  unannounced upstream changes.

### Ecosystem Readiness

[PEP 751](https://peps.python.org/pep-0751/) was accepted on 2025-03-31 with
status Final. Current tool support:

- **pip**: can generate and install from pylock.toml
- **uv**: can generate and install pylock.toml
- **PDM**: can generate, export, and install from pylock.toml
- **Poetry**: support planned but not yet implemented

There isn't a wide-spread adoption of the agnostic lockfile at the time of
writing and most projects do not ship a pylock.toml yet, and
users will likely generate one from their existing toolchain. Given that
Hermeto would strictly adhere to the accepted spec and the format is
versioned, we can implement this as a supported (non-experimental) feature.

### PEP 751 Standard

[PEP 751](https://peps.python.org/pep-0751/) defines `pylock.toml`, a
standardized ecosystem-agnostic lockfile format. Key features Hermeto needs:

1. Format versioning: `lock-version` field enables Hermeto to handle
   schema evolution
2. Per-artifact hashes: Hashes nested in distribution objects (wheel,
   sdist, archive)
3. Multiple package sources: PyPI, VCS, direct URLs, local directories
4. TOML structure: Easy to parse, better than line-based text

See the [PEP 751 specification](https://peps.python.org/pep-0751/) for full
field definitions and TOML schema.

### Lockfile Structure

```toml
lock-version = "1.0"

[[packages]]
name = "foo"
version = "1.0.0"
sdist = { url = "https://...", hashes = { "sha256" = "..." } }
wheels = [
    { url = "https://...", hashes = { "sha256" = "..." } }
]
```

### Package Kinds Hermeto Must Support

#### PyPI Packages

```toml
[[packages]]
name = "foo"
version = "1.0.0"
index = "https://pypi.org/simple/"  # Optional, defaults to PyPI
sdist = { url = "...", hashes = { "sha256" = "..." } }
wheels = [{ url = "...", hashes = { "sha256" = "..." } }]
```

#### VCS Packages

```toml
[[packages]]
name = "foo"

[packages.vcs]
type = "git"
url = "https://github.com/user/repo.git"
commit-id = "abc123"
subdirectory = "subpkg"  # optional, project root within the repo
```

#### URL Packages

```toml
[[packages]]
name = "foo"
archive = { url = "https://example.com/pkg.tar.gz", hashes = { ... } }
```

#### Directory Packages

```toml
[[packages]]
name = "foo"
directory = { path = "./local-pkg" }
```

#### Attestation Metadata

Packages can include `attestation-identities` which is signed provenance
metadata linking published artifacts to their source repository and build
system. Verifying or otherwise acting on these attestations is out of scope.

## Design

### Parser Architecture

Introduce a new pylock parser for TOML parsing while reusing the existing
pip download and processing infrastructure (hash verification, async
downloads, Rust extension detection, SBOM generation). The new parser
produces the same data structures that the download pipeline expects,
so the fetching and verification logic remains shared.

Two other alternatives were considered:

- **Extending the requirements.txt parser**: TOML and line-based formats
  are fundamentally different, nested hash extraction becomes awkward,
  attestations would be bolted on, and it mixes two unrelated parsing
  responsibilities without a shared abstraction to justify it.
- **Fully separate parser with no shared infrastructure**: introduces
  more code to maintain with no clear benefit over reusing the existing
  download pipeline.

### Key Design Decisions

#### 1. Lockfile Selection

With multiple Python toolchains and a standardized format that is still
early in adoption, we cannot safely assume which lockfile the user intends
Hermeto to consume. A project may have both a tool-specific lockfile and
`pylock.toml`, or may be experimenting with the format. Rather than
adding format-specific input fields (`pylock_files`, `uv_lock_files`,
etc.), two new generic fields are introduced: `lockfile` and `lockfile_extras`
for additional files such as separately locked build dependencies.

When the lockfile uses a default name (`pylock.toml`, `uv.lock`,
`poetry.lock`, `pdm.lock`, etc.), Hermeto auto-detects the packaging
tool from the filename. A `packaging_tool` field is available as an
override for cases where the lockfile has a non-default name (e.g. a
renamed or generated file).

The existing `requirements_files` and `requirements_build_files` fields
are preserved for backwards compatibility. The new fields take
precedence when both are specified. The old fields should be deprecated
in a future release once migration is complete.

Example input:

```json
{
  "type": "pip",
  "lockfile": "pylock.toml",
  "lockfile_extras": ["pylock.build.toml"],
  "packaging_tool": "pylock"
}
```

#### 2. Version Tolerance

Hermeto needs to be aware of the `lock-version` field. An unknown major
version should yield a fatal error.

#### 3. Hash Verification

pylock.toml nests hashes per distribution object (sdist, wheels, archive),
so Hermeto always knows which artifact it is fetching and can verify against
exactly that artifact's hashes.

### Markers

Markers are optional in pylock.toml and attach environment conditions
(OS, Python version, etc.) to packages. They could be used to skip
fetching packages not needed for a given target platform. However,
platform-specific artifact selection is already handled by Hermeto's
binary filters, which use wheel filename tags
([PEP 491](https://peps.python.org/pep-0491/)) to select matching
wheels. Markers operate at the package level rather than the artifact
level, but since Hermeto does not know the target platform at fetch
time, all packages are fetched unconditionally. Given the pipeline stage
where Hermeto operates and due to the existing binary filtering logic
environment markers are out of scope.

### Build Dependencies

Hermeto distinguishes between runtime and build dependencies. This mirrors
how requirements.txt handles build dependencies via a separate
`requirements-build.txt` file. Build dependencies are:

- Fetched and verified the same way as runtime dependencies
- Included in the output and SBOM
- Kept separate so Hermeto can distinguish them in the build config

PEP 751 does not solve locking build requirements for sdists (this was
[explicitly deferred](https://peps.python.org/pep-0751/#locking-build-requirements-for-sdists)
from the PEP). Users must generate a separate lockfile for their build
dependencies. PEP 751 supports named lockfiles (`pylock.<name>.toml`),
so a natural convention could be `pylock.build.toml`.

**NOTE:** Users can generate this file using
[pybuild-deps](https://github.com/hermetoproject/pybuild-deps), which
resolves build dependencies from `pyproject.toml`. pybuild-deps needs to be
enhanced to support pylock.toml output, generating a named lockfile
like `pylock.build.toml`. Hermeto consumes it via the `lockfile_extras`
input field.

### `created-by`

pylock.toml records the generating tool in the `created-by` field.
Different installers use different environment variables for build
configuration (e.g. `PIP_FIND_LINKS` for pip). Hermeto can use this
field to select the appropriate installer and its corresponding
environment variables when configuring the build environment.

### Integration

- Input: Add `lockfile`, `lockfile_extras`, and `packaging_tool` to
  `PipPackageInput`
- Downloads: PyPI, VCS, and URL packages reuse the existing download logic.
  Directory packages require new handling or rejection since the current
  implementation does not support them.
- SBOM: Preserve attestation-identities when present

### Security Considerations

1. Path traversal: Validate directory package paths stay within repo
2. Hash verification: All downloads verified against the specific
   artifact's hashes from the lockfile
3. Attestation preservation: Store in SBOM (verification is future work)
4. Trusted sources: VCS/directory are trusted; URL requires hashes

## Follow-up

- **Extras / dependency group filtering**: pylock.toml supports extras
  and dependency groups, allowing lockfiles to cover multiple installation
  profiles. Users should be able to specify which to fetch via the input
  JSON to avoid fetching unnecessary packages.
- **Attestation metadata**: pylock.toml supports `attestation-identities`
  ([PEP 740](https://peps.python.org/pep-0740/)) as package metadata.
  Hermeto could preserve these in SBOM output. Adoption of PEP 740 is
  still early, and existing conversions from other formats to pylock.toml
  may not preserve this metadata.

## References

- [PEP 751](https://peps.python.org/pep-0751/) - Lockfile specification
- [PEP 740](https://peps.python.org/pep-0740/) - Attestation identities
- [uv documentation](https://docs.astral.sh/uv/) - Reference implementation
