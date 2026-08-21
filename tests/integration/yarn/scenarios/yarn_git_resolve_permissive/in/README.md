# Yarn Berry git dependency resolution

Fetch-only scenario for Yarn Berry projects that resolve packages from Git/GitHub.
Hermeto supports these protocols with `--mode=permissive`; strict mode rejects them
because the lockfile must be rewritten to reference local tarballs.

## [Protocols](https://v3.yarnpkg.com/features/protocols)

- [x] Semver
- [ ] Tag
- [x] Npm alias
- [x] Git
- [x] GitHub
- [x] File
- [x] Link
- [x] Patch
- [x] Portal
- [x] Workspace
- [ ] ~~Exec~~
- [x] Https

## Dependencies

Includes a `github:` dependency (`get-document`) plus file, link, portal, workspace,
patch, HTTPS archive, and npm-alias packages.
