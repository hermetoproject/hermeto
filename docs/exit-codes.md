# Exit codes

Hermeto uses **stable exit codes per error type** so that scripts and CI can
detect failure reasons without parsing stderr. The CLI exits with `0` on success
and with one of the codes below on failure.

| Code | Description |
| ---- | ----------- |
| 1 | An unexpected system error occurred. Contact the maintainers. |
| 2 | The command usage was incorrect. Review your input syntax. |
| 3 | The file path sits outside the allowed directory. Ensure files remain within the source or output directories. |
| 4 | The input arguments are invalid. Check your arguments. |
| 5 | The package was rejected. Check package validity. If the error persists, contact the maintainers. |
| 6 | The package is not a Git repository. Ensure the directory is a valid Git repository. |
| 7 | The file format is incorrect. Verify the file structure. If the issue persists, contact the maintainers. |
| 8 | The feature is unavailable. Contact the maintainers to request this feature. |
| 9 | A required executable is missing. Install the executable in your PATH. |
| 10 | The file verification failed. Verify the file is not corrupt and checksums are correct. |
| 11 | The checksum data is invalid. Ensure the checksum matches the expected format. |
| 12 | The integrity checksum is missing. Ensure the checksum exists. |
| 13 | A required lockfile is missing. Ensure the file exists and is checked into the repository. |
| 14 | The lockfile format is invalid. Check the syntax in the lockfile. |
| 15 | The system failed to fetch data. Try again. If the issue persists, contact the maintainers. |
| 16 | The package manager failed. Check the logs for details. |
| 17 | The Git operation failed. Check your repository configuration and try again. |
| 18 | The Git remote is missing. Check the remote name and configuration. |
| 19 | The Git revision is invalid. Check the branch, tag, or commit hash. |
| 20 | The lockfile does not match the package configuration. Contact the dependency owner to resolve inconsistencies. |
| 21 | The architecture constraints cannot be satisfied. Ensure the lockfile supports the target architecture. |
| 22 | The Yarn lockfile version is incompatible. Use a Yarn v1 lockfile. |
