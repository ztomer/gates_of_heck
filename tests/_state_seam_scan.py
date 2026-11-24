"""The scanner behind tests/test_state_seam.py (GOH.DBSEAM): every place goh's code names a state
store that moves into goh.db or goh-cache.db (handover plan (b)).

A store is named by a PATH SEGMENT: a string joined into a path (`root / "done"`,
`os.path.join(q, "pending")`, `.with_name("remote-rates.json")`), or a literal path whose segments
include it (`"landq/done"`), or a store's FILE name in a shell line (`$STATE/agent-spawns.jsonl`).
A bare "done" that is a status, or a shell's `$LOGDIR/done` FIFO, is no store. Python is read by its
AST; shell by its uncommented lines; Rust (crates/) only for the shell-lint verdict store.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

DIRS = ("lib", "scripts", "tools", "hooks", "checks", "gates", "bin", "tests")
# tests/: only its helper modules (`_name.py`), which ARE store code (the kept stores, the
# duration table's one writer); a test's fixtures write stores on purpose.
HELPER = re.compile(r"^tests/_[^/]*\.py$")
# The seam itself: the only code that opens the databases.
SEAM = {
    "lib/db_core.py",
    "lib/db_bounds.py",
    "lib/goh_db.py",
    "lib/goh_cache_db.py",
    "lib/state_retire.py",  # `goh state retire`: the moved stores' files, archived and deleted
    "lib/state_retire_ships.py",  # its ship records' half (GOH.STATEDB S2)
    "lib/state_retire_verdicts.py",  # ... GOH.VERDICTDB's stores, split out at its line cap
    "lib/state_retire_lands.py",  # its land records' half (GOH.STATEDB S1)
    "lib/state_retire_landq.py",  # its land queue requests' half (GOH.LANDQDB)
    "tests/_state_seam_scan.py",
}  # and this scanner, whose table names every store

# store -> (its lane and table, the path segments that name it)
STORES: dict[str, tuple[str, tuple[str, ...]]] = {
    "land records": ("S1 goh.db 0002 lands", ("done", "guards.jsonl", "red_causes.json")),
    "selection misses": ("S1 goh.db 0002 selection_misses", ("selection_misses.jsonl",)),
    "ship records": ("S2 goh.db 0003 ships", ("ship",)),
    "ship pins": ("S2 goh.db 0003 ship_pins", (".pins.json",)),
    "small stores": (
        "S3 goh.db 0004 kv",
        (
            "cache-evict.json",
            "pin_push.json",
            "pin_sweeps.json",
            "wf_pin.pins.json",
            "estate_audit.json",
        ),
    ),
    "commit gated": ("S3 goh.db 0004 commit_gated", ("commit-gated.jsonl",)),
    "flakes": ("S3 goh.db 0004 flakes", ("flakes",)),
    "hook ledgers": (
        "S4 goh.db 0005",
        (
            "agent-spawns.jsonl",
            "lane-starts.jsonl",
            "fault-candidates.jsonl",
            "model-choice-watch.json",
        ),
    ),
    "durations": (
        "DUR goh.db 0006 readings",
        ("test-durations.json", "test-durations", "remote-rates.json", "linux-unsafe.json"),
    ),
    "reuse records": ("V1 goh-cache.db 0002 verdicts", ("reuse",)),
    "reuse audit": ("V1 goh.db 0008 reuse_audit", ("audit",)),
    "kept stores": (
        "V1 goh-cache.db 0002 kept",
        ("self-walk-verdicts", "parse-findings", "tree-lint-verdicts"),
    ),
    "identity memo": ("V1 goh-cache.db 0002 memo", ("identity",)),
    "cpu idle": ("S3 goh-cache.db kv", ("cpu_idle.json",)),
    "land queue": ("LANDQ goh.db 0007 requests", ("pending", "running", "enqueuing", "early")),
    "shell verdicts": ("V2 goh-cache.db 0003 shell_verdicts", ("verdicts",)),
}
SEGMENT = {seg: store for store, (_, segs) in STORES.items() for seg in segs}
JOINS = {
    "join",
    "joinpath",
    "with_name",
    "with_suffix",
    "Path",
    "PurePath",
    "kept",
}  # kept: _kept_store
SHELL = re.compile(r"""/([A-Za-z0-9_.-]+)(?=["'/\s}]|$)""")
RUST = re.compile(r"""goh/verdicts|"verdicts\"""")


def _segments(text: str) -> list[str]:
    return [s for s in re.split(r"[/{}$]", text) if s]


def _store_of(text: str, joined: bool) -> set[str]:
    """The stores TEXT names: as a whole segment when it is JOINED into a path, or as one segment
    of a literal path ("a/b"), never as a bare word."""
    out = set()
    if joined and text in SEGMENT:
        out.add(SEGMENT[text])
    if "/" in text:
        out |= {SEGMENT[s] for s in _segments(text) if s in SEGMENT}
    for seg, store in SEGMENT.items():
        if seg.startswith(".") and text.endswith(seg) and len(text) > len(seg):
            out.add(store)  # a suffix store: <name>.pins.json
    return out


def _strings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return ["".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)]
    return []


def python_hits(rel: str, text: str) -> list[tuple[str, int, str]]:
    """(store, line, literal) for each store a Python file names."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    joined: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            joined |= {id(n.left), id(n.right)}
        elif isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in JOINS:
                joined |= {id(a) for a in n.args}
    prose = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}  # docstrings
    out = []
    for n in ast.walk(tree):
        if id(n) in prose:
            continue
        for s in _strings(n):
            if re.search(r"\s", s):
                continue  # prose or a message, never a path goh opens
            out += [(store, n.lineno, s) for store in sorted(_store_of(s, id(n) in joined))]
    return out


def shell_hits(text: str) -> list[tuple[str, int, str]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split(" #", 1)[0] if not line.lstrip().startswith("#") else ""
        for seg in SHELL.findall(code):
            # A bare word (`$LOGDIR/done`, a FIFO) is no store in a shell line: only a file name.
            if seg in SEGMENT and re.search(r"[.-]", seg):
                out.append((SEGMENT[seg], i, seg))
    return out


def scan(root: Path) -> list[tuple[str, str, int, str]]:
    """(file, store, line, literal) for every store named outside the seam."""
    out = []
    for d in DIRS:
        for p in sorted((root / d).rglob("*")):
            rel = p.relative_to(root).as_posix()
            if not p.is_file() or rel in SEAM or "__pycache__" in rel:
                continue
            if rel.startswith("tests/") and not HELPER.match(rel):
                continue
            if p.suffix == ".py":
                hits = python_hits(rel, p.read_text(encoding="utf-8", errors="replace"))
            elif p.suffix in ("", ".sh") and p.read_bytes()[:2] != b"\x7fE":
                hits = shell_hits(p.read_text(encoding="utf-8", errors="replace"))
            else:
                continue
            out += [(rel, store, line, lit) for store, line, lit in hits]
    for p in sorted((root / "crates").rglob("*.rs")):
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if RUST.search(line) and not line.lstrip().startswith("//"):
                out.append((p.relative_to(root).as_posix(), "shell verdicts", i, line.strip()))
    return out
