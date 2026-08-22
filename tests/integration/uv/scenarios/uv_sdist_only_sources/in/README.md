# uv source kinds resolving to sdists

This test covers a project whose `uv.lock` exercises every source kind uv can
record, with each dependency resolving to an sdist: `idna` from a registry,
`packaging` from a direct URL, `itsdangerous` from git, and `localpkg` from a
path pointing at an sdist vendored in the repository.

Git sources carry no checksum in the lockfile, so `itsdangerous` should be
reported with a missing hash in the SBOM.
