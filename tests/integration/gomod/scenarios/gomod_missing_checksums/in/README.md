# gomod multiple modules integration test

The repository contains a top-level Go module and two submodules.

The root module and spam-module are versioned separately (see the git tags). There's
no tag for eggs-module - Hermeto should give it a `v0.0.0-*` [pseudo-version][pseudo-version].

The submodules depend on different versions of rsc.io/quote - Hermeto should download
and report both.

## Missing checksums

In this branch, the go.sum files are missing the checksums for some dependencies.
This is to simulate the situation where a user forgets to update the go.sum file
when manipulating dependencies.

* `golang.org/x/text`
  * missing in both `eggs-module` and `spam-module`
* `rsc.io/sampler`
  * missing in `eggs-module`

[pseudo-version]: https://go.dev/ref/mod#pseudo-versions
