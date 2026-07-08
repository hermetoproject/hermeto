# npm-yarn-registry-lockfile3

This repo is for testing npm support in [Hermeto](https://github.com/hermetoproject/hermeto).

The main package is named `npm-yarn-registry-test`. There are several workspaces defined, each with a unique dependency.

An example of the commands needed to create the main package and one of the workspaces is:

```
npm init -y
npm init -w foo
npm install abbrev -w foo
npm install
```

Note: The pre-existing dependencies in `package-lock.json` are resolved from `registry.yarnpkg.com`, while dependencies added locally (e.g. `abbrev`) resolve from `registry.npmjs.org`, resulting in a mixed-registry lockfile v3.