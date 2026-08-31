# Pylock test

This integration test exercises multiple `pylock.toml` dependency kinds in a
single project. It replaces several narrower pylock scenarios (missing hashes,
VCS) with one consolidated lockfile.

The lockfile includes:

- PyPI index packages (`smmap`, `gitdb`)
- A git VCS dependency (`gitpython`)
