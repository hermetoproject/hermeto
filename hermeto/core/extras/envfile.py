# SPDX-License-Identifier: GPL-3.0-only
import json
import re
import shlex
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Optional

from hermeto import APP_NAME
from hermeto.core.errors import UnsupportedFeature
from hermeto.core.models.output import BuildConfig, EnvironmentVariable

# Pattern that matches ${VAR_NAME} placeholders (brace-delimited only, never bare).
_REF_PATTERN = re.compile(r"\$\{(\w+)\}")


def _extract_refs(value: str) -> list[str]:
    """Return all variable names referenced as ${NAME} in *value*."""
    return _REF_PATTERN.findall(value)


def _substitute(value: str, mappings: dict[str, str]) -> str:
    """Replace every ${NAME} in *value* using *mappings* (brace-delimited only).

    Unknown placeholders are left untouched so callers can detect them later.
    Output_dir is treated as a regular mapping entry; pass it in *mappings* if needed.
    """

    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        name = m.group(1)
        return mappings.get(name, m.group(0))

    return _REF_PATTERN.sub(_replace, value)


def _detect_cycles(
    all_vars: list[EnvironmentVariable],
    name_to_var: dict[str, EnvironmentVariable],
) -> None:
    """Raise ValueError if a cycle exists in the ${VAR} reference graph.

    Uses iterative DFS; detected cycles are reported as a readable chain:
      "Circular variable reference detected: A → B → A"
    """
    # Build adjacency list restricted to variables we know about.
    adj: dict[str, list[str]] = {
        v.name: [r for r in _extract_refs(v.value) if r in name_to_var] for v in all_vars
    }

    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str, path: list[str]) -> Optional[list[str]]:
        """Return the cycle path if found, else None."""
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        for neighbour in adj.get(node, []):
            if neighbour not in visited:
                result = dfs(neighbour, path)
                if result is not None:
                    return result
            elif neighbour in rec_stack:
                # Found cycle: slice from where the cycle starts to its end.
                cycle_start = path.index(neighbour)
                return path[cycle_start:] + [neighbour]
        path.pop()
        rec_stack.discard(node)
        return None

    for var in all_vars:
        if var.name not in visited:
            cycle = dfs(var.name, [])
            if cycle is not None:
                chain = " → ".join(cycle)
                raise ValueError(f"Circular variable reference detected: {chain}")


def _topological_sort(
    names: list[str],
    adj: dict[str, list[str]],
) -> list[str]:
    """Return *names* in topological order given the dependency adjacency list *adj*.

    *adj[n]* lists the nodes that *n* depends on (i.e., must come **before** *n*).
    The returned order has every dependency before the node that needs it.

    Kahn's algorithm — assumes no cycles (caller checked already).
    Only edges between nodes present in *names* are considered.
    """
    name_set = set(names)
    # Restrict deps to within the name_set.
    deps_of: dict[str, list[str]] = {
        n: [d for d in adj.get(n, []) if d in name_set] for n in names
    }

    # Build reverse edges: dependency → list of nodes that need it.
    # In Kahn's, in-degree of N = number of its unprocessed dependencies.
    in_degree: dict[str, int] = {n: len(deps_of[n]) for n in names}
    # reverse_adj[d] = list of nodes that depend on d
    reverse_adj: dict[str, list[str]] = {n: [] for n in names}
    for n, deps in deps_of.items():
        for d in deps:
            reverse_adj[d].append(n)

    # Start with nodes whose dependencies are all satisfied (in_degree == 0).
    queue: deque[str] = deque(n for n in names if in_degree[n] == 0)
    order: list[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        # Mark `node` as resolved; reduce in-degree of every node that needs it.
        for dependent in reverse_adj[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Any remaining nodes with in_degree > 0 indicate a cycle that slipped
    # through (shouldn't happen if _detect_cycles ran first).
    if len(order) != len(names):
        remaining = [n for n in names if n not in set(order)]
        raise ValueError(
            f"Unexpected cycle among path variables: {', '.join(remaining)}"
        )
    return order


class EnvFormat(str, Enum):
    """Supported environment file formats."""

    json = "json"
    env = "env"
    sh = "env"

    @classmethod
    def based_on_suffix(cls, filepath: Path) -> "EnvFormat":
        """Determine the EnvFormat from the filename."""
        suffix = filepath.suffix.removeprefix(".")
        try:
            return cls[suffix]
        except KeyError as e:
            reason = (
                f"file has no suffix: {filepath}" if not suffix else f"unsupported suffix: {suffix}"
            )
            raise UnsupportedFeature(
                f"Cannot determine envfile format, {reason}",
                solution=(
                    f"Please use one of the supported suffixes: {cls._suffixes_repr()}\n"
                    f"You can also define the format explicitly instead of letting {APP_NAME} choose."
                ),
            ) from e

    @classmethod
    def _suffixes_repr(cls) -> str:
        return ", ".join(
            f"{name}[=={member.value}]" if name != member.value else name
            for name, member in cls.__members__.items()
        )


def generate_envfile(build_config: BuildConfig, fmt: EnvFormat, relative_to_path: Path) -> str:
    """Generate an environment file in the specified format.

    Some environment variables need to be resolved relative to a path for which @output_dir is
    used. Generally, this should be the path to the output directory where dependencies were
    fetched.

    Supported formats:
    - json: [{"name": "GOCACHE", "value": "/path/to/output-dir/deps/gomod"}, ...]
    - env: export GOCACHE=/path/to/output-dir/deps/gomod
           export ...

    Resolution order (two-pass):
    1. Detect cycles in the ${VAR} reference graph (raise ValueError if found).
    2. Resolve kind="path" variables in topological order (dependencies first):
       - Absolute values are kept unchanged.
       - Empty values raise ValueError.
       - Relative values are prepended with *relative_to_path*.
       - Any ${VAR} references within path vars are substituted using already-
         resolved path variables (allows chained path→path dependencies).
    3. Build a full substitution mapping from all variables (using resolved
       path values) plus ``output_dir`` for legacy template compatibility.
    4. Resolve all remaining (non-path) variables via ${VAR} substitution.
    5. Raise ValueError for any ${VAR} placeholders still present after
       substitution (indicates a reference to an undefined variable).
    6. Write variables to the env file in their original declaration order.
    """
    all_vars = build_config.environment_variables  # list in declaration order

    # Build name→var lookup (declaration order preserved in all_vars).
    name_to_var: dict[str, EnvironmentVariable] = {v.name: v for v in all_vars}

    # ── Step 0: global cycle detection ────────────────────────────────────────
    _detect_cycles(list(all_vars), name_to_var)

    # ── Step 1: resolve kind="path" variables, in dependency order ────────────
    path_vars = [v for v in all_vars if v.kind == "path"]
    path_names = [v.name for v in path_vars]

    # Adjacency: path var → path vars it depends on.
    path_adj: dict[str, list[str]] = {
        v.name: [r for r in _extract_refs(v.value) if r in set(path_names)] for v in path_vars
    }
    # Topological order: dependencies come FIRST so we can substitute as we go.
    topo_order = _topological_sort(path_names, path_adj)

    # Resolved values for path vars (populated incrementally).
    resolved_path: dict[str, str] = {}

    for name in topo_order:
        var = name_to_var[name]
        raw = var.value

        if raw == "":
            raise ValueError(
                f"Path variable '{name}' has an empty value; "
                "cannot prepend output directory to an empty path."
            )

        # First substitute any references to already-resolved path vars.
        # This handles chained path→path dependencies, e.g.:
        #   GOMODBASE (path) = ${GOMODCACHE}/cache
        # After substitution, the value becomes an absolute path pulled from
        # the already-resolved GOMODCACHE, so we must NOT prepend output_dir again.
        substituted = _substitute(raw, resolved_path)

        if Path(substituted).is_absolute():
            # Value is already absolute (either originally or after substitution).
            resolved = substituted
        else:
            # Still relative — prepend output_dir.
            resolved = f"{relative_to_path.as_posix()}/{substituted}"

        resolved_path[name] = resolved

    # ── Step 2: build full mappings (resolved path values + non-path values) ──
    mappings: dict[str, str] = {}
    for var in all_vars:
        if var.kind == "path":
            mappings[var.name] = resolved_path[var.name]
        else:
            mappings[var.name] = var.value
    # Legacy compatibility: callers expect ${output_dir} to work in values.
    mappings["output_dir"] = relative_to_path.as_posix()

    # ── Step 3: resolve non-path variables ────────────────────────────────────
    resolved_all: dict[str, str] = dict(resolved_path)  # seed with path results
    for var in all_vars:
        if var.kind != "path":
            resolved_all[var.name] = _substitute(var.value, mappings)

    # ── Step 4: detect undefined references ───────────────────────────────────
    for var in all_vars:
        resolved_value = resolved_all[var.name]
        leftover = _REF_PATTERN.findall(resolved_value)
        # Filter out 'output_dir' — it is a synthetic key, not a user-defined var.
        undefined = [ref for ref in leftover if ref != "output_dir" and ref not in name_to_var]
        if undefined:
            raise ValueError(
                f"Variable '{undefined[0]}' referenced by '{var.name}' is not defined."
            )

    # ── Step 5: emit output in original declaration order ─────────────────────
    env_vars = [(var.name, resolved_all[var.name]) for var in all_vars]

    if fmt == EnvFormat.json:
        content = json.dumps([{"name": name, "value": value} for name, value in env_vars])
    else:
        content = "\n".join(
            f"export {shlex.quote(name)}={shlex.quote(value)}" for name, value in env_vars
        )
    return content
