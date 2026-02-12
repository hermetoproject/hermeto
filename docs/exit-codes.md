# Exit codes

Hermeto uses a rich set of exit codes which can signal different internal
conditions. The codes are described in the table below. Please note that the
codes are not meant to be used alone, they are intended as an aid for automated
systems (scripts, CI) which were found to sometimes benefit from such a set.
Most errors require additional information to be corrected, this information is
always saved to logs.

| Group | Code                       | Meaning                     |
| ----- | -------------------------- | --------------------------- |
|     1 |                          0 | No errors                   |
|     2 |                          1 | Unexpected internal error   |
|     3 |                       2, 4 | Hermeto CLI usage error     |
|     4 |           3, 7, 9&ndash;12 | Filesystem error            |
|     5 | 5, 13, 14, 16, 20&ndash;22 | Packages or lockfiles error |
|     6 |             6, 17&ndash;19 | Git error                   |
|     7 |                         15 | Network / fetch error       |
|     8 |                          8 | Unsupported feature         |

Recommended course of action for each group:

| Group | Action                                                                   |
| ----- | ------------------------------------------------------------------------ |
|     1 | Do nothing; there was no error                                           |
|     2 | Collect run logs and stderr, share them with Hermeto maintainers         |
|     3 | Verify the commands you issued to Hermeto                                |
|     4 | Refer to logs to find out which filesystem object caused the trouble     |
|     5 | Refer to logs to find out which package or lockfile caused the trouble   |
|     6 | Refer to logs to find out which repo or Git operation caused the trouble |
|     7 | Try again; if the issue persists, contact Hermeto maintainers with logs  |
|     8 | You may request support for the feature                                  |

For bugs or unsupported features, [contact the Hermeto maintainers][].

[contact the Hermeto maintainers]: https://github.com/hermetoproject/hermeto/issues
