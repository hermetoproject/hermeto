---
name: write-gherkin-specs
description: >-
  Write Gherkin .feature specifications before implementing any change to a
  package manager backend or user-visible CLI behavior, then implement step
  definitions against an approved spec. Use when implementing a feature, fixing
  a bug, or handling a test-only task, and whenever an issue is labeled
  spec-only.
---

# Write Gherkin specs

Behavior that a hermeto user can observe is specified in Gherkin before it is
implemented. A human reviews and merges the specification; implementation
happens in a separate pull request afterwards.

This does not replace unit tests. It sits above them — see
[`tdd-python`](../tdd-python/SKILL.md) for the unit-test discipline.

## Step 1: Decide whether a scenario is warranted

Write a scenario when the behavior is **observable from the outside** — the
CLI accepts or rejects an input, the SBOM gains or loses a component, the
rewritten lockfile has a particular shape, an error message appears.

Do **not** write a scenario for:

- pure parsing helpers, URL construction, version comparison — those are unit
  tests
- anything whose assertion is not binary ("the SBOM looks reasonable")
- raising coverage

If a unit test is the right tool, say so and write that instead. Per
`AGENTS.md`, backend entry points (`fetch_*`) are deliberately not unit tested
because they need heavy mocking — a scenario plus an integration test is the
correct cover for them.

## Step 2: Write the feature file first

Create or update `features/<capability>.feature`. The filename is the
kebab-case capability, e.g. `features/pnpm-lockfile-versions.feature`.
One `Feature:` per user-visible capability.

Match the structure and voice of `references/exemplar.feature`.

## Step 3: Scenario rules — hard limits, not preferences

- Exactly `Given` / `When` / `Then`, in that order. `And` continues the
  previous keyword.
- 3–7 steps per scenario. More than 7 means the scenario does two things —
  split it.
- **Steps name user-visible behavior only.** Never name a Python class,
  function, module, exception class, or source path in a step. Lockfile field
  names (`lockfileVersion`, `packages`, `snapshots`) and CLI flags *are*
  user-visible and are allowed. `PnpmLock.from_file` is not.
- One behavior per scenario. One assertion per `Then`; a `Then` carrying two
  assertions is two steps.
- Use `Scenario Outline` with `Examples:` when the same behavior varies by
  input — this mirrors the repo's existing preference for `parametrize` over
  copy-pasted tests.
- Present tense, third person, from the user's point of view:
  `When hermeto fetches the dependencies`, not `When we call fetch_pnpm_source`.
- Reuse an existing step phrase before inventing a new one. Near-duplicate
  phrasings fragment the step library.
- Tags are exceptions only — `@slow`, `@wip`. Untagged scenarios run
  everywhere. Do not build a tag taxonomy.
- **A tag must be registered before it is used.** pytest-bdd turns Gherkin
  tags into pytest markers, and `strict = true` in `pyproject.toml` rejects
  unregistered markers — an unknown tag fails the run, it does not warn. To
  add a tag, declare it in `markers` under `[tool.pytest.ini_options]` in the
  same commit. If that feels like too much friction for the tag you had in
  mind, that is the signal: do not add it.
- Do not encode an issue reference as a tag. Gherkin tags cannot contain `:`
  or `/`, so a tag cannot hold the full issue URL that `AGENTS.md` requires.
  Put the URL in a comment line above the scenario instead:

  ```gherkin
  # https://github.com/hermetoproject/hermeto/issues/1802
  Scenario: A multi-document lockfile is accepted
  ```

- Error-message scenarios assert that the message is **actionable** — the
  scenario names the suggested remedy, because `AGENTS.md` requires error
  messages to suggest a solution.

## Step 4: Spec-only mode — STOP HERE when the issue is labeled `spec-only`

When the issue carries the `spec-only` label, this is a test-only task whose
single artifact is the feature file.

- Write or update `features/<capability>.feature`. Nothing else.
- Do NOT write step definitions. Do NOT touch `hermeto/`.
- Verify with `nox -s bdd-lint`. Do NOT run `nox -s bdd` — undefined steps are
  expected and correct at this stage.
- Commit the feature file alone, signed off, with the AI trailer:
  `git commit -s --trailer "Assisted-by: Claude"`.
- PR title: `spec: <capability>`.
- In the PR body, list each scenario with one line on what it pins down, and
  link the upstream issue by full URL, so a reviewer can judge the
  specification without reading Gherkin.

A human reviews and merges that PR. Implementation happens in a later run.

## Step 5: Implementing against an approved spec

Feature files already on the default branch are **approved specifications**.

- Never modify an existing `.feature` file while implementing.
- If a scenario is wrong, ambiguous, or unimplementable, stop and say so in the
  PR body. Do not silently adjust the scenario to match the implementation —
  that defeats the review that approved it.
- Implement step definitions first, watch them fail, then write the production
  code. See [`tdd-python`](../tdd-python/SKILL.md).

## Step 6: Step definitions

- Step definitions live in `tests/bdd/steps/`. Shared fixtures go in
  `tests/bdd/conftest.py` — do not create per-feature conftest files.
- Reuse an existing step definition before writing a new one. Search
  `tests/bdd/steps/` for a matching phrase first.
- Step definitions contain no assertions about internals. They translate a
  user-visible phrase into an action, or a check on observable state: the
  generated SBOM, the rewritten project files, the raised error and its
  message, the exit code.
- No network access, ever. Input fixtures are committed under
  `tests/bdd/fixtures/`. Mock downloads at the same boundary the unit tests
  for the code under test already mock — find that module first and match it,
  do not invent a new mocking layer.
- Never assert on log output. Assert on return values, generated files, and
  raised exceptions.
- A step definition longer than ~15 lines is doing too much; extract a helper.

## Step 7: Verify before committing

Run `nox -s bdd` and `pytest tests/unit/` and `nox -s lint`. Per `AGENTS.md`,
**every commit must pass independently** — not just the branch tip.

A scenario that cannot be made to pass is either a spec defect or an
implementation defect. Diagnose which and fix that. Never delete or `@skip` a
scenario to get the suite green; if a scenario should be removed, stop and
explain why in the PR body.
