# gomod-vendor-non-vendor-mix

This repository contains two Go modules demonstrating a mixed vendored and non-vendored
module setup. This is a regression test scenario to validate that Hermeto correctly
processes repositories containing both vendored and non-vendored Go modules.

**Related issue: https://github.com/hermetoproject/hermeto/issues/1160**

## Structure

- **vendored-module/** - A Go module with external dependency/dependencies that uses Go vendoring.
  The `vendor/` directory contains all dependencies.

- **non-vendored-module/** - A Go module that depends on `vendored-module` but does
  not use vendoring. It uses a `replace` directive to reference the local vendored-module.

## Test Scenario

This scenario validates that Hermeto can:

1. Correctly handle a repository with both vendored and non-vendored modules
2. Process dependencies from a vendored module's `vendor/` directory
3. Process dependencies from a non-vendored module normally

To ensure comprehensive coverage, this test should be executed in both processing
orders: first with the vendored module processed before the non-vendored one, and
then with the order reversed!
