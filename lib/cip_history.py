"""lib/cip_history.py -- the CIP lanes' push gates as the ledger's first rows (GOH.GATEBUDGET2).

OWNER, 2026-11-15/16: every repo a CIP lane (the per-repo CI speed class, CLS.CIPROFILE) brought
to 10 min or less is ARMED from day one: "seed one row per done CIP.* item from its recorded
after-time and load". The timing ledger (lib/timing_ledger.py) began after those lanes closed, so
their figures lived only in each item's result text. That text, read once by hand into numbers,
is `cip_history.json` in the owner's private seed store (the lanes name private repositories): a
list of {item, repo, wall_s (the push gate's work AFTER the lane's fixes, slot wait excluded, as
`push` spans are), load1 and load_end (the 1-min load at its start and end), sha, at (UTC ISO),
before_s (optional: the figure before the lane)}. A lane whose result named no push-gate time (an
estate survey) has no entry; a missing seed backfills nothing.

`backfill()` appends each as a `<repo> push` row with `ref` the CIP item, so a second run adds
nothing; `lib/budget.py` runs it with the ship backfill. A row's `at` is its day at 12:00 UTC (the
results name a day, not a time: `at_day` says so) except where the result named its minute.

Pinned by tests/test_budget_backfill.py.

GOH.GATEBUDGET2: the done CIP lanes' push-gate times (after the lane, slot wait excluded), loads,
shas and days, read once from each item's result into `cip_history.json` in the owner's private
seed store (`GOH_SEED_DIR`: the lanes name private repositories); `backfill()` appends each as a
`<repo> push` row with `ref` the CIP item (never doubled), so a repo a CIP lane brought under 10
min is ARMED from day one (owner 2026-11-16).
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_paths
import timing_ledger

FILE = "cip_history.json"
FIELDS = ("item", "repo", "wall_s", "load1", "load_end", "sha", "at")


def seed_dir() -> Path:
    """The owner's PRIVATE seed store (GOH_SEED_DIR, `goh config`): the lanes name private
    repositories, which this public tree never does (the private_names gate)."""
    return state_paths.seed_dir()


def history() -> list[dict]:
    """The seed's entries, each with every FIELD; a missing or unreadable seed is none."""
    try:
        data = json.loads((seed_dir() / FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data if isinstance(data, list) else []
    return [e for e in entries if isinstance(e, dict) and all(k in e for k in FIELDS)]


def rows() -> list[dict]:
    out = []
    for e in history():
        at = str(e["at"])
        try:
            when = dt.datetime.fromisoformat(at).replace(tzinfo=dt.UTC)
        except ValueError:
            continue  # an entry with no readable time cannot be placed among the runs
        out.append(
            timing_ledger.row_for(
                "push",
                timing_ledger.repo_name(str(e["repo"])),
                float(e["wall_s"]),
                load1=e["load1"],
                load_end=e["load_end"],
                ref=str(e["item"]),
                at=when,
                read_load=False,
                sha=e["sha"],
                item=e["item"],
                before_s=e.get("before_s"),
                at_day=at.endswith("T12:00:00") or None,
                backfill="CIP",
            )
        )
    return out


def backfill() -> int:
    """The seed's rows the ledger does not hold yet; how many were added."""
    have = {(r["repo"], r.get("ref")) for r in timing_ledger.read(kinds=("push",))}
    added = [r for r in rows() if (r["repo"], r["ref"]) not in have]
    for row in added:
        timing_ledger.append(row)
    return len(added)
