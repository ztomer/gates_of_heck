"""lib/test_work.py -- the work each test did, recorded and judged (GOH.TESTWORK, goh.db 0026).

    test_work.py readings [--runs N]      each file's worst per dimension, idle and loaded (goh.db)
    test_work.py seed [--min-runs 2]      today's offenders as `named` entries, from the same
        [--from REPORT...] [--loaded REPORT...]   or from run reports instead, idle and loaded

WHY. A test that starts 400 processes, builds a crate or walks the whole tree costs every run of
the suite that much, and nothing counted it: the file events were held under a ceiling
(tests/_fs_ceilings.py, GOH.FSEVQUIET) and the rest was felt as a slow suite. tests/_test_work.py
counts, per test, in the process that runs it:

    cpu_s         CPU seconds of the test and of the children it waited for
    procs         the processes it started itself (a child's own children are not seen)
    git_repos     of them, `git init` and `git clone`
    cargo_builds  of them, a cargo command that compiles (CARGO_COMPILES)
    tree_walks    walks of the whole tree: a listing of goh's tree (this checkout, or a copy of it,
                  tests/_test_work.py TREE_MARKS) that went on below its top level

and the controller of a run the runner named (tools/pytest.sh's top-level run: the goh.db
tests/_schedule.py took, `named`) writes one `test_work_runs` row and one `test_work` row per test.

THE CEILINGS are tests/test_work_ceiling.json, never a number here:

    new       what a test of no entry is held to (owner 2026-11-25: 1 s CPU, 5 processes, no cargo
              build, no tree walk)
    named     {"tests/f.py" or "tests/f.py::test": {dimension: ceiling, "why": reason}}: a node's
              entry, else its file's, else `new`; a NEW test (tests/test_test_work_new.py, at
              commit) is held to its own node's entry or `new`, never its file's
    headroom  how `seed` sets a ceiling over the most a dimension read
    judged_each_run   the dimensions a run judges its tests on (a NEW test: all of them)
    stale_runs        the whole runs a dimension must read within `new` to be stale

A test that missed a kept store (`cold`) is the store refilling: recorded, never judged. A WHOLE
run also judges each entry: a dimension whose every reading is within `new` (CPU: half of it) in
it and in the `stale_runs` - 1 whole runs goh.db holds before it is stale and red, so the list
only shrinks. Pinned by tests/test_test_work.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import contextlib
import json
import math
import os
import sqlite3
import sys
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/: the one door to goh.db

DIMS = ("cpu_s", "procs", "git_repos", "cargo_builds", "tree_walks")
COUNTS = DIMS[1:]
SHARED = "<shared setup>"  # the run's session and package fixtures: no test's
CONFIG = Path(__file__).resolve().parent.parent / "tests" / "test_work_ceiling.json"
FAULTS = (OSError, sqlite3.Error, goh_db.Uncut)
LOADED = 1.0  # load1 per core above which a run's readings are `loaded` (`readings`)


def config(raw: str | None = None) -> dict:
    """The ceilings: RAW (JSON) when given, else tests/test_work_ceiling.json."""
    return json.loads(raw) if raw else json.loads(CONFIG.read_text(encoding="utf-8"))


def _base(node: str) -> str:
    """NODE without its parameters: an entry names a test function, every case of it."""
    return node.split("[", 1)[0]


def ceiling(node: str, conf: Mapping, new_only: bool = False) -> tuple[dict, str]:
    """(the ceilings NODE is held to, the entry's name or `new`)."""
    named = conf.get("named", {})
    for key in (_base(node), *(() if new_only else (node.split("::")[0],))):
        if key in named:
            return {**conf["new"], **{d: named[key][d] for d in DIMS if d in named[key]}}, key
    return dict(conf["new"]), "new"


def _show(dim: str, n: float) -> str:
    return f"{n:.2f}" if dim == "cpu_s" else str(int(n))


def faults(rows: Mapping[str, Mapping], conf: Mapping, new_only: bool = False) -> list[str]:
    """Each test of ROWS ({node: its counts}) over a ceiling, each line naming the fault and the
    exception form that would let it stand. A NEW test is judged on every dimension; a run's other
    tests on `judged_each_run` (default all), because one CPU reading of a test that stood is no
    verdict: between two idle whole runs 19 files crossed 1 s and one read 0.31 s, then 6.07 s."""
    out = []
    dims = DIMS if new_only else tuple(conf.get("judged_each_run", DIMS))
    for node, row in sorted(rows.items()):
        if node == SHARED or row.get("cold"):
            continue
        limit, where = ceiling(node, conf, new_only)
        for dim in dims:
            if row.get(dim, 0) > limit[dim]:
                key = _base(node) if where == "new" else where
                out.append(
                    f"{node} {dim} {_show(dim, row[dim])}, over {_show(dim, limit[dim])} ({where}):"
                    f" do less, or name it in tests/test_work_ceiling.json `named` as"
                    f' "{key}": {{"{dim}": {_show(dim, row[dim])}, "why": "<its reason>"}}'
                )
    return out


def within_new(dim: str, n: float, new: Mapping) -> bool:
    return 2 * n <= new[dim] if dim == "cpu_s" else n <= new[dim]


def stale(rows: Mapping[str, Mapping], conf: Mapping,
          past: Mapping[str, Mapping[str, list[float]]] | None = None) -> list[str]:  # fmt: skip
    """Each named dimension that a WHOLE run read within `new` for every test it governs, as did
    each of the `stale_runs` - 1 whole runs before it (PAST: {entry: {dimension: its worst in each
    earlier whole run}}, `history`): the entry is no longer needed. One run is no verdict: a file
    whose tests fill a process-wide cache walks the tree in one run and not the next (3 of 22
    tree-walk entries read 0 in each of two whole runs). An entry no test of the run reached, or
    a cold one, says nothing."""
    governs: dict[str, list[Mapping]] = {}
    for node, row in rows.items():
        if node != SHARED:
            governs.setdefault(ceiling(node, conf)[1], []).append(row)
    out, runs, new = [], int(conf.get("stale_runs", 3)), conf["new"]
    for key, entry in sorted(conf.get("named", {}).items()):
        got = governs.get(key, [])
        if not got or any(r.get("cold") for r in got):
            continue
        for dim in (d for d in DIMS if d in entry):
            most = max(r.get(dim, 0) for r in got)
            before = (past or {}).get(key, {}).get(dim, [])
            if not within_new(dim, most, new) or len(before) < runs - 1:
                continue
            if all(within_new(dim, n, new) for n in before):
                out.append(
                    f"{key} is named for {dim} {_show(dim, entry[dim])} and read at most"
                    f" {_show(dim, most)} in {runs} whole runs, within new's"
                    f" {_show(dim, new[dim])}: remove {dim} from its entry in"
                    " tests/test_work_ceiling.json"
                )
    return out


def history(conf: Mapping, db: Path | None = None) -> dict[str, dict[str, list[float]]]:
    """{named entry: {dimension: its worst in each of the newest `stale_runs` - 1 recorded whole
    runs}}; {} when goh.db holds none."""
    runs, out = int(conf.get("stale_runs", 3)) - 1, {}
    try:
        with goh_db.ro(db, db is not None) as con:
            picked = [r for (r,) in con.execute(
                "SELECT run FROM test_work_runs WHERE whole = 1 ORDER BY at DESC LIMIT ?",
                (runs,))]  # fmt: skip
            for run in picked:
                worst: dict[str, dict[str, float]] = {}
                for node, *vals in con.execute(
                    f"SELECT node, {', '.join(DIMS)} FROM test_work WHERE run = ? AND cold = 0",
                    (run,),
                ):
                    key = ceiling(node, conf)[1]
                    if node == SHARED or key == "new":
                        continue
                    w = worst.setdefault(key, {})
                    for dim, v in zip(DIMS, vals, strict=True):
                        w[dim] = max(w.get(dim, 0), v)
                for key, w in worst.items():
                    for dim, v in w.items():
                        out.setdefault(key, {}).setdefault(dim, []).append(v)
    except FAULTS:
        return {}
    return out


def record(run: str, rows: Mapping[str, Mapping], whole: bool, load1: float | None,
           load_end: float | None, db: Path | None = None, why: str = "",
           cores: int | None = None) -> bool:  # fmt: skip
    """RUN's rows in ONE transaction; False (one stderr line) when they cannot be written: the
    instrument never fails the run."""
    now = time.time()
    try:
        with goh_db.transaction(db, db is not None) as con:
            con.execute(
                "INSERT OR REPLACE INTO test_work_runs(run, whole, tests, load1, load_end, cores,"
                " why, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run, int(whole), sum(n != SHARED for n in rows), load1, load_end, cores,
                 why or None, now),
            )  # fmt: skip
            con.executemany(
                "INSERT OR REPLACE INTO test_work(run, node, cpu_s, procs, git_repos,"
                " cargo_builds, tree_walks, cold) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(run, node, round(max(0.0, float(r.get("cpu_s", 0))), 3),
                  *(int(r.get(d, 0)) for d in COUNTS), int(bool(r.get("cold"))))
                 for node, r in rows.items()],
            )  # fmt: skip
    except FAULTS as e:
        with contextlib.suppress(OSError):
            sys.stderr.write(f"test_work: run {run} not recorded ({e})\n")
        return False
    return True


def loaded(load: float | None, cores: int | None) -> bool:
    """Whether a run that read LOAD (load1, either end) on CORES was loaded: over LOADED a core."""
    return (load or 0.0) / (cores or 1) > LOADED


def fold(runs: Iterable[tuple[bool, Mapping[str, Mapping]]]) -> dict[str, dict]:
    """{file: {"idle": {dim: worst}, "loaded": {dim: worst}, "runs": {"idle": n, "loaded": n}}}
    of RUNS, each (loaded, {node: its counts}); the shared setup left out. A COLD row is read too:
    it is never judged, but the next run may be warm and is judged, and a ceiling set from warm
    rows alone missed 2 tests that read cold in every seeding run (a cargo build, 17 processes)
    and refused a land's pre-step (2026-11-25)."""
    out: dict[str, dict] = {}
    for is_loaded, rows in runs:
        kind, seen = "loaded" if is_loaded else "idle", set()
        for node, row in rows.items():
            if node == SHARED:
                continue
            f = node.split("::")[0]
            got = out.setdefault(f, {"idle": {}, "loaded": {}, "runs": {"idle": 0, "loaded": 0}})
            if f not in seen:
                got["runs"][kind] += 1
                seen.add(f)
            for dim in DIMS:
                got[kind][dim] = max(got[kind].get(dim, 0), row.get(dim, 0))
    return out


def readings(runs: int = 10, db: Path | None = None) -> dict[str, dict]:
    """`fold` of the newest RUNS whole runs recorded in goh.db."""
    picked = []
    with goh_db.ro(db, db is not None) as con:
        for run, load, cores in con.execute(
            "SELECT run, max(COALESCE(load1, 0), COALESCE(load_end, 0)), cores FROM test_work_runs"
            " WHERE whole = 1 ORDER BY at DESC LIMIT ?", (runs,)
        ).fetchall():  # fmt: skip
            rows = {node: dict(zip(DIMS, vals, strict=True)) | {"cold": cold}
                    for node, *vals, cold in con.execute(
                        f"SELECT node, {', '.join(DIMS)}, cold FROM test_work WHERE run = ?",
                        (run,))}  # fmt: skip
            picked.append((loaded(load, cores), rows))
    return fold(picked)


def reports(idle: Iterable[str], busy: Iterable[str] = ()) -> dict[str, dict]:
    """`fold` of the run reports tests/_test_work.py wrote (GATES_OF_HECK_TEST_WORK_OUT): IDLE's
    as idle readings, BUSY's (a run under tools/load.py's spinners) as loaded ones."""
    runs = []
    for paths, is_loaded in ((idle, False), (busy, True)):
        for p in paths:
            runs.append((is_loaded, json.loads(Path(p).read_text(encoding="utf-8"))["rows"]))
    return fold(runs)


def seed(read: Mapping[str, Mapping], conf: Mapping, min_runs: int = 2) -> dict[str, dict]:
    """The `named` entries of today's offenders: each file whose worst reading, idle or loaded,
    is over `new`, its ceiling that worst times the headroom; only files read in MIN_RUNS runs."""
    room, new, out = conf.get("headroom", {}), conf["new"], {}
    for f, r in sorted(read.items()):
        if sum(r["runs"].values()) < min_runs:
            continue
        entry: dict[str, object] = {}
        for dim in DIMS:
            most = max(r["idle"].get(dim, 0), r["loaded"].get(dim, 0))
            if most <= new[dim]:
                continue
            if dim == "cpu_s":
                entry[dim] = max(new[dim] + 1, math.ceil(most * room.get("cpu_s", 2.0) * 2) / 2)
            else:
                entry[dim] = max(new[dim] + 1, math.ceil(most * room.get("count", 1.5)))
        if entry:
            entry["why"] = "unreviewed: stood before the rule (GOH.TESTWORK)"
            out[f] = entry
    return out


def _main(argv: Iterable[str]) -> int:
    ap = argparse.ArgumentParser(prog="test_work.py")
    ap.add_argument("what", choices=("readings", "seed"))
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--min-runs", type=int, default=2)
    ap.add_argument("--from", dest="idle", nargs="*", default=[], help="idle run reports")
    ap.add_argument("--loaded", nargs="*", default=[], help="run reports taken under load.py")
    a = ap.parse_args(list(argv))
    read = reports(a.idle, a.loaded) if a.idle or a.loaded else readings(a.runs)
    if a.what == "readings":
        print(json.dumps(read, indent=1, sort_keys=True))
    else:
        print(json.dumps(seed(read, config(), a.min_runs), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
