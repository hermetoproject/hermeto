# pnpm v11 scenario

A comprehensive test scenario for pnpm v11 that demonstrates all available package sources in the
pnpm ecosystem.

## Prerequisites

Check the pnpm version in the `package.json` file. If you don't have this version, you can
install it via corepack. It is possible to have multiple pnpm versions installed on the system.

## Dependencies

```bash
pnpm install
```

This project is designed to validate that pnpm v11 correctly handles:

- [x] Standard npm registry packages with semantic versioning
- [x] JSR packages with the `jsr:` protocol
- [x] GitHub repository references
- [x] GitHub tarball URLs
- [x] Local file-based tarballs
- [x] Patched dependencies with custom modifications
- [x] Workspace package dependencies
- [x] Dev and optional dependency types

## Useful resources

- <https://pnpm.io/11.x/migration>
- <https://pnpm.io/11.x/installation>
- <https://pnpm.io/11.x/package-sources>
- <https://pnpm.io/11.x/settings>
- <https://pnpm.io/11.x/workspaces>
- <https://pnpm.io/11.x/cli/patch>
- <https://pnpm.io/11.x/cli/patch-commit>
