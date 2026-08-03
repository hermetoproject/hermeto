# gomod-multiple-modules

The repository contains a top-level Go module and two submodules.

The root module and spam-module are versioned separately (see the git tags). There's
no tag for eggs-module - Hermeto should give it a `v0.0.0-*` [pseudo-version][pseudo-version].

The submodules depend on different versions of rsc.io/quote - Hermeto should download
and report both.

[pseudo-version]: https://go.dev/ref/mod#pseudo-versions
