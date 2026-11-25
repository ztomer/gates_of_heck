"""No code reads or writes a document of a converted table of goh.db (GOH.OLDFORMS): the SQL that
names `body` beside one of them is the cutover's alone, which moves an older file off them."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import state_columns
from conftest import REPO_ROOT

TABLES = {*state_columns.TABLES, "commit_gated", "red_causes", "land_guards"}
NAMES = re.compile(rf"\b(?:{'|'.join(sorted(TABLES))})\b")
SQL = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|REPLACE|FROM|JOIN)\b")
BODY = re.compile(r"\bbody\b")
OTHER = re.compile(r"\b(?:kv|lands_v1|ships_v2)\b")  # `kv` keeps its documents, a view renders one
# What may name them, and why: each moves an older file off its documents, or compares the two.
CUTOVER = {
    "lib/queue_read.py": "another database: the loop's queue, by its own layout",
    "lib/state_backfill.py": "the cutover fills a row's columns from the document it has",
    "lib/state_columns.py": "goh state check compares a row to the document it still has",
    "lib/state_cutover.py": "the cutover: the red causes of a land with no document",
    "lib/state_strip.py": "the cutover drops a document its columns give back",
}
ROOTS = ("lib", "tools", "hooks", "scripts", "crates")


def literals(text: str) -> list[tuple[int, str]]:
    """(line, text) of every string of a Python source, an f-string's parts as one."""
    out, inner = [], set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.JoinedStr):
            parts = [v for v in ast.walk(node) if isinstance(v, ast.Constant)]
            inner |= {id(v) for v in parts}
            out.append((node.lineno, "{}".join(str(v.value) for v in parts)))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value if id(node) not in inner else ""))
    return out


def named(path: str, text: str) -> list[str]:
    """`path:line` of each string that is SQL over a converted table's `body`. A statement whose
    table is a placeholder counts: the table is not known not to be one."""
    if path.endswith(".rs"):
        found = [(text.count("\n", 0, m.start()) + 1, m[0]) for m in re.finditer(
            r'"(?:[^"\\]|\\.)*"', text)]  # fmt: skip
    else:
        found = literals(text)
    doc = ast.get_docstring(ast.parse(text), clean=False) if path.endswith(".py") else None
    return [f"{path}:{n}" for n, s in found
            if s != doc and BODY.search(s) and SQL.search(s) and not OTHER.search(s)
            and (NAMES.search(s) or re.search(r"\b(?:INTO|FROM|UPDATE) \{\}", s))]  # fmt: skip


def sources() -> list[Path]:
    return [p for r in ROOTS for e in ("*.py", "*.rs") for p in sorted((REPO_ROOT / r).rglob(e))
            if "target" not in p.parts]  # fmt: skip


def test_the_cutover_alone_names_a_document() -> None:
    texts = {str(p.relative_to(REPO_ROOT)): p.read_text(encoding="utf-8") for p in sources()}
    hits = [h for p, text in texts.items() for h in named(p, text)]
    assert [h for h in hits if h.partition(":")[0] not in CUTOVER] == []
    assert {h.partition(":")[0] for h in hits} == set(CUTOVER), "an allowed file names none"


@pytest.mark.parametrize(
    ("path", "code", "red"),
    [
        ("a.py", 'con.execute("SELECT body FROM lands")', True),
        ("a.py", 'con.execute(f"UPDATE {t} SET body = ?")', True),
        ("a.py", 'con.execute("INSERT INTO flakes(epoch, body) " "VALUES (?, ?)")', True),
        ("a.py", "x = 'SELECT l.body ->> \"$.at\" FROM ships l'", True),
        ("a.rs", 'q("SELECT body FROM agent_spawns WHERE at > ?1")', True),
        ("a.py", 'con.execute("SELECT body FROM kv WHERE name = ?")', False),
        ("a.py", 'con.execute("SELECT body FROM ships_v2")', False),
        ("a.py", 'body = con.execute("SELECT rest FROM lands")', False),
        ("a.py", '"""A land has no body. SELECT it from lands."""', False),
    ],
)
def test_what_it_names(path: str, code: str, red: bool) -> None:
    assert bool(named(path, code)) is red
