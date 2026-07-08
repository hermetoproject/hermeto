# Bundled dependencies test

Verify cachi2's handling of direct and indirect bundled dependencies.

## Indirect bundled dependencies

A dependency of this package (bundle-dependencies@1.0.2) has bundled dependencies
(they are included in the bundle-dependencies-1.0.2.tgz tarball).

One of those dependencies (ansi-regex@2.0.0) is also a direct dependency.

Expected behavior:

* cachi2 shouldn't download indirect bundled dependencies
* cachi2 *should* report them in the SBOM
* cachi2 should treat ansi-regex as not bundled, because it's also a direct dependency

## Direct bundled dependencies

This package marks some of its dependencies as bundled (fecha@4.2.3, builtin-modules@1.1.1).
If you `npm pack` this package and upload it to the registry, npm will include those
dependencies in the tarball. But we are processing the source repo, not the packed
tarball.

The builtin-modules@1.1.1 dependency is also an indirect bundled dependency of bundle-dependencies.

Expected behavior:

* cachi2 should treat direct bundled dependencies as not bundled
* cachi2 should treat builtin-modules as directly bundled (=> not bundled)

## Package creation

```shell
podman run --rm -ti -v "$PWD:$PWD:z" -w "$PWD" --user 0 \
  registry.access.redhat.com/ubi9/nodejs-18-minimal bash

npm init --yes
npm add --save-dev bundle-dependencies@1.0.2
npm add ansi-regex@2.0.0
npm add --save-bundle fecha@4.2.3
npm add --save-bundle builtin-modules@1.1.1
```
