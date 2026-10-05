"""Rust binding- and guard-attribution for the unreaped-spawn gate.

Split out of `_spawn_rust.py`: everything here answers "what is this binding,
and does it carry a guard that actually reaps", which is guard attribution. The
statement walk in `_spawn_rust` uses it and does not belong beside it.

Kept as a module rather than folded in because the guard rules are the part that
was wrong twice: a guard on one binding must not launder a second, unguarded
one, and a `Drop` counts only if its body kills or waits.
"""

from __future__ import annotations

import re

from _spawn_lex import FN, IMPL_TYPE, LET_BIND


def _stmt_head(lines, at, sig):
    """The line where the statement containing the spawn BEGINS.

    Not the spawn line. A guard very often wraps the spawn from ABOVE -- `let mut child =
    ReapOnDrop(Command::new(..).spawn()?)` puts `ReapOnDrop(` four lines higher -- and a window
    that starts at the `.spawn()` cannot see the guard, so the incident's own shipped fix
    (`test_a_guard_is_clean_even_with_assertions_below_the_spawn`) was reported as a leak. Walking
    back is bounded by `;` and `}`, and masking has already removed any that lived inside a string
    or a comment.
    """
    for j in range(at, sig - 1, -1):
        if LET_BIND.search(lines[j]):
            return j
        if j < at and re.search(r"[;}]", lines[j]):
            break
    return at


def _binding(lines, at, sig):
    """The identifier the spawn's result is bound to, taken from the statement's own `let`.

    `None` when there is none: a spawn whose `Child` is never bound is dropped at the end of the
    statement, which is a live orphan, and a spawn inside a `Self { child }` is handled by the
    attribution rules rather than by a binding.
    """
    m = LET_BIND.search(lines[_stmt_head(lines, at, sig)])
    return m.group(1) if m else None


def _self_type(lines, sig):
    """The type an `impl` block's `Self` names, or None.

    `Self` is the load-bearing token in a factory -- `Holder::open` ends `Self { child }`, and
    `monitor`'s guard ends `Self(command.spawn()?)`. Accepting a bare `Self(` with nothing to
    resolve it against is what let a `Drop` that does NOTHING pass as a guard, so `Self` has to name
    a type and that type has to be in `guard_types`.
    """
    for j in range(sig - 1, -1, -1):
        m = IMPL_TYPE.match(lines[j])
        if m:
            return m.group(1)
        if FN.match(lines[j]) or re.match(r"^\s*mod\b|^\s*}", lines[j]):
            return None
    return None


def _known_types(masked: str) -> set[str]:
    """Types this file DEFINES, by any declaration the compiler would accept."""
    return {
        m.group(1)
        for m in re.finditer(
            r"\b(?:struct|enum|union|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*)", masked
        )
    }


def rust_region(masked: str) -> str:
    """Blank everything OUTSIDE every `#[cfg(test)]` region, preserving line numbers.

    Measured on this repo's own sweep, 2026-10-03: `crates/goh/src/index_view.rs` carries a
    `#[cfg(test)] mod` at its BOTTOM and a `git checkout-index` spawn in production code at the
    top. A file-granular content scope reads the whole file as a test -- it reported that spawn,
    which is the gate's own production path, as a leak. The scope question is `#[cfg(test)]`-REGION
    granular, which is also how the compiler answers it. A `tests/` file has no marker and is
    entirely in scope; with no marker at all this returns the text unchanged.
    """
    lines = masked.split("\n")
    marks = [n for n, line in enumerate(lines) if "cfg(test)" in line.replace(" ", "")]
    if not marks:
        return masked
    inside = [False] * len(lines)
    for at in marks:
        start = next((j for j in range(at, len(lines)) if "{" in lines[j]), len(lines) - 1)
        depth = 0
        for j in range(start, len(lines)):
            inside[j] = True
            depth += lines[j].count("{") - lines[j].count("}")
            if depth <= 0 and j >= start:
                break
    return "\n".join(line if keep else " " * len(line) for line, keep in zip(lines, inside))
