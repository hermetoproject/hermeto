---
name: tdd-python
description: >-
  Write failing unit tests before production code in this repo, following
  hermeto's pytest conventions. Use whenever implementing or fixing anything
  under hermeto/, and for any task labeled test-only.
---

# TDD in hermeto

Tests come first. Write the failing test, see it fail for the right reason,
then write the code that makes it pass.

`AGENTS.md` is the authority on repo conventions. This skill is the procedure.
Where they disagree, `AGENTS.md` wins — say so in the PR body.

## Step 1: Read before writing

Find the test module that already covers the code you are about to change.
`tests/unit/` mirrors the `hermeto/` package tree, so the path is mechanical:

```text
hermeto/<path>/<module>.py  →  tests/unit/<path minus 'core/'>/test_<module>.py
```

```sh
# derive it, do not guess it
find tests/unit -name "test_<module>.py"
```

Read it in full. Copy its idioms — fixture style, parametrize style, how it
asserts on exceptions, how it constructs its inputs. Do not introduce a
different testing style alongside an existing one.

If the module you are changing has **no** test file at all, say so before
proceeding. A package with no tests cannot meet the coverage expectation and
the gap is worth a maintainer's attention.

## Step 2: Write the failing test

- One test function per behavior.
- **Add new test cases; do not modify existing ones.** Adding a case to an
  existing `@pytest.mark.parametrize` is fine as long as the test function
  body is unchanged. Rewriting an existing test to accommodate new behavior
  is a red flag — it usually means you are changing behavior someone relied
  on, which belongs in the PR description.
- Prefer a new `parametrize` case over a copy-pasted near-identical test.
- Assert on the exception **type** for error paths, never on the message text.
  `AGENTS.md` is explicit about this: a unit test asserts the type, an
  integration test asserts the `ExitError` code. Message wording is expected to
  be reworded for clarity and a test that pins it turns every such improvement
  into a test failure:

  ```python
  with pytest.raises(<ErrorClass>):
      ...
  ```

  Error classes for every backend live in `hermeto/core/errors.py` — use an
  existing one rather than adding a class.

- **No network.** Unit tests must run offline. Mock at the boundary the
  existing tests mock at — do not invent a new mocking layer.
- Test hermeto's logic, not the standard library or a third-party library.
  A test that asserts `yaml.safe_load` parses YAML is not a test.

## Step 3: Run it and confirm it fails correctly

Run the one test module you just changed, not the whole suite:

```sh
venv/bin/pytest <the test module from step 1> -x -q
```

Read the failure. If it fails with `ImportError`, `AttributeError`, or a
fixture error, the test is broken — not the code. Fix the test first.
The failure you want is the assertion failing on the behavior you are about
to implement.

## Step 4: Test-only tasks stop here

When the issue is labeled `test-only` (or the task is explicitly to add
coverage), commit the failing-or-passing tests alone and open the PR. Do not
touch anything under `hermeto/`.

If the new tests pass against unchanged production code, say so explicitly in
the PR body — that is a meaningful finding, not a problem to hide.

## Step 5: Implement

Write the smallest change that makes the test pass. Then:

- Re-read the test. If it now passes for a reason you did not intend, the test
  is too loose.
- Check whether the change broke an existing test. If it did, that existing
  test encodes behavior someone relied on — do not edit it to match your new
  code without explaining why in the PR body.

## Step 6: Where each layer of test belongs

| Change | Cover it with |
| --- | --- |
| Parsing, URL building, version logic, model validation | unit test in `tests/unit/` |
| A backend entry point (`fetch_*`) | **not** a unit test — integration scenario under `tests/integration/<pm>/scenarios/` |
| User-observable end-to-end behavior | a Gherkin scenario — see [`write-gherkin-specs`](../write-gherkin-specs/SKILL.md) |
| A new package-manager backend | all of the above, plus a design document |

Integration scenarios are directories with `in/` and `out/`. `out/bom.json`
and `out/.build-config.yaml` are generated — regenerate them with
`nox -s generate-test-data`, never hand-edit them.

## Step 7: Verify before each commit

```sh
venv/bin/pytest tests/unit/
nox -s lint
```

Both must pass for **every commit independently**, not just the branch tip
(`AGENTS.md`, Git Workflow). If you are splitting work into standalone
commits, that means the test and the code it covers usually land in the same
commit — a commit containing only a failing test does not satisfy the rule.

Aim for near-full coverage of new code. Check what you actually hit:

```sh
venv/bin/pytest tests/unit/ --cov=hermeto --cov-report=term-missing
```

## Step 8: Commit hygiene

Every commit:

```sh
git commit -s --trailer "Assisted-by: Claude"
```

- Sign-off (DCO) and the `Assisted-by` trailer are both required.
- The message explains **why**, not what.
- Issues and PRs are referenced by **full URL**
  (`https://github.com/hermetoproject/hermeto/issues/1802`), never `#1802`.
- New files carry `# SPDX-License-Identifier: GPL-3.0-only`.
- Rebase only, no merge commits. No gitmojis.
