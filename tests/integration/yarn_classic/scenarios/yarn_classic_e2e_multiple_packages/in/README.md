# Integration test with multiple top-level packages

Two packages sharing the same dependencies at different versions.

## First package

dependencies:

- axios@1.6.4
- express@4.18.2
- lodash@4.17.21

## Second package

dependencies:

- axios@1.5.1
- express@4.18.2

devDependencies:

- lodash@4.17.21
