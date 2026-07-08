# Missing hashes test

This test covers a scenario where `requirements.txt` is missing hashes. Hermeto should still
successfully prefetch all packages and report missing hashes (checksums) in the SBOM.
