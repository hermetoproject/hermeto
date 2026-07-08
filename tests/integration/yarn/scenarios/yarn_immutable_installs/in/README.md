# Yarnberry Immutable Installs Integration Test

Its purpose is to verify that Hermeto will correctly fail a request.

Version of a dependency in package.json file was changed but the yarn.lock file was not modified. When [enableImmutableInstalls](https://yarnpkg.com/configuration/yarnrc#enableImmutableInstalls) is true (the default), Yarn will refuse to change the lockfile.
