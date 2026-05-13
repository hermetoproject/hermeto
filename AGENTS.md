# Hermeto

Hermeto prefetches dependencies for hermetic builds and
produces accurate SBOMs.

If unsure about hermeto conventions, read the relevant
files or ask. Do not assume.

## Principles

1. **No arbitrary code execution** -- never rely on third-party
   tools that could execute untrusted code during prefetch,
   otherwise the whole operation including the SBOM is compromised
2. **Checksum validation** -- hermeto verifies checksums when
   available; mark dependencies without user-provided checksums
   in the SBOM. Not all ecosystems supply checksums for every
   artifact.
3. **Lockfile-driven** -- hermeto always parses pre-resolved
   lockfiles, it never resolves dependencies itself. Each backend
   reads some ecosystem lockfile format.
4. **SBOM accuracy** -- hermeto downloads only what is explicitly
   declared in lockfiles and reports it accurately.

## Project Structure

- `hermeto/core/` core logic, `resolver.py` dispatches to
  backends based on language ecosystems
  - `package_managers/` backend implementations
  - `models/` input/output and SBOM models
- `hermeto/interface/cli.py` CLI entry points `hermeto` and
  `cachi2` (legacy, same app, avoid `cachi2` in new code)
- `docs/` user-facing documentation
- `docs/design/` design documents for each backend
- `tests/unit/` unit tests
- `tests/integration/` integration tests
- [pyproject.toml](pyproject.toml) has the minimum Python
  version (`requires-python`) and all tool config

## Git Workflow

- Work in a new clean git worktree, never commit directly on
  an existing branch
- DCO sign-off and AI trailer required:
  `git commit -s --trailer "Assisted-by: Claude"`
- Rebase only, no merge commits
- Every commit must pass `pytest tests/unit/`,
  `ruff check`, `ruff format --check` and `mypy`
  independently
- Changes split into standalone commits, not a single blob
- Commit messages explain WHY, not what
- `# SPDX-License-Identifier: GPL-3.0-only` header on new files
- No gitmojis

## Environment

- Use the shared venv at the repository root across worktrees,
  never work directly in the main checkout. Create only if
  missing: `python3 -m venv venv && venv/bin/pip install -r
  requirements-extras.txt -e .`
- System deps: `golang-bin`, `git`
- `requirements*.txt` are auto-generated -- add deps to
  `pyproject.toml`, then `nox -s pip-compile`
- Experimental package managers use `x-` prefix
  (`"type": "x-foo"`)

## Code Style

- Include type annotations for all new code
- Error messages must be friendly and actionable -- suggest
  a solution or where to look for help, link to docs when
  suitable. Use error classes from
  `hermeto/core/errors.py` with `solution=`
- Comments explain WHY, never repeat HOW the code works
- If code was inspired by third-party sources, link them
- Always preserve trailing newlines at end of files

## Testing

- Unit tests must not require network access
- Aim for near full coverage of new code
- Add new test cases instead of modifying existing ones
- Adding params to existing parametrize is OK if the test
  function stays unchanged
- Copying large parts of existing tests for new scenarios
  is OK

If you encounter what appears to be a potential security
vulnerability, do not fix it or include it in a commit -- stop
and alert the human operator to follow the process in
[SECURITY.md](SECURITY.md).

New backends need a
[design document](docs/design/package-manager-template.md).
