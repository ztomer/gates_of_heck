"""lib/rule_tests.py -- goh's rule tests, found by their shape (GOH.RULESLEFT).

WHY. Lanes learned goh's house rules from red lands: 38 enqueue-check and 71 pre-step reds on
2026-11-22/23, topped by policy tests no `goh land --check` ran -- the from-HEAD stanza, the
git-init ratchet, the clock patch, the env dump -- because a rule entered the check only when its
file declared itself (`# goh-land-guard:`) or after it had refused someone (lib/land_check.py's
learned list). A rule test has a shape: `test_every_*` or `test_no_*`, a plain function of the
module (no decorator: a parametrized one is a table of cases) with no temporary tree or patched
process (`tmp_path`, `tmp_path_factory`, `tmpdir`, `monkeypatch`): it judges the real tree. Every
such function is a rule here, by construction -- a new one is in `goh land --check` the moment it
exists, and no list is kept by hand. A rule of another name declares itself
(`# goh-land-guard: NAME`, lib/land_check.py `declared`).

    rules(root)   the node ids (`tests/test_x.py::test_every_y`) of ROOT's rule tests
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SHAPE = re.compile(r"^test_(?:every|no)_")
TEMP = {"tmp_path", "tmp_path_factory", "tmpdir", "monkeypatch"}


def rules_in(rel: str, text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or not SHAPE.match(fn.name) or fn.decorator_list:
            continue
        if {a.arg for a in fn.args.args} & TEMP:
            continue
        out.append(f"{rel}::{fn.name}")
    return out


def rules(root: Path) -> list[str]:
    out: list[str] = []
    for path in sorted((root / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        out += rules_in(path.relative_to(root).as_posix(), text)
    return out
