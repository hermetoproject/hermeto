# Cargo/Rust integration test

This repository is meant to test a wide range of cases that can occur in processing of Rust projects.

## Cases Covered

- [ x ] Standard crates.io dependency
- [ x ] crates.io dependency that is not pinned
- [ x ] git dependency
- [ x ] path dependency
- [ x ] patched dependency
- [ x ] workspaces
- [ x ] workspace dependency inheritance
- [ x ] dev dependency
- [ x ] build dependency
- [   ] dependency from non-standard registry
- [   ] platform specific dependency
- [ x ] dependency with multiple-locations

To see more about Cargo dependency types, check the [official docs](https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html).
