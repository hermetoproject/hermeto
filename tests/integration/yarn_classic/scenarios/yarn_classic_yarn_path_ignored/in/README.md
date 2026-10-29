# Yarn Classic Ignores yarn-path Integration Test

This Classic project points `yarn-path` in `.yarnrc` at a checked-in Yarn 3.6.1
executable. Hermeto must ignore that setting and executable during prefetch.

The project has no `packageManager` field, so Hermeto selects the fixed default
Classic version, `1.22.22`.
