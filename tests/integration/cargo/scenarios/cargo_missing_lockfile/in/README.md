# Missing Cargo.lock integration test

This test case checks that Hermeto can handle a Rust project that does not have a Cargo.lock file in
the permissive mode. Hermeto will generate a Cargo.lock file and proceed with a warning.
