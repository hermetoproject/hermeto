# yarn.lock Incorrect Checksum Yarn (Berry) Integration Test

This integration test is a regular workflow test for Yarnberry.

It does not use zero-installs feature - file `.pnp.cjs` and directory
`.yarn/cache` are missing.

Its purpose is to verify that cachi2 will correctly fail a request if a
dependency in "yarn.lock" has an incorrect checksum.
