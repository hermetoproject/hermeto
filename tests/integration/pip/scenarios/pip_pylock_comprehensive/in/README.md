# Comprehensive pylock test

This integration test exercises multiple `pylock.toml` dependency kinds in a
single project. It replaces several narrower pylock scenarios (missing hashes,
VCS) with one consolidated lockfile.

The lockfile includes:

- PyPI index packages (`smmap`, `gitdb`, `aiowsgi`) locked by version only,
  without per-artifact hashes
- A git VCS dependency (`gitpython`)

Index packages omit hashes because Hermeto mirrors pip's `--require-hashes`
behaviour: once any dependency carries a hash, every dependency in the
lockfile must. VCS sources cannot provide wheel/sdist hashes the same way, so
hashed and unhashed dependencies cannot be mixed in one pylock file.
