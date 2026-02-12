# Exit codes

Hermeto uses stable exit codes so that scripts and CI can detect failure reasons
without parsing stderr. The CLI exits with `0` on success and with one of the
codes below on failure.

## Unexpected / system error

**Codes:** 1

An unexpected error occurred.

Contact the Hermeto maintainers.

## Command usage

**Codes:** 2, 4

The command was used incorrectly. Wrong syntax, subcommand or argument.

Review your input and the documentation.

## Paths and files

**Codes:** 3, 7, 9, 10, 11, 12

A path sits outside the allowed directory, a file format is wrong, a required
executable is missing, or a checksum failed.

Keep paths and files within the allowed directories.

## Packages and lockfiles

**Codes:** 5, 13, 14, 16, 20, 21, 22

A package was rejected, a lockfile is missing or invalid, the package manager
failed, or there is an architecture or lockfile version issue.

Check the package and lockfile.

## Git

**Codes:** 6, 17, 18, 19

A Git-related operation failed (repository, remote, or revision).

Check your repository configuration.

## Network / fetch

**Codes:** 15

The system failed to fetch data (e.g. network error).

*Try again*; if the issue persists, contact the Hermeto maintainers.

## Unsupported feature

**Codes:** 8

The requested feature is not supported.

You may request support for it.

---

For details on any failure, check the command output and logs.

For bugs or unsupported features, [contact the Hermeto maintainers][].

[contact the Hermeto maintainers]: https://github.com/hermetoproject/hermeto/issues
