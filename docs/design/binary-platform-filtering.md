# Platform Filtering for Binary Artifacts

## Overview

This design introduces platform-aware dependency fetching to Hermeto. The goal is to allow users to filter pre-fetched binary dependencies (like RPMs, Python wheels or platform-specific Gems) based on platform selectors (e.g., architecture, OS). The result is a lean, accurate set of dependencies tailored for each target in a multi-architecture build.

## Design Principles

- **Source-First Posture**: Hermeto remains source-first. Binary prefetching is an opt-in feature and must be explicitly enabled by the user, unless the package manager backend (e.g. RPM) is inherently binary-based.
- **Filtering with Overridable Defaults** When binary prefetching is enabled, Hermeto uses a filter-based system to reduce the set of prefetched binary artifacts. Each filter begins with an expansive default (:all:), which backends may override with more selective defaults. Users can, in turn, override any default by providing their own explicit list of values.
- **No Runtime Platform Detection**: Platform filtering relies on a combination of explicit, user-provided configuration options and backend-specific defaults that are independent of the Hermeto execution environment. Hermeto will not automatically detect the host platform to infer filtering parameters.
- **Backend-Specific Filters**: The input parameters for filtering may differ significantly between package managers. The design accommodates this by scoping platform selectors to each package manager backend.

## Proposed Implementation

The implementation is centered around a new backend-specific input model for specifying binary filtering options. The output structure and SBOM format remain unchanged.

### Input Models

- Platform fields are encapsulated within a `binary` submodel for relevant package types. This approach provides a clear namespace for binary-related options and allows for future extensions without cluttering the top-level package input.
- Platform fields are handled at the PackageInput level rather than globally because the fields needed to filter/select prefetched binaries may differ between package managers
- The `allow_binary` option will be deprecated and the functionality moved to the `binary` submodel, but both the option and the current behavior will be retained until the next major version for backwards compatibility
- Platform fields will accept a string input. The keyword `:all:` will match anything for a given filter. Multiple filter values can be provided as a comma-separated list.
- Platform fields can use Literals for input validation for small, stable sets like Python implementations
- Platform fields may have sensible defaults for the most common use cases. The existing `allow_binary` behavior must be maintained until that field is removed.
- Introduce an optional packages field within the binary model. This field accepts a list of package names, acting as an allow-list for binary prefetching. If this list is provided, only packages named in it will be considered for binary downloads; all others will be treated as source-only. This option applies only to backends like pip and bundler where binary fetching is optional.

```python
class BinaryModeOptions(pydantic.BaseModel, extra="forbid"):
    """Base configuration for binary package handling."""

    packages: BinaryFilterField = BinaryFilterField()

class PipBinaryFilters(BinaryModeOptions):
    """Binary filters specific to pip packages."""

    arch: BinaryFilterField = BinaryFilterField(filters={"x86_64"})
    os: BinaryFilterField = BinaryFilterField(filters={"linux"})
    py_version: BinaryFilterField = BinaryFilterField()
    py_impl: BinaryFilterField = BinaryFilterField(filters={"cp"})

class BundlerBinaryFilters(BinaryModeOptions):
    """Binary filters specific to bundler packages."""

    platform: BinaryFilterField = BinaryFilterField()

class RpmBinaryFilters(pydantic.BaseModel):
    arch: BinaryFilterField = BinaryFilterField()

class PipPackageInput(_PackageInputBase):
    type: Literal["pip"]
    allow_binary: bool = False
    binary: Optional[PipBinaryFilters] = None

class BundlerPackageInput(_PackageInputBase):
    """Accepted input for a bundler package."""

    type: Literal["bundler"]
    allow_binary: bool = False
    binary: Optional[BundlerBinaryFilters] = None

class RpmPackageInput(_PackageInputBase):
    type: Literal["rpm"]
    binary: Optional[RpmBinaryFilters] = None
```

### Binary Fetching Strategies
The new binary model allows users to select from three distinct fetching strategies. These strategies are analogous to pip's binary-related flags and apply only to package managers where binary fetching is optional (e.g., pip, bundler).

**No Binaries (Analogous to pip install `--no-binary`)**
Configuration: binary field unspecified (or the legacy `allow_binary: false`).

Behavior: Hermeto operates in a source-only mode. No binary artifacts will be prefetched for any packages. This is the default behavior if no binary options are specified.

**Prefer Binaries (Analogous to pip install `--prefer-binary`)**
Configuration: binary field specified with no packages list defined.

Behavior: Hermeto will attempt to prefetch compatible binaries for all dependencies where possible. In this mode, it will also prefetch a corresponding source distribution **if there is no binary available** to serve as a fallback.

**Only Binaries for Specific Packages (Analogous to pip install `--only-binary`)**
Configuration: binary field specified and a packages list is provided.

Behavior: This enables a strict, targeted binary mode.

For packages in the packages list: Hermeto will attempt to prefetch only binary artifacts. If a matching binary cannot be found for one of these packages, the operation will fail. Source distributions for these packages will not be downloaded.

For packages NOT in the list: Hermeto reverts to the "No Binaries" behavior, fetching only their source distributions.

### CLI Usage

Complex input is provided by an input JSON string. This is the only way to specify binary options.

```bash
# Simple invocation (no binaries)
hermeto fetch-deps pip

# With binaries enabled
hermeto fetch-deps '{"type": "pip", "binary": {}}'

# With platform filtering
hermeto fetch-deps '{
  "type": "pip",
  "binary": {
    "os": "linux",
    "arch": "x86_64",
    "py_version": ":all:",
    "py_impl": "py",
    "packages": "cryptography"
  }
}'
```

### Platform Filters
- When multiple values are provided for a single filter field, they are combined with **OR** logic
- When multiple filter fields are provided, they are combined with **AND** logic

**Example**
```
{
    "type": "pip",
    "binary": {
      "os": "linux,macosx",
      "arch": "x86_64,arm64",
      "py_version": "310,311,312",
      "py_impl": "cp"
    }
  }
```
  Matches wheels for:
  - ✅ torch-2.7.1-cp310-cp310-manylinux_2_28_x86_64.whl (linux + x86_64 + py310 + cp)
  - ✅ torch-2.7.1-cp311-none-macosx_11_0_arm64.whl (macosx + arm64 + py311 + cp)
  - ✅ torch-2.7.1-cp312-cp312-manylinux_2_28_x86_64.whl (linux + x86_64 + py312 + cp)
  - ❌ torch-2.7.1-cp39-none-macosx_11_0_arm64.whl (Python 3.9 not in version list)
  - ❌ torch-2.7.1-cp313-cp313-win_amd64.whl (Windows not in OS list)


### pip

The pip backend will be updated to filter Python wheels based on user-provided platform selectors. Unless operating in strict binary mode, it will fall back to fetching source distributions when no matching binary is found.

| Current Behavior (**When `allow_binary=true`**) | Proposed Changes (**When platform filtering**) |
|---|---|
| Packages with prefetched wheels are specifically noted in the SBOM | - |
| Fail when a package has no matching distributions | - |
| Prefetch all available wheels | Prefetch *wheels that match platform filters* |
| Always prefetch sdists when available | Do not prefetch sdists when user requests binaries for specific packages |
| - | Perform filtering *before* downloading from the registry |

**Implementation Details**:
- Hermeto will use the Python packaging library to [parse wheel filenames][] and do platform matching between the user-provided filters and [wheel tags][].
- Filtering logic will match against the standard `{python tag}-{abi tag}-{platform tag}` tag format (e.g., numpy-1.24.3-cp311-cp311-linux_x86_64.whl). See [packaging platform tags][]
  - The user-specified python implementation is a match if:
    - The wheel python implementation from the python tag is either `py` or equal to the user-specified python implementation
  - The user-specified python version is a match if:
    - Equal to the wheel python version from the python tag
    - Greater than the wheel python version from the python tag and the wheel abi from the abi tag is either `abi3` or `none`
  - The user-specified os is a match if:
    - The wheel platform tag is `any`
    - Contained by the wheel platform tag
  - The user-specified arch is a match if:
    - The wheel platform tag is `any`
    - Contained by the wheel platform tag
- User-provided filters should have sensible defaults:
  - For python implementation, the implementation-agnostic `py` and reference implementation `cp` (CPython)
  - For OS, linux
  - For architecture, x86_64

**Platform Matching Examples**:
- Example 1: Prefetching for Python 3.11 on Linux
  ```json
  {
    "os": "linux",
    "arch": "x86_64,aarch64",
    "py_version": "311",
    "py_impl": "cp"
  }
  ```
  Matches torch 2.7.1 wheels:
  - ✅ `torch-2.7.1-cp311-cp311-manylinux_2_28_x86_64.whl` (linux + x86_64 + py311 + cp)
  - ✅ `torch-2.7.1-cp311-cp311-manylinux_2_28_aarch64.whl` (linux + aarch64 + py311 + cp)
  - ❌ `torch-2.7.1-cp310-cp310-manylinux_2_28_x86_64.whl` (Python 3.10 not in version list)
  - ❌ `torch-2.7.1-cp311-none-macosx_11_0_arm64.whl` (macOS not in OS list)

- Example 2: Prefetching for multiple Python versions on macOS ARM64
  ```json
  {
    "os": "macosx",
    "arch": "arm64",
    "py_version": "311,312",
    "py_impl": "cp"
  }
  ```
  Matches torch 2.7.1 wheels:
  - ✅ `torch-2.7.1-cp311-none-macosx_11_0_arm64.whl` (macosx + arm64 + py311 + cp)
  - ✅ `torch-2.7.1-cp312-none-macosx_11_0_arm64.whl` (macosx + arm64 + py312 + cp)
  - ✅ `torch-2.7.1-cp310-none-macosx_11_0_arm64.whl` (macosx + arm64 + cp310 with abi=none works for py311/312)
  - ❌ `torch-2.7.1-cp313-cp313t-macosx_14_0_arm64.whl` (Python 3.13 not in version list)
  - ❌ `torch-2.7.1-cp312-cp312-manylinux_2_28_aarch64.whl` (Linux not in OS list)

**Python Compatibility**:
- Version format: `"311"` for Python 3.11, `"39,310"` for Python 3.9 or 3.10
- Implementation: `"cp"` (CPython), `"pp"` (PyPy), `"py"` (generic)

This filtering is unlikely to produce a single "best" wheel, but is likely to reduce the number of downloads significantly. See the [wheelios][] repository for the original investigation.

### bundler

The bundler backend will be updated to filter platform-specific Gems based on user-provided platform selectors.

| Current Behavior (**When `allow_binary=true`**) | Proposed Changes (**When platform filtering**)|
|---|---|
| Platform information for Gems is parsed from `Gemfile.lock` | - |
| Always prefetch platform-agnostic (`platform="ruby"`) gems | Do not prefetch platform-agnostic gems when user requests binaries for specific packages |
| Platform-specific Gems are specifically noted in the SBOM | - |
| Prefetch all platform-specific Gems in `Gemfile.lock` | Prefetch all platform-specific Gems *that match platform filters* |
| - | Perform filtering *before* downloading from the registry |

**Implementation Details**:
- Hermeto will perform matching between the user-specified platforms and platform-specific Gems
- The lockfile parser appears to return normalized values for platform, so to be safe, Hermeto will also likely need to use the [Gem:Platform][] class to perform normalization of the user specified platforms before any comparisons/matches can be made.

**Platform Matching Examples**:
- Example 1: Prefetching for x86_64 Linux (any libc variant)
  ```json
  {
    "platform": "x86_64-linux-gnu,x86_64-linux-musl"
  }
  ```
  Matches nokogiri 1.18.8 gems:
  - ✅ `nokogiri (1.18.8-x86_64-linux-gnu)`
  - ✅ `nokogiri (1.18.8-x86_64-linux-musl)`
  - ✅ `nokogiri (1.18.8)` platform="ruby" (pure Ruby fallback, always included)
  - ❌ `nokogiri (1.18.8-aarch64-linux-gnu)`
  - ❌ `nokogiri (1.18.8-java)`

- Example 2: Prefetching for JRuby
  ```json
  {
    "platform": "java"
  }
  ```
  Matches nokogiri 1.18.8 gems:
  - ✅ `nokogiri (1.18.8-java)` (exact match - special case: single-component platform)
  - ✅ `nokogiri (1.18.8)` platform="ruby" (pure Ruby fallback, always included)
  - ❌ `nokogiri (1.18.8-x86_64-linux-gnu)`
  - ❌ `nokogiri (1.18.8-arm64-darwin)`

### rpm

The rpm backend will filter packages based on a user-provided architecture list, failing if a requested architecture is not available.

| Current Behavior | Proposed Changes (**When platform filtering**) |
|---|---|
| `allow_binary=true` is assumed without being specified | - |
| RPMs are grouped by arch in `rpms.lock.yaml`. Noarch is present in all groups | - |
| RPM Architecture noted in SBOM PURLs | - |
| Output structure is per-architecture | - |
| Prefetch RPMs for all arches in `rpms.lock.yaml` | Prefetch RPMs for *user-requested* arches |
| - | Perform filtering *before* downloading from the registry |
| - | **Fail** if a requested architecture is not present in the lockfile |

### JavaScript & Source-based Package Managers
- **npm/yarn**: While JS packages can publish prebuilt binaries using the [prebuildify][] tool, Hermeto does not yet handle/support them. Adding this support is a prerequisite for platform filtering and requires a separate investigation, placing it outside the scope of this design.
- **gomod/cargo**: These backends currently only manage source-based packages, so binary filtering does not apply. Adding support for prefetching other binary artifacts, like Go toolchains, is outside the scope of this design.


[parse wheel filenames]: https://packaging.pypa.io/en/stable/utils.html#packaging.utils.parse_wheel_filename
[wheel tags]: https://packaging.pypa.io/en/stable/tags.html
[prebuildify]: https://github.com/prebuild/prebuildify
[wheelios]: https://github.com/chmeliik/wheelios
[packaging platform tags]: https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/
[Gem:Platform]: https://github.com/rubygems/rubygems/blob/8ad4509f95c90cf9523a82ca917b6b842fd37132/lib/rubygems/platform.rb#L10