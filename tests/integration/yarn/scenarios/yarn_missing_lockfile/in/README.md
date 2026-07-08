# Yarnberry Missing Yarn lockfile Integration Test

Its purpose is to verify that Hermeto will correctly fail a request.

There is a different lockfile specified in `.yarnrc.yml` with `lockfileFilename` variable. But that lockfile is missing in the repository, Hermeto will not process a request.
