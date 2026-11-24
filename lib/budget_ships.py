"""lib/budget_ships.py -- a ship result's ledger rows (split from lib/budget.py, GOH.LANDTARGET,
under the 450-line headroom): one place knows how a result becomes rows.

    ship_rows(res)     the `ship` row (its own run `own_s`, the queue's `land_wait_s` a field:
                       GOH.SHIPSTARVE) and the `release` row of a finished result
    record_ship(res)   a finished ship's rows, goh ship's last act; a ref already there not doubled
    backfill_ships()   the rows of every finished result on disk that has none (GOH_SHIP_DIR)

lib/budget.py re-exports each. Pinned by tests/test_budget.py.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ship_records
import timing_ledger


def _parse(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def ship_rows(res: dict) -> list[dict]:
    """The `ship` row (its OWN run `own_s`, the queue's `land_wait_s` a field: GOH.SHIPSTARVE) and
    the `release` row of a ship result; none while running, one for a ship failed before release."""
    start, end = _parse(res.get("started", "")), _parse(res.get("finished", ""))
    if not start or not end or res.get("schema") != 1 or "name" not in res:
        return []
    repo = timing_ledger.repo_name(res["name"])
    ref = Path(str(res.get("result", ""))).stem or f"{res['name']}-{res['started']}"
    ok = res.get("status") == "ok"
    rows = [
        timing_ledger.row_for(
            "ship",
            repo,
            res["own_s"] if "own_s" in res else (end - start).total_seconds(),
            ok=ok,
            ref=ref,
            at=end,
            read_load=False,
            load1=res.get("load1"),
            sha=str(res.get("sha", ""))[:12],
            item=res.get("item"),
            version=res.get("version"),
            failed_step=res.get("failed_step"),
            land_wait_s=res.get("land_wait_s"),
            top_steps=sorted(
                ([s["name"], s["seconds"]] for s in res.get("steps", []) if "seconds" in s),
                key=lambda p: -p[1],
            ),
        )
    ]
    for s in res.get("steps", []):
        if s.get("name") == "release" and "seconds" in s:
            rows.append(
                timing_ledger.row_for(
                    "release",
                    repo,
                    s["seconds"],
                    ok=s.get("status") == "ok",
                    ref=ref,
                    at=end,
                    read_load=False,
                    version=res.get("version"),
                )
            )
    return rows


def record_ship(res: dict) -> None:
    """A finished ship's rows (goh ship's last act); a ref already in the ledger is not doubled."""
    for row in ship_rows(res):
        if row["ref"] not in timing_ledger.known_refs(row["kind"], row["repo"]):
            timing_ledger.append(row)


def backfill_ships() -> int:
    """Ledger rows for every finished ship result on disk that has none; how many were added. A
    ship that predates the ledger is its first row, with the box's load unknown (not 'now')."""
    have = {(r["kind"], r["repo"], r["ref"]) for r in timing_ledger.read() if r.get("ref")}
    added = []
    for _, res in ship_records.every():
        if isinstance(res, dict):
            added += [r for r in ship_rows(res) if (r["kind"], r["repo"], r["ref"]) not in have]
    for row in sorted(added, key=lambda r: r["epoch"]):
        timing_ledger.append(row)
    return len(added)
