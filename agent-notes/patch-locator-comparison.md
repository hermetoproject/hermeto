# Patch Locator Implementation Comparison

## Executive Summary for Engineers

NOTE:  the Analysis section, and below, refers to the *original* Python
implementation; this document was merged with the fix.

### Problem

The Python implementation incorrectly required a workspace locator for **all**
non-builtin patches, while TypeScript only requires it for relative workspace
paths.

### Root Cause

Missing `isParentRequired()` logic (TypeScript `patchUtils.ts:153-158`).

Python treated absolute paths and project-relative (`~/`) paths as
workspace-relative, causing validation failures when patches used these path
types without a parent workspace locator.

As well, Yarn v3 options, separated by `~`, conflicted with project-relative
(`~/`) paths in the original Python.

Added logic to handle `~` and `~/` differently, as well as correctly handling
both Yarn v3 `~` option delimiters *and* Yarn v4 `!` option delimiters.

### Solution Implemented

Added path type detection to match TypeScript behavior:

- **Builtin** (`builtin<...>`): (Unsupported) No parent required
- **Project-relative** (`~/...`): No parent required, resolved against project root
- **Absolute** paths: No parent required
- **Relative** paths: Parent workspace locator required

### Test Changes

Converted `test_get_pedigree` in
[test_resolver.py](tests/unit/package_managers/yarn/test_resolver.py#L1056) to
parametrized test with 3 cases:

1. Nested patches with workspace (original behavior)
2. Project-relative patches without workspace locator
3. Absolute patches without workspace locator

---

## Analysis

### Summary of Key Implementation Gaps
<!-- markdownlint-disable line-length -->
| Feature                    | TypeScript    | Python        | Impact                                   |
|----------------------------|---------------|---------------|------------------------------------------|
| Absolute patch paths       | ✓ Supported   | ✗ Unsupported | Python fails on absolute paths           |
| Project-relative `~/`      | ✓ Supported   | ✗ Unsupported | Python fails on `~/` paths               |
| Multiple flags             | ✓ Supported   | ✗ Unsupported | Python loses flag information            |
| Optional flag preservation | ✓ Preserved   | ✗ Discarded   | Python cannot use optional flag          |
| Builtin patch plugins      | ✓ Hook system | ✗ Hardcoded   | Python only supports plugin-compat       |
| Line ending normalization  | ✓ Implemented | ✗ Missing     | Python may have checksum mismatches      |
| LocalPath optimization     | ✓ Implemented | ✗ Missing     | Python less efficient for local packages |

### Path Normalization Approaches

#### TypeScript: `patchUtils.ts:119-146` (visitPatchPath function)

- Uses visitor pattern with 4 distinct callbacks for different path types
- Path type detection:
  - **Builtin**: Matches `BUILTIN_REGEXP` (`/^builtin<([^>]+)>$/`)
  - **Project-relative**: Starts with `~/`
  - **Absolute**: Uses `ppath.isAbsolute()`
  - **Relative**: Default fallback
- Strips flag suffixes (e.g., `optional!path`) before type detection

#### Python: `locators.py:210-221` (process_patch_path function)

- Simple binary classification: builtin string vs Path object
- Path type detection:
  - **Builtin**: Matches regex `^builtin<([^>]+)>$`
  - **All others**: Treated as `Path` objects
- Strips optional prefixes (`~`, `optional!`) before classification
- **Missing**: No handling for `~/` project-relative paths
- **Missing**: No handling for absolute paths

#### Discrepancy

**Python does not distinguish between absolute, relative, and project-relative
paths.** All non-builtin patches are treated as relative paths that get
resolved against the workspace locator.

### Handling of Project-Root vs Workspace-Bound Patches

#### TypeScript: `patchUtils.ts:153-158` (isParentRequired function)

Uses `visitPatchPath` to determine if a parent locator is needed:

- **Absolute paths**: `false` (no parent needed)
- **Relative paths**: `true` (parent required)
- **Project paths** (`~/`): `false` (no parent needed)
- **Builtin patches**: `false` (no parent needed)

#### TypeScript: `PatchResolver.ts:27-34` (bindDescriptor method)

Only binds descriptor to parent if `isParentRequired()` returns `true` for ANY
patch path.

#### Python: `locators.py:224-232`

- Checks if `locator` param exists in parsed reference
- If present, validates it's a `WorkspaceLocator`, raises
  `UnsupportedFeature` otherwise
- **No validation** of whether the locator is actually needed

#### Python: `resolver.py:460-464`

Unconditionally requires `patch_locator.locator` to be non-None for path
patches, raising `UnsupportedFeature` if missing.

#### Discrepancy - project-root

**Python does not implement the `isParentRequired()` logic.** It assumes ALL
non-builtin patches require a workspace locator. This means:

1. Absolute patch paths would fail in Python but work in TypeScript
2. Project-relative (`~/`) patch paths would fail in Python but work in
   TypeScript

### Builtin Patch URL Construction

#### TypeScript: `patchUtils.ts:167-180` (loadPatchFiles function)

Uses hook system to retrieve builtin patches:

```typescript
onBuiltin: async name => {
  return await opts.project.configuration.firstHook((hooks: PatchHooks) => {
    return hooks.getBuiltinPatch;
  }, opts.project, name);
},
```

#### Python: `resolver.py:474-484` (_get_builtin_patch_url method)

- Hardcodes URL to `@yarnpkg/berry` repository
- Only supports `builtin<compat/...>` patches (from plugin-compat)
- Constructs URL: `git+{YARN_REPO_URL}@{tag}#{subpath}`
- Tag format: `@yarnpkg/cli/{yarn_version}`
- Subpath: `packages/plugin-compat/sources/patches/{name}.patch.ts`

#### Discrepancy - builtin

**Python hardcodes builtin patch URLs to the Yarn repository, while TypeScript
uses a flexible hook system.** This means:

1. Python cannot handle custom builtin patches from other plugins
2. Python assumes all builtins are from `plugin-compat`
3. Python URL format assumes `.patch.ts` extension

### Handling Backwards Compatibility with Yarn v3

#### TypeScript: `patchUtils.ts:119-122` (extractPatchFlags function)

```typescript
const flagIndex = patchPath.lastIndexOf(`!`);
const flags = flagIndex !== -1
  ? new Set(patchPath.slice(0, flagIndex).split(/!/))
  : new Set();
const optional = flags.has(`optional`);
```

Supports Yarn v4 flag format: `optional!path` (and potentially other flags)

#### TypeScript: `visitPatchPath` references

References old code that handled `~` prefix (line 92 in old commit), but
current implementation uses `!` delimiter.

#### Python: `locators.py:210-217` (process_patch_path function)

```python
patch = patch.removeprefix("~")  # Yarn v3
patch = patch.removeprefix("optional!")  # Yarn v4
```

Strips both prefixes sequentially but doesn't preserve the optional flag
information.

#### Discrepancy - Yarn v3 compat

**Python strips both v3 (`~`) and v4 (`optional!`) prefixes but discards the
optional flag.** TypeScript extracts and preserves the flag. This means:

1. Python cannot distinguish between optional and required patches after
   parsing
2. Python supports both v3 and v4 syntax but loses semantic information
3. TypeScript supports multiple flags via `!` delimiter, Python only handles
   one prefix

### Edge Cases in TypeScript Not Handled in Python

#### 1. Multiple Flag Support (`patchUtils.ts:119-122`)

TypeScript supports multiple flags: `flag1!flag2!path` Python only handles
single prefix removal

#### 2. Path Resolution for Different Types (`patchUtils.ts:161-180`)

TypeScript handles 4 distinct resolution strategies:

- **Absolute**: Direct file system access
- **Relative**: Via parent package fs (with fallback to localPath)
- **Project**: Via `opts.project.cwd`
- **Builtin**: Via hook system

Python only handles:

- **Builtin**: Via hardcoded URL
- **Everything else**: Via workspace locator join

#### 3. LocalPath Optimization (`patchUtils.ts:167-172`)

```typescript
const effectiveParentFetch = parentFetch && parentFetch.localPath
  ? {packageFs: new CwdFS(PortablePath.root), prefixPath: ppath.relative(PortablePath.root, parentFetch.localPath)}
  : parentFetch;
```

TypeScript optimizes for local packages by using direct file system access.
Python does not have this optimization.

#### 4. Line Ending Normalization (`patchUtils.ts:192-194`)

```typescript
for (const spec of patchFiles)
  if (typeof spec.source === `string`)
    spec.source = spec.source.replace(/\r\n?/g, `\n`);
```

TypeScript normalizes line endings to prevent Windows/Unix mismatch. Python
does not perform this normalization.

### Differences in 'locator' Parameter Interpretation

#### TypeScript: `PatchResolver.ts:27-34`

The `locator` parameter is **conditionally required**:

- Only bound if `isParentRequired()` returns `true`
- Binding happens in `bindDescriptor()` method
- Descriptor can exist without locator param if all patches are
  absolute/project/builtin

#### Python: `locators.py:224-232`, `resolver.py:460-464`

The `locator` parameter interpretation:

- **Parsing**: Accepts missing locator, stores as `None`
- **URL generation**: Unconditionally requires non-None locator for path
  patches
- No check whether locator is semantically needed

#### Discrepancy - 'locator' interpretation

**Python accepts missing locator during parsing but fails later during URL
generation for non-builtin patches.** TypeScript validates this earlier via
`isParentRequired()` and only binds when needed.
