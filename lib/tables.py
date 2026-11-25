"""lib/tables.py -- goh's mechanical tables, written from the tree (GOH.TABLEGEN).

    python3 lib/tables.py --check [ROOT]   # exit 1 naming each table not as written
    python3 lib/tables.py --write [ROOT]   # rewrite each stale table
    python3 lib/tables.py --list           # each table, generated or why not

WHY. A rule that only asks a lane to copy a fact the code already holds is a red a lane can miss,
and lanes did, one per land (2026-11-17..24, goh.db `lands`): a lock_order.tsv row naming a file
that no longer takes the lock, a 450-line headroom entry no longer over the line, a base file with
no row in tests/base_files.tsv (4). Each such table, or its
mechanical part, is written here; tests/test_tables.py holds every committed copy to what this
writes, so the fix for every one of those reds is `--write`.

THE TABLES.
  lib/lock_order.tsv            the files column: exactly the files checks/check_lock_order.py
                                finds taking each lock (rank, scope and the prose are a design).
  tests/cap_headroom_allow.txt  its stale direction: an entry no longer over 450 lines leaves. A
                                file newly over is the rule's own refusal (split it), never a row.
  tests/base_files.tsv          a row per file pytest loads before every test, in the class its
                                rule computes (tests/test_base_table.py).
The script map is no table here: `goh map` renders it on demand (lib/map_table.py, GOH.MAPGEN).
NOT_GENERATED names every other table a rule demands, with why.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]

HEADROOM = 450
FIX = "python3 lib/tables.py --write"

NOT_GENERATED = {
    "gates/verdict_free_keys.txt": "each key is a judgement, verdict-free or keyed with a reason "
    "(tests/test_verdict_free_keys.py): a key defaulted to free leaves a verdict out of its "
    "cache key",
    "tests/linux_safe.txt": "generated already, by `lib/remote_lane.py record` from a Linux and a "
    "Mac run's JUnit: only a remote run on both hosts can write it",
    "gates/undeclared_reads.txt": "a shrink-only ratchet whose new entry is the rule's own "
    "refusal (declare the test's reads); an entry gone stale is named by test_declared_reads.py",
    "gates/undeclared_scanners.txt": "a shrink-only ratchet like undeclared_reads.txt: a new pair "
    "is the rule's refusal (the scanner declares TREE_READS), never a row to copy",
    "tests/shard_weights.json": "a measurement (the suite's own timings), rewritten by the run "
    "that measures it, not a copy of a fact the code holds",
}


@dataclass(frozen=True)
class Table:
    name: str
    path: str
    render: Callable[[Path, str], str]  # (root, the committed text) -> the text it should be


def lock_rows(text: str, holders: dict[str, set[str]]) -> str:
    """The lock table with each lock's files exactly those HOLDERS say take it; the hand order of
    the files still taking it kept, new ones appended sorted. A lock nothing takes keeps its row:
    the lock check refuses it, and deleting a lock is a design decision."""
    out = []
    for line in text.split("\n"):
        if not line.strip() or line.startswith("#"):
            out.append(line)
            continue
        cols = line.split("\t")
        taken = {rel for rel, names in holders.items() if cols[0] in names}
        if taken:
            kept = [f for f in cols[4].split(",") if f in taken]
            cols[4] = ",".join(kept + sorted(taken - set(kept)))
        out.append("\t".join(cols))
    return "\n".join(out)


def _lock_render(root: Path, text: str) -> str:
    sys.path[:0] = [str(root / "checks")]
    try:
        import check_lock_order as clo
    finally:
        del sys.path[0]
    tree = clo.load(root)
    clo.scan(tree)
    return lock_rows(text, {rel: {n for _, n in sites} for rel, sites in tree.sites.items()})


def _tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                         text=True, check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    return [p for p in out.split("\0") if p]


def over_headroom(root: Path = ROOT) -> set[str]:
    """Every tracked source file over HEADROOM lines, by the cap's own suffixes (length.rs)."""
    src = (root / "crates" / "goh" / "src" / "length.rs").read_text(encoding="utf-8")
    measured = []
    for name in ("SOURCE_SUFFIXES", "SOURCE_NAMES"):
        body = re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", src, re.DOTALL)
        if not body:
            raise ValueError(f"length.rs no longer declares {name}")
        measured.append(re.findall(r'"([^"]*)"', body.group(1)))
    suffixes, names = measured
    over = set()
    for rel in _tracked(root):
        if not (rel.endswith(tuple(suffixes)) or rel.rsplit("/", 1)[-1] in names):
            continue
        path = root / rel
        if (
            path.is_file()
            and len(path.read_bytes().decode("utf-8", "replace").splitlines()) > HEADROOM
        ):
            over.add(rel)
    return over


def headroom_rows(text: str, over: set[str]) -> str:
    return "\n".join(ln for ln in text.split("\n")
                     if not ln.strip() or ln.startswith("#") or ln.strip() in over)  # fmt: skip


def base_rows(text: str, base: set[str], loads: dict[str, str], entry_reads: Iterable[str]) -> str:
    """One row per base file, in the class tests/test_base_table.py's rule computes."""
    sys.path[:0] = [str(ROOT / "tests")]
    try:
        import _carry_shadow as cs
    finally:
        del sys.path[0]
    head = [ln for ln in text.split("\n") if ln.startswith("#")]
    entry = set(entry_reads)

    def klass(path: str) -> str:
        if path in cs.SUITE_WIDE or Path(path).name == "conftest.py" or path in entry:
            return cs.SUITE
        return cs.LOAD if path in loads else cs.BASE_CODE

    return "\n".join([*head, *(f"{p}\t{klass(p)}" for p in sorted(base))]) + "\n"


def _base_render(root: Path, text: str, graph: object | None = None) -> str:
    from lib import test_closure

    graph = graph or test_closure.Closure(root)
    return base_rows(text, graph.base(), graph.load_reads(), test_closure.ENTRY_READS)  # type: ignore[attr-defined]


TABLES = (
    Table("lock files", "lib/lock_order.tsv", _lock_render),
    Table(
        "headroom", "tests/cap_headroom_allow.txt", lambda r, t: headroom_rows(t, over_headroom(r))
    ),
    Table("base files", "tests/base_files.tsv", _base_render),
)


def check(root: Path, tables: Iterable[Table] = TABLES) -> list[str]:
    out = []
    for t in tables:
        text = (root / t.path).read_text(encoding="utf-8")
        if t.render(root, text) != text:
            out.append(f"{t.path}: not as lib/tables.py writes it ({t.name}); run `{FIX}`")
    return out


def write(root: Path, tables: Iterable[Table] = TABLES) -> list[str]:
    done = []
    for t in tables:
        path = root / t.path
        text = path.read_text(encoding="utf-8")
        new = t.render(root, text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            done.append(t.path)
    return done


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    from lib import goh_root  # an export judges the checkout it runs for (gates/_from_head.py)

    root = Path(args[0]).resolve() if args else Path(goh_root.live_root_for(str(ROOT)) or ROOT)
    if "--list" in argv:
        for t in TABLES:
            print(f"{t.path}\tgenerated: {t.name}")
        for path, why in NOT_GENERATED.items():
            print(f"{path}\tnot generated: {why}")
        return 0
    if "--write" in argv:
        for path in write(root):
            print(f"wrote {path}")
    found = check(root)
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
