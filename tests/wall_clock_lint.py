"""The wall-clock shapes a test may not write unless the load-scaled helper carries them.

GOH.CLASS.HARNESS (2026-11-15). About nineteen flake items in a month were one shape: a fixed wall
bound in a test -- `wait(timeout=10)`, `run(..., timeout=3)`, `sleep(0.5); assert ...` -- written
on a quiet box and red at load 110-180, refusing lands for reasons unrelated to the change
(GOH.BOUNDEDRUNFLAKE: a 10 s `wrapper.wait` starved at load ~200). The one helper is
`timing_bounds.patience(seconds)` (a give-up stretched by the box's load), with `GiveUp` for a
polled wait and `assert_sooner` for an upper bound on an elapsed time
(tests/test_timing_bounds.py refuses the literal elapsed-time asserts).

What this READS, in every tests/**/*.py but the fixture subjects under tests/fixtures/:

  * `timeout=` given a number: a literal, arithmetic over literals, or a module-level name bound to
    one (`T = 10` then `timeout=T` is the same literal, renamed);
  * a number handed positionally to `.wait(...)` or `.join(...)` (Popen.wait, Event.wait,
    Thread.join) -- `str.join` never takes a number;
  * `time.sleep(<number>)` whose NEXT statement is an `assert`: a sleep standing for a state.

What it does NOT read, so a reader knows the hole: a timeout passed as a STRING argument to a CLI
under test (`"--timeout", "60"`), a number reaching `timeout=` through a function argument or an
attribute, and a sleep followed by anything but an assert.
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path


def _numeric(node: ast.AST, consts: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp):
        return _numeric(node.operand, consts)
    if isinstance(node, ast.BinOp):
        return _numeric(node.left, consts) and _numeric(node.right, consts)
    if isinstance(node, ast.Name):
        return node.id in consts
    return False


def _module_consts(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for stmt in tree.body:
        targets: list[ast.expr] = []
        value = None
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        if value is not None and _numeric(value, out):
            for t in targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


def _is_sleep(stmt: ast.stmt, consts: set[str]) -> bool:
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    call = stmt.value
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    return name == "sleep" and len(call.args) == 1 and _numeric(call.args[0], consts)


def findings(source: str) -> list[tuple[int, str]]:
    """(line, shape) for each wall-clock literal in `source`: timeout, wait or sleep_assert."""
    tree = ast.parse(source)
    consts = _module_consts(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "timeout" and _numeric(kw.value, consts):
                    found.append((kw.value.lineno, "timeout"))
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr in ("wait", "join")
                and node.args
                and _numeric(node.args[-1], consts)
            ):
                found.append((node.lineno, "wait"))
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if not isinstance(body, list):
                continue
            for a, b in itertools.pairwise(body):
                if _is_sleep(a, consts) and isinstance(b, ast.Assert):
                    found.append((a.lineno, "sleep_assert"))
    return sorted(found)


def census(tests: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(tests.rglob("*.py")):
        rel = path.relative_to(tests).as_posix()
        if rel.startswith("fixtures/"):
            continue
        n = len(findings(path.read_text(encoding="utf-8")))
        if n:
            out[rel] = n
    return out
