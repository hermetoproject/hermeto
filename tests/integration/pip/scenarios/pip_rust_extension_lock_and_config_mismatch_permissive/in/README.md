# Misconfigured Rust-based Python dependency: lock mismatch

Sometimes some Rust-based Python dependencies end up released with
Cargo lock file not matching their own Cargo.toml. This is apparently
a result of some misconfiguration of release toolchain and manifests
itself in Cargo rejecting the package during vendoring. Hermeto
provides a workaround for such packages where it would try and regenerate
a lock file if mismatch were detected. 

This test case provides a dummy package depending on [jiter v0.9.0](https://github.com/pydantic/jiter)
which is known to contain Cargo.lock which does not match Cargo.toml.
The dependencies should be successfully fetched in permissive mode
and fail to be retrieved in strict mode.
