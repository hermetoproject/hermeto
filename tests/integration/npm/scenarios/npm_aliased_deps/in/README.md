# npm-with-aliased-deps

This repo is used to test cachi2 npm functionality where the npm package
has aliased dependencies.

The fecha package is included as a dependency several times, aliased under
different names and with different versions. One of these aliased versions
is from the npm registry and the other is a git dependency.

All three versions of the fecha dependency should be included in the SBOM
with their respective versions, but be reported with the same "fecha" package
name.
