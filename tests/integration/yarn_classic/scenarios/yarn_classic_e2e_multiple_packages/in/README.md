# Integration test with multiple top-level packages

This project is almost entirely copied from the Yarn 2.x+ project at https://github.com/cachito-testing/cachi2-yarn-berry/commit/b6f47bd07e669c8d2eced8015c4bfb06db499493
and simply modified to make it a Yarn 1.x project.

This branch covers testing repo with multiple top-level packages.
The repo contains two packages. They both share the same (random) dependencies.

axios: `1.6.4` and `1.5.1` (different versions)

express: `4.18.2` (same version)

lodash: `4.17.21` (same version, but the second package has it as a dev dependency)

_See the summary below._

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
