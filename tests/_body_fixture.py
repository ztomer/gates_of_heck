"""Documents for the tests of lib/body_columns.py (GOH.BODYCOLS): one of each kind with EVERY key
goh's code writes, as its writer leaves it, and goh.db's schema in memory.

    DOCS                   {table name: [whole document, ...]}
    OWN                    {table name: the table's own columns for its first document}
    named(t, doc, n)       DOC as row N's document
    memory(upto)           goh.db as it is made; UPTO: an older goh's, its schema files numbered up
                           to it, uncut (GOH.OLDFORMS)
    old(con, t, doc, own)  DOC as a copy that knows no columns writes it, into an uncut file
    held(con, t)           every row of T and its child rows, numbers left out
"""

from __future__ import annotations

import contextlib
import functools
import json
import sqlite3

import body_columns
import db_core
import db_schema
import goh_db
from body_tables import Table

AT, STAMP = 1_795_000_000.0, "2026-11-18T11:06:40+00:00"
HOLDER = {"repo": "r", "slot": "slot-0", "weight_from": "r", "since": 9.5, "weight": 1.5, "pid": 7}
READING = {
    "v": 1, "at": "2026-11-18T11:06:39+00:00", "unread": [],
    "disk": {"path": "/d", "free_gib": 10.5}, "load": {"avg": [1.5, 2.5, 3.5], "cores": 12,
                                                         "per_core": 0.2},
    "cpu": {"source": "rolling", "idle_pct": 50.5, "window_s": 60.0, "samples": 4},
    "memory": {"source": "vm", "avail_pct": 40, "swap_used_mb": 1, "total_mb": 2, "low": None},
    "cpu_weight": {"in_flight": 1.5, "per_core": 0.1},
    "slots": {"dir": "/s", "free": 1, "held": 1, "total": 2,
              "holders": [HOLDER, {**HOLDER, "pid": None, "since": None}]},
    "waiting": {"long": 1, "short": 2, "pre": 3},
    "presteps": {"total": 2, "held": 1, "in_flight": 1, "cap": {"value": 3, "from": "curve"},
                 "holders": [HOLDER], "live": [{"repo": "r", "n": 1, "pid": 8, "since": 1.5}],
                 "reaped": [{"repo": "r", "n": 2, "pid": 9}]},
    "room": {"ok": False, "why": ["full"], "settings": {
        "GOH_LANE_LOAD_PER_CORE": 2.0, "GOH_LANE_MAX_WAITING": 3,
        "GOH_LANE_MIN_FREE_GIB": 20.0, "GOH_LANE_MIN_IDLE_PCT": 25.0}},
}  # fmt: skip
LANE = {"v": 1, "at": STAMP, "session_id": "S", "tool_use_id": "T", "subagent_type": "x",
        "declared": "opus@high", "mode": "refuse", "verdict": "refused", "item": "A.B",
        "owner": "go", "why": ["full"], "reading": READING}  # fmt: skip
LOW = {**LANE, "item": None, "owner": None, "session_id": None, "why": ["low"], "reading": {
    **READING, "unread": ["disk"],
    "cpu_weight": {"in_flight": 0, "per_core": 0.0}, "memory": {**READING["memory"], "low": "low"},
    "room": {**READING["room"], "why": ["low"], "ok": True, "unread": True}}}  # fmt: skip
BROKEN = {**LANE, "why": [], "reading": {"error": "OSError()"}}
FLAKE = {"id": "k.1", "test": "t::a", "repo": "/r/goh", "sha": "s", "load": 1.5, "first": "red",
         "second": "green", "log": {"first": "/l/1", "second": "/l/2"}, "site": "x",
         "at": STAMP, "epoch": AT}  # fmt: skip
BASE = {"source": "land", "item": "A.B", "sha": "s", "day": "2026-11-18"}
JUDGED = {"kind": "judged", **BASE, "changed": ["a.py", "b.py"], "selected": 3, "failed": 2,
          "missed": 1, "steps": 1, "steps_missed": 0}  # fmt: skip
MISS = {"kind": "miss", **BASE, "test": "t::a", "selected": 3}
STEP = {"kind": "step", **BASE, "step": "./g.sh", "script": "g.sh", "pre_ran": True,
        "miss": False, "changed": []}  # fmt: skip
NAMED = ("base", "bin", "checker", "closure", "config", "corpus", "entry", "env", "identity", "key",
         "node", "remote", "repo", "rust_identity", "selection", "sha", "step", "tools",
         "tree")  # fmt: skip
SAMPLE = {"path": "carry", "key": "k", "fields": {f: f"f-{f}" for f in NAMED}, "reused": "green",
          "fresh": "red", "epoch": 1_795_000_000, "fresh_from": "x",
          "again": {"reused": "green", "fresh": "red"}, "repeated": True}  # fmt: skip
WINDOW = {"start": 1, "uses": 2, "sampled": 3, "compared": 4, "agreed": 5, "disagreed": 6,
          "flakes": 7}  # fmt: skip
OFF = {"path": "carry", "state": "off", "cause": "disagree", "why": "w", "since": 5, "fault": "f",
       "evidence": SAMPLE, "window": {**WINDOW, "unbacked": 8},
       "closed": [WINDOW, {**WINDOW, "unbacked": 1}]}  # fmt: skip
ON = {"path": "carry", "state": "on", "window": WINDOW, "closed": [], "enabled": {
    "epoch": 9, "fix": "s", "test": "t::a", "why": "", "was": {
        "cause": "disagree", "why": "w", "since": 5, "fault": "f",
        "evidence": SAMPLE}}}  # fmt: skip
STARVED = {**OFF, "cause": "starved", "evidence": {}}
PUSH = {"repo": "/r/goh", "sha": "s1", "remote": "origin", "branch": "main", "item": "",
        "mode": "ship", "summary": "say", "enqueued_at": "2026-11-18T11:06:40Z"}  # fmt: skip
REMOTE = {"outcome": "ran", "cause": "c", "where": "w", "host": "h", "worker_s": 1.5, "mac_s": 2.5,
          "mac_est_s": 3.5, "rerun_s": 4.5, "saved_s": 5.5, "t0": 6.5, "tests": 7,
          "fresh": True}  # fmt: skip
LAND = {"repo": "/r/goh", "worktree": "/q/slots/0", "sha": "s1", "remote": "", "branch": "lane-x",
        "item": "A.B", "mode": "land", "summary": "", "enqueued_at": "2026-11-18T11:06:40Z",
        "lane": "/w", "slot": "tok", "slot_s": 1.5, "red_last": "r", "no_pre": "why",
        "pre_s": 2.5, "pre_carry": "c1", "pre": {"judged": "j", "base": "b", "keep": "/q/pre/k"},
        "pre_run": {"wait_s": 1.5, "run_s": 2.5, "limit_s": 3.5, "cpu_s": 4.5, "workers": 5,
                    "skipped": "no", "remote": REMOTE},
        "pre_verdict": {"state": "none", "kind": "deadline", "why": "w"},
        "pre_affected": {"ran": "all", "took": "kept", "by": "lane", "lane_s": 9.5, "tests": 3},
        "main_red": {"sha": "m", "nodes": ["t::a", "t::b"]},
        "cov_risk": {"lines": 2, "said": "s", "files": {"a.rs": "1-2"}, "head": "h", "base": "v1",
                     "carry": [{"file": "a.rs", "line": 1, "text": "x", "why": "w", "own": True,
                                "measured": False}]}}  # fmt: skip
MARK = {"pid": 7, "pid_stamp": "7 Wed", "item": "A.B", "repo": "/r/goh", "at": 1.5, "until": 99.5,
        "slot": {"worktree": "/q/slots/0", "token": "tok"}, "superseded": 8,
        "pre": {"number": 9, "born": [11, 12], "group": 9}}  # fmt: skip
DOCS: dict[str, list[dict]] = {
    "lane_starts": [LANE, LOW, BROKEN],
    "flakes": [FLAKE, {"filed": "GOH.FLAKEK", "epoch": AT}],
    "selection_misses": [JUDGED, MISS, STEP],
    "reuse_shadow": [{**SAMPLE, "outcome": "disagreed"}],
    "reuse_audit": [OFF, ON, STARVED],
    "requests": [LAND, PUSH, MARK],
    "early": [LAND, MARK],
}
_PLAIN = ("item", "repo", "sha", "mode")


def named(t: Table, doc: dict, n: int) -> dict:
    """DOC as the document of row N: a reuse record's key is in it."""
    if t.name != "reuse_audit":
        return json.loads(json.dumps(doc))
    return json.loads(json.dumps(doc).replace('"carry"', f'"p{n}"'))


def own(t: Table, doc: dict, n: int = 1) -> dict:
    """The table's own columns for DOC, as its store module makes them."""
    if t.name in ("requests", "early"):
        key = {"queue": "/q", "run": f"1795000000000000000-{n}"}
        if t.name == "early":
            return {**key, "at": AT}
        return {**key, **{k: doc.get(k) or ("" if k != "item" else doc.get(k)) for k in _PLAIN},
                "pid": doc.get("pid"), "pid_stamp": doc.get("pid_stamp"),
                "until": doc.get("until"), "state": "pending", "filed_at": AT}  # fmt: skip
    return {
        "lane_starts": {"at": AT},
        "flakes": {"epoch": AT, "test": "t::a"},
        "selection_misses": {"queue": f"/q{n}", "at": AT, "day": doc.get("day")},
        "reuse_shadow": {"epoch": float(doc.get("epoch") or 0), "path": doc.get("path") or ""},
        "reuse_audit": {"path": doc.get("path"), "at": AT},
    }[t.name]


UNCUT = 16  # the last schema file an older goh's open applied


def memory(upto: int | None = None) -> sqlite3.Connection:
    """Made once a process and copied from its bytes: the schema files are 0.3 s of CPU a
    connection (GOH.PYSLOW4)."""
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.deserialize(_made(upto, goh_db.SEAM))
    return con


@functools.cache
def _made(upto: int | None, seam: db_core.Seam) -> bytes:
    with contextlib.closing(sqlite3.connect(":memory:", isolation_level=None)) as con:
        con.executescript(db_core.BOOT)
        files = db_schema.migrations(seam).items()
        born = seam.born if upto is None else ()
        some = {n: p for n, p in files if upto is None or n <= upto}
        assert db_schema.fresh(con, some, "t", set(), born)
        return con.serialize()


def old(con: sqlite3.Connection, t: Table, doc: dict, mine: dict) -> None:
    row = {**mine, "body": json.dumps(doc, sort_keys=True)}
    con.execute(f"INSERT INTO {t.name}({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                list(row.values()))  # fmt: skip


def held(con: sqlite3.Connection, t: Table) -> list[dict]:
    rows = []
    kids = sorted({k.table for k in t.kids})
    cols = body_columns.columns(con, t.name)
    for r in con.execute(f"SELECT * FROM {t.name} ORDER BY rowid"):
        row = dict(zip(cols, r, strict=True))
        row.pop("body", None)
        row["rest"] = json.loads(row["rest"])
        ours = " AND ".join(f"{k} = ?" for k in t.key)
        for kid in kids:
            got = con.execute(f"SELECT * FROM {kid} WHERE {ours}", [row[k] for k in t.key])
            row[kid] = sorted((tuple(x)[len(t.key) :] for x in got), key=repr)
        rows.append({k: v for k, v in row.items() if k not in (*t.key, "queue")})
    return rows
