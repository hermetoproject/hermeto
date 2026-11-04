# hello-rust-ext

A minimal Python extension built with [maturin](https://github.com/PyO3/maturin) and [pyo3](https://github.com/PyO3/pyo3), created as a test fixture for [Hermeto](https://github.com/hermetoproject/hermeto)'s Python + Rust extension support.

The package exposes a single function written in Rust — a small number guessing game — just enough to verify that Hermeto can correctly fetch, build, and generate an SBOM for a maturin-based Python package with Rust dependencies.

## Dependencies

- `pyo3` — Python/Rust bindings
- `rand` (via git) — random number generation, pulled from `https://github.com/rust-random/rand`

The git-sourced `rand` dependency is intentional — it exercises Hermeto's handling of transitive Rust git dependencies in the SBOM.

## Usage

```bash
pip install maturin
maturin develop
```
