"""lib/spawn_columns.py -- a spawn or outcome of `agent_spawns` as typed columns and child rows
(GOH.SPAWNKEY; goh.db 0013), the one writer and the one reader of the table's fields.

    normal(row)       ROW as it is kept: the spelling older code used (`item`, an item that is only
                      the description's first word) is `items`, and its text is UTF-8
    write(con, row)   ROW's columns, its spawn_lists and spawn_app_exits rows, and its document
                      while the table has a `body`; its n. Inside the caller's transaction
    read(con)         every row as its document, from the columns and child rows, in order
    Refused           a value no column takes, and no document to keep it in (the caller's dead
                      letter, lib/hook_ledgers.py)

ONE HOME. A field is a column or a child row, its name in SCALARS, LISTS, `load` or `apps.exits`.
What no column takes (an unknown key, a value of the wrong type) stays in `rest`, which `goh state
check` names. From a cutover on (lib/state_shape.py), a known key of the wrong type is Refused
instead: no document would explain its `rest`.

`rest` IS WRITTEN, never NULL, so 0013's fill trigger (older code's) does not run on these rows. A
row it has not filled yet (`rest` NULL: `goh state backfill`) is read from its document.
Pinned by tests/test_spawn_columns.py, the triggers alone by tests/test_state_columns.py.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state_shape  # lib/, on the path from the line above

# the key of the document (dotted: inside an object): its column, per type
TEXT = {k: k for k in (
    "agent_type", "description", "effort", "first_line", "model", "model_id", "prompt_sha",
    "tool_use_id", "declared", "route", "agent_type_escape", "status", "report", "profile",
    "profile_why", "lane", "no_item_why")} | {
    "subagent_type": "asked_type", "session_id": "session", "agent_id": "agent",
    "meter.read_at": "meter_read_at", "pool.said": "pool_said", "pool.why": "pool_why"}  # fmt: skip
REAL = {f"meter.{k}": f"meter_{k}" for k in ("5h", "7d", "age_h")}
INTEGER = {f"tokens.{k}": f"tokens_{k}"
           for k in ("cache_creation", "cache_read", "input", "output", "total")}  # fmt: skip
INTEGER["cores"] = "cores"
BOOL = {k: k for k in ("exempt", "explored", "tokens_settled")} | {"meter.ok": "meter_ok"}
SCALARS = {**TEXT, **REAL, **INTEGER, **BOOL}
LISTS = {"tags": "tag", "items": "item", "faults": "fault", "scratch_loose": "scratch_loose",
         "ci_direct": "ci_direct", "apps": "app", "apps.before": "app_before",
         "apps.failed": "app_failed", "apps.missing": "app_missing",
         "apps.relaunched": "app_relaunched"}  # fmt: skip
LOAD = ("load1", "load5", "load15")
EXIT = ("gone_by", "last_seen")
OBJECTS = ("apps", "meter", "pool", "tokens")
ITEM = re.compile(r"^\s*([A-Z][A-Z0-9]*(?:\.[A-Za-z0-9]+)+)\b")  # what older code took an item for


YEAR_10000 = 253402300800


class Refused(ValueError):
    """A row no shape of the table holds."""


def instant(at: object) -> float | None:
    """AT (an ISO stamp, no offset read as UTC; a number) as epoch seconds; None when neither."""
    if isinstance(at, (int, float)) and not isinstance(at, bool):
        return float(at) if 0 <= at < YEAR_10000 else None
    try:
        when = datetime.fromisoformat(at) if isinstance(at, str) else None
    except ValueError:
        return None
    return when and (when if when.tzinfo else when.replace(tzinfo=UTC)).timestamp()


def _text(v: Any) -> Any:
    """V with every text a column can hold (a lone surrogate is no UTF-8)."""
    if isinstance(v, str):
        return v.encode("utf-8", "replace").decode("utf-8")
    if isinstance(v, dict):
        return {_text(k): _text(x) for k, x in v.items()}
    return [_text(x) for x in v] if isinstance(v, list) else v


def normal(row: dict) -> dict:
    doc = _text(row)
    if doc.get("event") == "spawn" and "items" not in doc:
        was = doc.get("item")
        m = None if was else ITEM.match(str(doc.get("description") or ""))
        if named := m.group(1) if m else was if isinstance(was, str) else None:
            doc["items"] = [named]
            doc.pop("item", None)
    return doc


def _number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _fits(path: str, v: object) -> bool:
    if path in BOOL or isinstance(v, bool):
        return path in BOOL and isinstance(v, bool)
    if path in TEXT:
        return isinstance(v, str)
    if path in REAL:
        return _number(v)
    return isinstance(v, int) and -(1 << 63) <= v < 1 << 63


def _get(doc: dict, path: str) -> Any:
    v: Any = doc
    for k in path.split("."):
        v = v.get(k) if isinstance(v, dict) else None
    return v


def _drop(rest: dict, path: str) -> None:
    head, _, key = path.partition(".")
    if not key:
        rest.pop(head, None)
    elif isinstance(rest.get(head), dict):
        rest[head].pop(key, None)


def _exits(v: object) -> list[tuple] | None:
    """`apps.exits` as rows, None when a key or a type of it has no column."""
    if not isinstance(v, dict):
        return None
    for seen in v.values():
        if not isinstance(seen, dict) or not set(seen) <= set(EXIT):
            return None
        if not all(x is None or isinstance(x, str) for x in seen.values()):
            return None
    return [(app, *(seen.get(k) for k in EXIT)) for app, seen in v.items()]


def _split(doc: dict) -> tuple[dict[str, Any], list[tuple], list[tuple], dict]:
    """(columns, spawn_lists rows, spawn_app_exits rows, what none of them takes)."""
    rest = {k: dict(v) if k in OBJECTS and isinstance(v, dict) else v for k, v in doc.items()}
    cols: dict[str, Any] = dict.fromkeys([*SCALARS.values(), *LOAD])
    lists, exits = [], []
    for path, col in SCALARS.items():
        v = _get(doc, path)
        if v is not None and _fits(path, v):
            cols[col] = float(v) if path in REAL else int(v) if path in BOOL else v
            _drop(rest, path)
    for path, kind in LISTS.items():
        v = _get(doc, path)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            lists += [(kind, i, x) for i, x in enumerate(v)]
            _drop(rest, path)
    if (gone := _exits(_get(doc, "apps.exits"))) is not None:
        exits = gone
        _drop(rest, "apps.exits")
    load = doc.get("load")
    if isinstance(load, list) and len(load) == len(LOAD) and all(map(_number, load)):
        cols.update(zip(LOAD, map(float, load), strict=True))
        _drop(rest, "load")
    at = doc.get("time")
    cols["at"] = instant(at)
    if isinstance(at, str) and cols["at"] is not None:
        _drop(rest, "time")
    cols["at"] = cols["at"] or 0.0
    cols["event"] = doc["event"] if isinstance(doc.get("event"), str) else ""
    if cols["event"]:
        _drop(rest, "event")
    for k in OBJECTS:
        if isinstance(rest.get(k), dict):
            rest[k] = {i: x for i, x in rest[k].items() if x not in (None, {}, [])}
    return cols, lists, exits, {k: v for k, v in rest.items() if v not in (None, {}, [])}


def _json(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True)


def _columns(con: sqlite3.Connection) -> set[str]:
    return {r[1] for r in con.execute("PRAGMA table_info(agent_spawns)")}


def write(con: sqlite3.Connection, row: dict) -> int:
    doc = normal(row)
    cols, lists, exits, rest = _split(doc)
    have = _columns(con)
    known = {"event", "time", "load", "apps.exits", *SCALARS, *LISTS}
    odd = sorted(p for p in known if _get(rest, p) is not None and p != "apps")
    odd += ["apps"] * (not isinstance(rest.get("apps", {}), dict))
    if odd and state_shape.closing(con, "agent_spawns"):
        raise Refused(f"{', '.join(odd)}: not a type its column takes")
    items = [v for kind, _, v in lists if kind == "item"]
    cols |= {"rest": _json(rest), "item": items[0] if items else None,
             **state_shape.document(con, "agent_spawns", _json(doc))}  # fmt: skip
    names = [c for c in cols if c in have]  # `item` and `body` leave with contract 0019
    sql = f"INSERT INTO agent_spawns({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
    n = con.execute(sql, [cols[c] for c in names]).lastrowid
    con.executemany("INSERT INTO spawn_lists(n, kind, ord, value, at) VALUES (?, ?, ?, ?, ?)",
                    [(n, *r, cols["at"]) for r in lists])  # fmt: skip
    con.executemany("INSERT INTO spawn_app_exits(n, app, gone_by, last_seen, at) "
                    "VALUES (?, ?, ?, ?, ?)", [(n, *r, cols["at"]) for r in exits])  # fmt: skip
    return int(n or 0)


def _put(doc: dict, path: str, v: object) -> None:
    head, _, key = path.partition(".")
    if key:
        doc = doc.setdefault(head, {})
    doc[key or head] = v


def _merge(doc: dict, rest: dict) -> dict:
    for k, v in rest.items():
        if isinstance(v, dict) and isinstance(doc.get(k), dict):
            _merge(doc[k], v)
        else:
            doc[k] = v
    return doc


def read(con: sqlite3.Connection) -> list[dict]:
    """One snapshot is the caller's: three reads of one transaction."""
    have = _columns(con)
    if "rest" not in have:  # a file no writer of this goh has opened yet
        docs = con.execute("SELECT body FROM agent_spawns ORDER BY n")
        return [json.loads(b) for (b,) in docs]
    paths = {col: p for p, col in SCALARS.items()}
    flags = set(BOOL.values())
    lists: dict[int, dict[str, list[str]]] = {}
    for n, kind, v in con.execute("SELECT n, kind, value FROM spawn_lists ORDER BY n, kind, ord"):
        lists.setdefault(n, {}).setdefault(kind, []).append(v)
    exits: dict[int, dict[str, dict]] = {}
    for n, app, *seen in con.execute("SELECT n, app, gone_by, last_seen FROM spawn_app_exits"):
        said = {k: v for k, v in zip(EXIT, seen, strict=True) if v is not None}
        exits.setdefault(n, {})[app] = said
    body = "body" if "body" in have else "NULL"
    sql = (f"SELECT n, at, event, rest, CASE WHEN rest IS NULL THEN {body} END, "
           f"{', '.join([*LOAD, *paths])} FROM agent_spawns ORDER BY n")  # fmt: skip
    out = []
    for n, at, event, rest, unfilled, *values in con.execute(sql):
        if rest is None:
            out.append(json.loads(unfilled or "{}"))
            continue
        loads = [values.pop(0) for _ in LOAD]
        doc: dict[str, Any] = {"event": event}
        doc["time"] = datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds")
        for (col, path), v in zip(paths.items(), values, strict=True):
            if v is not None:
                _put(doc, path, bool(v) if col in flags else v)
        if loads[0] is not None:
            doc["load"] = loads
        kinds = lists.get(n, {})
        for path, kind in LISTS.items():
            if kind in kinds:
                _put(doc, path, kinds[kind])
        if n in exits:
            _put(doc, "apps.exits", exits[n])
        out.append(_merge(doc, json.loads(rest)))
    return out
