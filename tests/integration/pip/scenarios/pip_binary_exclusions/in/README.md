# Binary fetching with source-only exceptions

The scenario verifies that excluded packages are fetched only as sdists while
the remaining package uses the implicit `:all:` binary filter default and is
fetched as both a compatible wheel and an sdist.

NOTE: smaller package sizes are preferable.
