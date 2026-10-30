# Yarn Classic and Berry Version Mismatch Integration Test

This project has a Classic `yarn.lock` and declares `yarn@3.6.2` in the
`packageManager` field of `package.json`.

Hermeto must reject the mismatch before downloading or executing Yarn.
Previously this field was ignored for Classic projects; it now controls the
selected version and must agree with the lockfile's Classic format.
