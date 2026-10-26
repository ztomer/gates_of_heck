"""E59: no writer in this repo writes a report in two writes.

THE CLASS. A writer that prints a header and then its details in two writes leaves a state
that is not final, and a reader can land in it: the gate-slot wait report was two writes, the
window between them was 56 us, and a reader polling every millisecond read a half-written
report as final (1 of 20 trials against the real writer, 20 of 20 against the instrumented
one).

WHY A TEST-SIDE SCANNER CANNOT GATE IT (E39: written, measured and DELETED). The soundness
of a wait depends on the writer's write ORDER, which a scanner reading the TEST cannot know:
"wait for X, then assert Y" is safe only if the writer writes Y after X, and the rule flagged
two SAFE sites in this suite. A machine that cries wolf is worse than no machine.

WHY A WRITER-SIDE SCANNER CAN. The order is in the WRITER, and the house's own vocabulary
settles what the shape is: `step` is "a sub-item under an info line" (tui/lib.py's
docstring). So an info write immediately followed by a step write IS a report -- a header and
its details -- written in two pieces. That is visible in the source, and decidable.

WHAT THIS IS, PRECISELY. A RATCHET over this repo, not a proof. It sees an info write
immediately followed by a step write (adjacent statements in the python AST, adjacent lines in
the shell), not every way a report can be split: a write through a helper, two writes with a
statement between them, or a writer in a language this scanner does not read are all beyond
it. The GUARANTEE is the primitive -- one write, so the intermediate state does not exist --
plus the instrumented writer (tests/test_gate_slot_race.py), which proves the state is gone
for the writer that had the race.

TWO SITES were found when this ratchet landed, and both were converted (lib/tree_lock.sh,
lib/desktop_lock/desktop_lock.sh). The conversion is byte-identical: `report` renders what
`info` and `step` rendered, pinned by tests/test_tui_report.py.
"""

from __future__ import annotations

import ast
import itertools
import re
from pathlib import Path

from conftest import REPO_ROOT

# A shell `info "..."` / `step "..."` line. The function DEFINITIONS in tui/lib.sh are
# `info()  { ... }` -- a paren, not a space -- so the primitive itself is not a finding.
SHELL_INFO = re.compile(r"^\s*info\s+")
SHELL_STEP = re.compile(r"^\s*step\s+")

# Build output and vendored copies are not writers in this repo.
SKIP = {".git", "target", "bin", "__pycache__", ".venv"}


def _bodies(node: ast.AST):
    """Every statement list in the tree: a module, a function, a block."""
    body = getattr(node, "body", None)
    if isinstance(body, list) and body and isinstance(body[0], ast.stmt):
        yield body


def _called(stmt: ast.stmt) -> str | None:
    """The name a statement calls, when it is a bare call statement."""
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        if isinstance(func, ast.Name):
            return func.id
    return None


def _python_findings(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        for body in _bodies(node):
            for first, second in itertools.pairwise(body):
                if _called(first) == "info" and _called(second) == "step":
                    out.append(f"{path}:{first.lineno}")
    return out


def _shell_findings(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[str] = []
    for i, line in enumerate(lines):
        if SHELL_INFO.match(line) and i + 1 < len(lines) and SHELL_STEP.match(lines[i + 1]):
            out.append(f"{path}:{i + 1}")
    return out


def findings(paths: list[Path]) -> list[str]:
    """Every writer in `paths` that writes a report in two pieces."""
    out: list[str] = []
    for path in paths:
        if path.suffix == ".py":
            out += _python_findings(path)
        elif path.suffix == ".sh":
            out += _shell_findings(path)
    return out


def _sources() -> list[Path]:
    out: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in (".py", ".sh"):
            continue
        if any(part in SKIP for part in path.parts):
            continue
        if "fixtures" in path.parts:
            continue  # a fixture stands in for another repo's file, and may not have the TUI
        out.append(path)
    return out


def test_no_writer_in_this_repo_writes_a_report_in_two_writes():
    found = findings(_sources())
    assert not found, (
        "a report written in pieces can be READ in pieces, and a reader cannot know the"
        " writer's write order -- use `report` (tui/lib.py, tui/lib.sh), which writes the"
        " header and its details in ONE write:\n  " + "\n  ".join(found)
    )


def test_the_ratchet_bites(tmp_path):
    """The instrument has to be shown a violation, or it is a rule that cannot fail."""
    py = tmp_path / "writer.py"
    py.write_text('def w():\n    info("header")\n    step("detail")\n', encoding="utf-8")
    sh = tmp_path / "writer.sh"
    sh.write_text('info "header"\nstep "detail"\n', encoding="utf-8")
    found = findings([py, sh])
    assert len(found) == 2, f"the ratchet did not see the violations it must see: {found}"
