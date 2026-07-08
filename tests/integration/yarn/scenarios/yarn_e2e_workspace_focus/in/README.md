# Yarn workspace focus integration test

Integration test project for Hermeto's [`yarn workspaces focus`](https://yarnpkg.com/cli/workspaces/focus) support. Verifies that when focusing on a specific workspace, only its dependencies (and those of its transitive workspace dependencies) are included in the output. Dependencies belonging to non-focused workspaces and the root must be excluded. All workspaces include lifecycle scripts that `exit 1` to verify Hermeto strips them before running the focus command and does not execute scripts for unfocused workspaces.

## Workspace structure

- **root** — declares a dependency on `nanoid` to verify root deps are excluded when not focused.
- **packages/app** — the focused workspace. Depends on `shared` (workspace), `tooling` (workspace devDependency), and `is-number` (npm).
- **packages/shared** — a direct workspace dependency of `app`. Depends on `is-odd` (npm), which itself depends on `is-number@6`.
- **packages/tooling** — a workspace devDependency of `app`. Depends on `ms` (npm).
- **packages/unrelated** — an independent workspace with its own dependency (`left-pad`), expected to be excluded.

## Expected outcome

Focusing on `app` should produce output containing:

| Component        | Reason                                        |
| ---------------- | --------------------------------------------- |
| `app`            | Directly focused workspace                    |
| `shared`         | Direct workspace dependency of `app`          |
| `tooling`        | Workspace devDependency of `app`              |
| `is-number@7`    | Direct npm dependency of `app`                |
| `is-odd@3`       | Direct npm dependency of `shared`             |
| `is-number@6`    | Transitive npm dependency via `is-odd`        |
| `ms`             | Direct npm dependency of `tooling`            |

The following should **not** appear:

| Component    | Reason                              |
| ------------ | ----------------------------------- |
| `root`       | Root workspace, not focused         |
| `nanoid`     | Dependency of root                  |
| `unrelated`  | Workspace not in the focused set    |
| `left-pad`   | Dependency of `unrelated`           |
