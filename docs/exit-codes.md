# Exit codes

Hermeto uses **stable exit codes per error type** so that scripts and CI can
detect failure reasons without parsing stderr. The CLI exits with `0` on success
and with one of the codes below on failure.

| Code | Error type | Description |
| ---- | ---------- | ----------- |
| 1 | `BaseError` | Root of the error hierarchy. Don't raise this directly, usemore specific error types. |
| 2 | `UsageError` | Generic error for "Hermeto was used incorrectly." Prefer more specific errors. |
| 3 | `PathOutsideRoot` | After joining a subpath, the result is outside the root of a rooted path. |
| 4 | `InvalidInput` | An invalid argument or input was given to Hermeto. |
| 5 | `PackageRejected` | The package was rejected and cannot be used or stored. |
| 6 | `NotAGitRepo` | The specified path is not a Git repository. |
| 7 | `UnexpectedFormat` | Data does not match the expected format or structure for this context. |
| 8 | `UnsupportedFeature` | The operation or feature is not supported in the current context. |
| 9 | `ExecutableNotFound` | Required executable was not found in PATH. |
| 10 | `ChecksumVerificationFailed` | The computed checksum did not match the expected value. |
| 11 | `InvalidChecksum` | The checksum provided is invalid or unreadable. |
| 12 | `MissingChecksum` | Required checksum information is missing. |
| 13 | `LockfileNotFound` | The lockfile required to proceed was not found. |
| 14 | `InvalidLockfileFormat` | The lockfile is malformed or does not match the expected format. |
| 15 | `FetchError` | Failed to fetch data or perform a network operation. |
| 16 | `PackageManagerError` | The package manager reported an error or could not complete its task. |
| 17 | `GitError` | Generic error raised when a Git command fails. |
| 18 | `GitRemoteNotFoundError` | The specified Git remote does not exist in this repository. |
| 19 | `GitInvalidRevisionError` | The given revision identifier is not valid for this repository. |
| 20 | `PackageWithCorruptLockfileRejected` | Lockfile for this package is corrupted and cannot be used. |
| 21 | `UnsatisfiableArchitectureFilter` | Unable to satisfy the architecture constraints specified for RPM packages. |
| 22 | `NotV1Lockfile` | The Yarn Classic lockfile is not version 1 and cannot be used. |

## Usage in scripts and CI

Branch on `$?` (or the process exit code) instead of parsing stderr.
For example, retry once on fetch failure (15),
or fail the job when the lockfile is missing (13):

**Shell:**

```shell
hermeto fetch-deps pip
code=$?
if [ "$code" -eq 15 ]; then
  # Retry once on fetch failure
  hermeto fetch-deps pip
  code=$?
fi
if [ "$code" -eq 13 ]; then
  echo "Lockfile missing; add one and re-run." >&2
fi
exit "$code"
```

**Python:**

```python
import subprocess
import sys

result = subprocess.run(
    ["hermeto", "fetch-deps", "pip"],
    capture_output=True,
    text=True,
)
code = result.returncode
if code == 15:
    # Retry once on fetch failure
    result = subprocess.run(
        ["hermeto", "fetch-deps", "pip"],
        capture_output=True,
        text=True,
    )
    code = result.returncode
if code == 13:
    print("Lockfile missing; add one and re-run.", file=sys.stderr)
sys.exit(code)
```
