# Yarnberry Regular Workflow Integration Test

This integration test is a regular workflow test for Yarnberry.
It does not use zero-installs feature - file `.pnp.cjs` and directory `.yarn/cache` are missing.

## [Protocols](https://v3.yarnpkg.com/features/protocols)

- [x] Semver
- [ ] Tag
- [x] Npm alias
- [ ] ~~Git~~
- [ ] ~~GitHub~~
- [x] File
- [x] Link
- [x] Patch
- [x] Portal
- [x] Workspace
- [ ] ~~Exec~~
- [x] Https

_Cachi2 does not support the Git, GitHub and Exec protocols._

## [Pre|Post install scripts](https://yarnpkg.com/advanced/lifecycle-scripts)

```bash
"preinstall": "touch pwned-from-preinstall.txt",
"install": "touch pwned-from-install.txt",
"postinstall": "touch pwned-from-postinstall.txt"
```

See this [commit](https://github.com/hermetoproject/integration-tests/commit/646ba0d70bd7e08527985d70b663aa595800396c).

Arbitrary scripts are executed before and after `yarn install` command.

## Dependencies

- [x] dev dependencies
- [x] optional dependencies
- [x] peer dependencies
- [x] scoped dependencies
- [x] compiled dependencies

### Scoped dependencies

Starting with _@_.

```bash
"@types/node": "^20.4.5",
"@types/yargs": "^17",
```

### Optional dependencies

See this [commit](https://github.com/hermetoproject/integration-tests/commit/4326fbbde1b1770e752138a9347e13fcfaabc9be).

```bash
"fsevents": "^2.3.2", # only for MacOS
```

### Compiled dependencies

See this [commit](https://github.com/hermetoproject/integration-tests/commit/4066baa6ff91ce6d4491370479d8b8c23ed7dd37).

Let the container build to do the compilation, not Hermeto.

```bash
"tree-sitter-json": "^0.20.0",
```
