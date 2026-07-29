# Corepack installs Yarn 1.x Integration Test

This repository provides an extremely simple Yarn 1.x project which can
be used to test whether lifecycle scripts are executed by yarn commands.

All lifecycle scripts should exit in error and so if any script is executed, the
`yarn install` command should fail.
