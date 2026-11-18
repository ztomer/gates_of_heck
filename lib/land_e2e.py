"""lib/land_e2e.py -- a land END TO END, one budget run per item (GOH.LANDTARGET).

OWNER TARGET, 2026-11-18: a goh land end to end -- the enqueue request, its pre-step, the queue
wait, the land gate, the retries, main fast-forwarded -- in 5 min for a plain land and 10 min for an
instrumented one (lib/budget.py KINDS, `land e2e plain` / `land e2e instrumented`, each judged on
the median of its last five runs). The per-step kinds (land, landq wait, landq run, pre-land,
land retries) stay; this is the whole the user waits for.

    rows(q)       one row per landed item of mode `land` in <q>/done/: its CHAIN (its records from
                  the first after its last landing to the one that landed, so a retry is inside
                  it) from the first record's START to the landing's `finished`. START is the
                  request's `requested` (lib/landq_request.py's enqueued_at, taken BEFORE the
                  enqueue's pre-step; tools/landq.py copies it and the pre-step's `pre_s` into the
                  done record) -- `from: requested`; a record older than that carries none, so its
                  filing (the request's name, time_ns at enqueue, after the pre-step) is the
                  start -- `from: filed`, the enqueue's pre-step not in it.
                  INSTRUMENTED when the landing's log, or its batch gate's (`<stem>.gate*.log`),
                  carries the coverage step's timing row; a landing whose logs are gone is no row
                  (never guessed plain). A batch of N shares one gate and is N rows, each its own
                  request to landed.
    backfill(q)   append the rows the ledger lacks (by ref, the landing's name); how many. Run by
                  `goh budget` and by tools/landq.py after each record.

Pinned by tests/test_land_e2e.py.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timing_ledger

PLAIN = "land e2e plain"
INSTRUMENTED = "land e2e instrumented"
KINDS = (PLAIN, INSTRUMENTED)
# The step table's row of the instrumented run (tools/land.sh's timings: wall, cpu, rc, dir, name).
COVERAGE = re.compile(r"rc=\d+\s+\S+\s+coverage \(floor")


def _when(ts: object) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def filed(path: Path) -> dt.datetime | None:
    """A request's filing time: its name's time_ns prefix (tools/landq.py enqueue)."""
    head = path.stem.partition("-")[0]
    return dt.datetime.fromtimestamp(int(head) / 1e9, dt.UTC) if head.isdigit() else None


def instrumented(q: Path, path: Path, rec: dict) -> bool | None:
    """Whether the land ran the coverage step; None when none of its logs is left to say."""
    logs = q / "logs"
    found = [Path(rec["log"])] if rec.get("log") else []
    found += [logs / f"{path.stem}.log", *sorted(logs.glob(f"{path.stem}.gate*.log"))]
    texts = []
    for log in dict.fromkeys(found):
        try:
            texts.append(log.read_text(errors="replace"))
        except OSError:
            continue
    return any(COVERAGE.search(t) for t in texts) if texts else None


def _start(path: Path, rec: dict) -> tuple[dt.datetime | None, str]:
    asked = _when(rec["requested"]) if rec.get("requested") else None
    return (asked, "requested") if asked else (filed(path), "filed")


def gate_cpu(gates: list[dict], repo: str, rec: dict) -> float | None:
    """The cpu-s of the `land` row land.sh wrote inside the landing's run (a batch's items share
    it): what the relative guard judges, never the wall (budget.trend)."""
    start, end = _when(rec.get("started")), _when(rec.get("finished"))
    if start is None or end is None:
        return None
    lo, hi = start.timestamp() - 1, end.timestamp() + 30
    cpu = [g["cpu_s"] for g in gates
           if g.get("repo") == repo and "cpu_s" in g and lo <= g.get("epoch", 0) <= hi]  # fmt: skip
    return cpu[-1] if cpu else None


def rows(q: Path, gates: list[dict] | None = None) -> list[dict]:
    """Every landed item's e2e row, oldest landing first; GATES the ledger's `land` rows."""
    chains: dict[str, list[tuple[Path, dict]]] = {}
    out: list[dict] = []
    for path in sorted((q / "done").glob("*.json")):
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("mode") != "land":
            continue
        item = str(rec.get("item") or path.stem.partition("-")[2])
        chain = chains.setdefault(item, [])
        chain.append((path, rec))
        if rec.get("state") != "landed":
            continue
        del chains[item]
        row = _row(q, item, chain, gates or [])
        if row is not None:
            out.append(row)
    return out


def _row(q: Path, item: str, chain: list[tuple[Path, dict]], gates: list[dict]) -> dict | None:
    (first, head), (last, rec) = chain[0], chain[-1]
    start, origin = _start(first, head)
    end = _when(rec.get("finished")) if rec.get("finished") else None
    cov = instrumented(q, last, rec)
    if start is None or end is None or cov is None or not rec.get("repo"):
        return None
    repo = timing_ledger.repo_name(str(rec["repo"]))
    return timing_ledger.row_for(
        INSTRUMENTED if cov else PLAIN,
        repo,
        (end - start).total_seconds(),
        cpu_s=gate_cpu(gates, repo, rec),
        ok=True,
        ref=last.stem,
        at=end,
        read_load=False,
        load1=rec.get("load_start"),
        load_end=rec.get("load_end"),
        item=item,
        tries=len(chain),  # not `attempts`: that is a retries chain (budget.judge)
        pre_s=head.get("pre_s"),
        sha=str(rec.get("sha") or "")[:12] or None,
        **{"from": origin},
    )


def backfill(q: Path) -> int:
    """Append the e2e rows the ledger lacks; how many were added."""
    ledger = timing_ledger.read()
    have = {(r["kind"], r["repo"], r.get("ref")) for r in ledger}
    gates = [r for r in ledger if r.get("kind") == "land"]
    added = [r for r in rows(q, gates) if (r["kind"], r["repo"], r["ref"]) not in have]
    for row in added:
        timing_ledger.append(row)
    return len(added)
