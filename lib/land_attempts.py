"""lib/land_attempts.py -- every land attempt is a ledger row; an item's retries are one figure
(GOH.GATEBUDGET2).

OWNER, 2026-11-15: "apply the budget to the land queue retries too". A land's real cost is every
attempt it took: GOH.REUSEAUDIT went red after 537 s and landed on a second attempt, and the ledger
held only the land that took the lock, so the red one was nobody's cost. Here:

    attempt_of(q, item)      the attempt number a finishing request of ITEM is: one more than the
                             reds of that item in queue Q's land records (lib/land_records.py)
                             since its last landed record (a re-enqueue
                             after a red is attempt n+1, never a fresh request)
    row_of(outcome, ref)     one `land attempt` row (kind `land attempt`, subject `<repo> land
                             attempt`): the request's started -> finished, ok when it landed, the
                             item, the attempt, the mode and why it went red; written by
                             tools/landq.py as it records each request, failed and timed-out ones
                             too, and backfilled from the land records that carry their times
    retry_runs(rows)         each item's CHAIN -- its attempts from the first to the one that
                             landed -- as one `land retries` run: wall_s the attempts' summed wall,
                             `attempts` their count. A chain still red at the end is an open run
                             (ok False): like any failed run it counts only once it is past BREACH.

Pinned by tests/test_land_attempts.py.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import land_records  # the land records' one door (GOH.STATEDB)
import timing_ledger

KIND = "land attempt"
RETRIES = "land retries"


def queue_dir() -> Path:
    """The landing queue (tools/landq.py's GOH_LANDQ_DIR)."""
    return Path(os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")


def item_of(path: Path, rec: dict) -> str:
    """A done record's item: its own field, else the name the request was filed under
    (`<ns>-<ITEM>.json`; tools/landq.py names a request by its item, else by its repo)."""
    return str(rec.get("item") or path.stem.partition("-")[2])


def attempt_of(q: Path, item: str) -> int:
    """The attempt number of ITEM's next record in queue Q (1 when it has never gone red)."""
    n = 0
    for run, rec in land_records.records(q):
        if item and item_of(Path(f"{run}.json"), rec) == item:
            n = 0 if rec.get("state") == "landed" else n + 1
    return n + 1


def _when(ts: object) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return None


def row_of(outcome: dict, ref: str) -> dict | None:
    """The `land attempt` row of a finished request's OUTCOME; None without its start and end."""
    start, end = _when(outcome.get("started")), _when(outcome.get("finished"))
    if start is None or end is None or not outcome.get("repo"):
        return None
    return timing_ledger.row_for(
        KIND,
        timing_ledger.repo_name(str(outcome["repo"])),
        (end - start).total_seconds(),
        ok=outcome.get("state") == "landed",
        ref=ref,
        at=end,
        read_load=False,
        load1=outcome.get("load_start"),
        load_end=outcome.get("load_end"),
        item=outcome.get("item") or None,
        attempt=outcome.get("attempt"),
        mode=outcome.get("mode"),
        why=outcome.get("why"),
        sha=str(outcome.get("sha") or "")[:12] or None,
        slot_wait_s=outcome.get("slot_wait_s"),  # GOH.SLOTPRIORITY: the slot waits, per class
        slot_wait_by_class=outcome.get("slot_wait_by_class") or None,
    )


def record(outcome: dict, ref: str) -> dict | None:
    """Append OUTCOME's row unless its ref is already in the ledger; the row, or None."""
    row = row_of(outcome, ref)
    if row is None or ref in timing_ledger.known_refs(KIND, row["repo"]):
        return None
    return row if timing_ledger.append(row) else None


def backfill(q: Path) -> int:
    """Rows for queue Q's land records that carry their times and have none yet; how many were
    added. Their attempt numbers are counted in the records' order, as the lander would have."""
    have = {(r["repo"], r.get("ref")) for r in timing_ledger.read() if r.get("kind") == KIND}
    reds: dict[str, int] = {}
    added = []
    for run, rec in land_records.records(q):
        path = Path(f"{run}.json")
        item = item_of(path, rec)
        n = reds.get(item, 0)
        reds[item] = 0 if rec.get("state") == "landed" else n + 1
        row = row_of({"item": item, "attempt": n + 1, **rec}, path.stem)
        if row is not None and (row["repo"], path.stem) not in have:
            added.append(row)
    for row in added:
        timing_ledger.append(row)
    return len(added)


def retry_runs(rows: list[dict]) -> list[dict]:
    """Each item's chains of `land attempt` rows as `land retries` runs, oldest first."""
    open_: dict[tuple[str, str], list[dict]] = {}
    runs: list[dict] = []
    for r in sorted((r for r in rows if r.get("kind") == KIND), key=lambda r: r.get("epoch", 0)):
        key = (r["repo"], str(r.get("item") or r.get("ref") or ""))
        open_.setdefault(key, []).append(r)
        if r.get("ok"):
            runs.append(_chain(open_.pop(key)))
    runs += [_chain(c) for c in open_.values()]
    return sorted(runs, key=lambda r: r["epoch"])


def _chain(attempts: list[dict]) -> dict:
    last = attempts[-1]
    return {
        "v": timing_ledger.SCHEMA,
        "at": last["at"],
        "epoch": last["epoch"],
        "subject": timing_ledger.subject_of(last["repo"], RETRIES),
        "repo": last["repo"],
        "kind": RETRIES,
        "wall_s": round(sum(a["wall_s"] for a in attempts), 1),
        "ok": bool(last.get("ok")),
        "item": last.get("item"),
        "attempts": [
            [a.get("attempt") or i + 1, "landed" if a.get("ok") else "red", a["wall_s"]]
            for i, a in enumerate(attempts)
        ],
    }


def chain_line(run: dict) -> str:
    """`ITEM: 2 attempts: red 537 s, landed 300 s = 837 s` for the budget's table."""
    shown = ", ".join(f"{state} {wall:.0f} s" for _, state, wall in run["attempts"])
    n = len(run["attempts"])
    what = run.get("item") or run["repo"]
    return f"{what}: {n} attempt{'s' if n != 1 else ''}: {shown} = {run['wall_s']:.0f} s"
