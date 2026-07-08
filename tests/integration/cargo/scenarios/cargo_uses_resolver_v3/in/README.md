# Test Cargo resolver v3 with unspecified rust-version

A minimal Rust project for testing Cargo dependency resolution behavior.

## Configuration

- **Resolver**: Version 3 (Rust 2024 edition resolver)
- **Rust Version**: Not specified (intentionally omitted)

## Purpose

This project tests the scenario where:
- Cargo resolver v3 is used
- No `rust-version` field is specified in Cargo.toml
- Standard crates.io dependencies are present

This configuration triggers MSRV-aware dependency resolution in Cargo, which requires checking the installed Rust version.

