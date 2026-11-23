#!/usr/bin/env python3
"""lib/budget.py -- `goh budget`: every subject's time budget, read from the one timing ledger
(lib/timing_ledger.py, GOH.GATEBUDGET).

    goh budget [--json] [--refresh-ships]      # the table; --json the same facts for a caller
    budget.py backfill-ships                   # ledger rows from the ship result JSONs (idempotent)
    goh budget gates [--json]                  # each estate repo's last push gate, per check

OWNER, 2026-11-15: "once we reach the 10m mark, going above 15m will trigger a self improvement
cycle until we are back below 10m". Per SUBJECT (a push gate, a land, a ship, a ship's release step,
per repo) a hysteresis state over its runs in the ledger, thresholds from ONE table (KINDS):

    UNARMED   no run has finished within ARM yet: nothing to defend, so nothing can breach,
              unless the kind's target was set before its first run (`target_set`, the land e2e:
              GOH.E2EARMED), where a figure over BREACH is BREACHED even before ARM was met
    ARMED     a run finished in <= ARM (10 min)
    BREACHED  an ARMED (or CLEARED) subject ran > BREACH (15 min)
    CLEARED   a BREACHED subject ran <= ARM again; its next run <= ARM makes it ARMED

Between ARM and BREACH nothing moves: that band is the hysteresis. A kind may judge the MEDIAN of
its last five runs instead of the last run (`median5`: one slow wait behind a long gate is not a
breach; the commit gates' 30 s / 60 s). A FAILED run counts only when it ran past BREACH: a fast red
measures nothing about the gate's cost, a red at 41 min proves it was slow.

THE WALL STATE IS THE OWNER'S AND IS DISPLAYED AS IS; THE JUDGEMENT OF A REGRESSION IS CPU-S
(2026-11-17 review). Load predicts nothing about wall time on this box (528 s at load 148, 1028 s
at 24.7), so each run carries the CPU its reaped tree spent and the gate's own control drift
(lib/step_timings.py), and `trend` compares the LAST run's cpu-s to the median of the runs before
it:
  regressed  cpu-s more than REL over that median
  void       the control drifted more than CONTROL_DRIFT: the box moved under the run, so the
             figure holds no information
  loaded     the wall is over BREACH but the cpu-s is flat: the box, not the code
The relative guard needs no ARM: an UNARMED subject, which the 10 / 15 states cannot defend, still
shows a run that moved +REL over its own median.

A breach is never silent (lib/budget_breach.py: `breaches` in the JSON). The land END TO END:
lib/land_e2e.py (GOH.LANDTARGET).

A SUBJECT'S START AND END are the sentences in KINDS[kind].span, one per kind, so every writer of a
kind measures the same span. Subjects no writer feeds yet (`writer` false) are listed UNMEASURED,
never silently absent; so is a repo that ships through goh but has no row.

Pinned by tests/test_budget.py.
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
import json
import os
import statistics
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import budget_breach  # lib/: a BREACHED subject named for the coordinator (GOH.LANDTARGET)
import budget_ships  # lib/: a ship result's rows (split for the headroom, GOH.LANDTARGET)
import cip_history  # lib/: the CIP lanes' push gates, the ledger's first rows (GOH.GATEBUDGET2)
import gate_times  # lib/: `goh budget gates`, each estate repo's last push gate (CLS.CIPROFILE)
import land_attempts  # lib/: each land attempt a row, an item's retries one run (GOH.GATEBUDGET2)
import land_e2e  # lib/: a land end to end, request to main, one run per item (GOH.LANDTARGET)
import reuse_audit  # lib/: each reuse path's counts (GOH.REUSEAUDIT2)
import ship_records  # the ship records in goh.db (GOH.STATEDB S2)
import timing_ledger

MIN = 60
CONTROL_DRIFT = 0.15  # a figure holds within 15% (lib/step_timings.py, BACKLOG 5.3)
REL = 0.25  # the relative guard: this run's cpu-s over the median of the runs before it
WINDOW = 5  # runs behind a median
MIN_BASE = 3  # prior runs the relative guard needs before it judges
UNARMED, ARMED, BREACHED, CLEARED = "UNARMED", "ARMED", "BREACHED", "CLEARED"
# OWNER TARGET 2026-11-18 (GOH.LANDTARGET): a goh land end to end in 5 min plain, 10 min
# instrumented (the coverage step ran). BREACH, owner 2026-11-18: "yes, 7.5 and 15 for the
# breach" (the ship's 1.5x hysteresis). Move them here.
LAND_E2E_PLAIN_ARM_S = 5 * MIN
LAND_E2E_PLAIN_BREACH_S = 7.5 * MIN
LAND_E2E_INSTRUMENTED_ARM_S = 10 * MIN
LAND_E2E_INSTRUMENTED_BREACH_S = 15 * MIN


@dataclass(frozen=True)
class Kind:
    """One subject kind: thresholds (seconds), what a state is judged on, its span, its writer."""

    arm_s: float
    breach_s: float
    agg: str  # "last" (the last run) or "median5" (the median of the last five)
    span: str  # where the subject starts and ends
    writer: str  # who appends it; "" when no writer feeds it yet (listed UNMEASURED)
    wall_trend: bool = True  # False: the relative guard judges cpu-s only, never the wall
    # True: the owner set the target before any run, so an UNARMED subject over BREACH is
    # BREACHED (GOH.E2EARMED); False: nothing to defend until a run has been <= ARM once
    target_set: bool = False


# THE ONE TABLE (owner 2026-11-15): every threshold lives here, none in code. tests/test_budget.py
# walks it and tests each kind at both edges.
KINDS: dict[str, Kind] = {
    "push": Kind(
        10 * MIN,
        15 * MIN,
        "last",
        "gate.sh --full's start in the clean export to its exit; slot wait and export excluded",
        "the push gate",
    ),
    "land": Kind(
        10 * MIN,
        15 * MIN,
        "last",
        "land lock taken to main fast-forwarded (or the land refused); lock wait a field",
        "the land step",
    ),
    "ship": Kind(
        10 * MIN,
        15 * MIN,
        "last",
        "the ship's own run: land lock taken to `finished`, every step; lock wait a field",
        "goh ship",
    ),
    "release": Kind(
        10 * MIN,
        15 * MIN,
        "last",
        "the ship's release step: its start to its exit, the kit's whole run",
        "goh ship",
    ),
    "ci": Kind(
        10 * MIN,
        15 * MIN,
        "last",
        "a workflow run's createdAt to updatedAt on main (gh, read-only)",
        "",
    ),
    "commit": Kind(30, 60, "median5", "pre-commit or commit-msg: hook start to exit", ""),
    "pre-land": Kind(2 * MIN, 3 * MIN, "last", "the land's pre-step: request to exit", ""),
    "gate slot wait": Kind(10 * MIN, 15 * MIN, "median5", "slot requested to flock taken", ""),
    "load place wait": Kind(
        10 * MIN, 15 * MIN, "median5", "place requested to granted or expired", ""
    ),
    "land queue": Kind(10 * MIN, 15 * MIN, "median5", "goh-land.lock requested to taken", ""),
    "landq wait": Kind(10 * MIN, 15 * MIN, "median5", "enqueued to started", ""),
    "landq run": Kind(10 * MIN, 15 * MIN, "median5", "started to finished", ""),
    "land retries": Kind(
        10 * MIN,
        15 * MIN,
        "last",
        "an item's land attempts summed, the first to the one that lands (lib/land_attempts.py)",
        "goh land: each request's `land attempt` row",
    ),
    land_e2e.PLAIN: Kind(
        LAND_E2E_PLAIN_ARM_S,
        LAND_E2E_PLAIN_BREACH_S,
        "median5",
        "an item's land request (before the enqueue's pre-step) to main fast-forwarded, its "
        "retries in it; no coverage step",
        "lib/land_e2e.py from landq done/ and logs/",
        wall_trend=False,
        target_set=True,
    ),
    land_e2e.INSTRUMENTED: Kind(
        LAND_E2E_INSTRUMENTED_ARM_S,
        LAND_E2E_INSTRUMENTED_BREACH_S,
        "median5",
        "an item's land request (before the enqueue's pre-step) to main fast-forwarded, its "
        "retries in it; the land ran the coverage step",
        "lib/land_e2e.py from landq done/ and logs/",
        wall_trend=False,
        target_set=True,
    ),
    "suite": Kind(
        10 * MIN,
        15 * MIN,
        "last",
        "tools/pytest.sh's whole run: pytest's start to its exit (`suite:carried` reused verdicts)",
        "tools/pytest.sh",
    ),
}


def kind_of(name: str) -> Kind | None:
    """KINDS' row for a ledger row's kind (`ci:tests` is `ci`)."""
    return KINDS.get(name) or KINDS.get(name.split(":", 1)[0])


def counts(run: dict, kind: Kind) -> bool:
    """A passed run measures its subject; a failed one only when it already ran past BREACH."""
    return bool(run.get("ok", True)) or run["wall_s"] > kind.breach_s


def figure(runs: list[dict], i: int, kind: Kind) -> float:
    """The value run I is judged on: its own wall, or the median of the last WINDOW up to it."""
    if kind.agg == "median5":
        return statistics.median(r["wall_s"] for r in runs[max(0, i - WINDOW + 1) : i + 1])
    return runs[i]["wall_s"]


def state_of(runs: list[dict], kind: Kind) -> str:
    """The hysteresis state after RUNS (oldest first, already filtered by `counts`)."""
    state = UNARMED
    for i in range(len(runs)):
        v = figure(runs, i, kind)
        if state == UNARMED:
            if v <= kind.arm_s:
                state = ARMED
            elif kind.target_set and v > kind.breach_s:
                state = BREACHED
        elif state in (ARMED, CLEARED):
            if v > kind.breach_s:
                state = BREACHED
            elif v <= kind.arm_s:
                state = ARMED
        elif state == BREACHED and v <= kind.arm_s:
            state = CLEARED
    return state


def trend(runs: list[dict], kind: Kind) -> dict:
    """The last run against the median of the runs before it, on cpu-s (wall for a run with none).

    `verdict`: new (too few runs before it), void (the control drifted), regressed (cpu-s over the
    median by more than REL), loaded (wall past BREACH with cpu-s inside REL: the box, not the
    code), ok. `delta` is the relative move, None when it was not judged."""
    if not runs:
        return {"verdict": "new", "delta": None, "metric": "wall_s"}
    last, base = runs[-1], runs[:-1][-WINDOW:]
    metric = "cpu_s" if "cpu_s" in last and all("cpu_s" in r for r in base) else "wall_s"
    if metric == "wall_s" and not kind.wall_trend:
        return {"verdict": "no cpu-s", "delta": None, "metric": "cpu_s"}
    if len(base) < MIN_BASE:
        return {"verdict": "new", "delta": None, "metric": metric}
    median = statistics.median(r[metric] for r in base)
    delta = (last[metric] - median) / median if median else 0.0
    drift = last.get("control_drift")
    if drift is not None and abs(drift) > CONTROL_DRIFT:
        verdict = "void"
    elif delta > REL:
        verdict = "regressed"
    elif metric == "cpu_s" and last["wall_s"] > kind.breach_s:
        verdict = "loaded"
    else:
        verdict = "ok"
    return {
        "verdict": verdict,
        "delta": round(delta, 3),
        "metric": metric,
        "median": round(median, 1),
    }


def subjects(rows: list[dict]) -> dict[str, list[dict]]:
    """Each subject's runs in TIME order (a backfill appends rows older than live ones); land
    attempts become their items' `land retries` runs."""
    out: dict[str, list[dict]] = {}
    rows = sorted([*rows, *land_attempts.retry_runs(rows)], key=lambda r: r.get("epoch", 0))
    for r in rows:
        if kind_of(r.get("kind", "")) is not None and isinstance(r.get("wall_s"), (int, float)):
            out.setdefault(r["subject"], []).append(r)
    return out


def judge(subject: str, rows: list[dict]) -> dict:
    kind = kind_of(rows[0]["kind"])
    assert kind is not None
    runs = [r for r in rows if counts(r, kind)]
    last = rows[-1]
    out = {
        "subject": subject,
        "kind": rows[0]["kind"],
        "state": state_of(runs, kind) if runs else UNARMED,
        "arm_s": kind.arm_s,
        "breach_s": kind.breach_s,
        "agg": kind.agg,
        "runs": len(rows),
        "last_s": last["wall_s"],
        "last_ok": bool(last.get("ok", True)),
        "median_s": round(statistics.median(r["wall_s"] for r in (runs or rows)[-WINDOW:]), 1),
        "cpu_s": last.get("cpu_s"),
        "load1": last.get("load1"),
        "at": last.get("at"),
        "trend": trend(runs, kind)
        if runs
        else {"verdict": "new", "delta": None, "metric": "wall_s"},
    }
    if last.get("top_steps"):
        out["top_steps"] = last["top_steps"]
    if last.get("attempts"):
        out["attempts"] = last["attempts"]
        out["chain"] = land_attempts.chain_line(last)
    return out


# ---- the report -----------------------------------------------------------------------------


def unmeasured(seen: set[str]) -> list[dict]:
    """What the budget should cover and has no run for: a kind no writer feeds, and each repo that
    ships through goh (a result directory) with no `ship` row."""
    out = [{"subject": k, "why": "no writer yet"} for k, v in KINDS.items() if not v.writer]
    kinds = {s.partition(" ")[2] for s in seen}
    out += [
        {"subject": k, "why": "no landed run recorded"} for k in land_e2e.KINDS if k not in kinds
    ]
    for d in ship_records.repo_dirs():
        for kind in ("ship", "release"):
            name = timing_ledger.subject_of(timing_ledger.repo_name(d.name), kind)
            if name not in seen:
                out.append({"subject": name, "why": "no finished run recorded"})
    return out


def reuse_rows() -> list[dict]:
    """Each registered reuse path's window, as `reuse_audit.py report` counts it: what the time
    saved by skipping work stands on (GOH.REUSEAUDIT2)."""
    out = []
    for r in reuse_audit.report():
        counts = {k: r["window"].get(k, 0) for k in REUSE_COUNTS}
        out.append({"path": r["path"], "state": r["state"], "every": r["every"],
                    "verdict": r["verdict"], **counts})  # fmt: skip
    return out


REUSE_COUNTS = ("uses", "sampled", "compared", "disagreed", "flakes")


def snapshot() -> dict:
    rows = timing_ledger.read()
    judged = [judge(s, r) for s, r in sorted(subjects(rows).items())]
    return {
        "ledger": str(timing_ledger.ledger_path()),
        "subjects": judged,
        "breaches": budget_breach.breaches(judged),
        "unmeasured": unmeasured({j["subject"] for j in judged}),
        "reuse": reuse_rows(),
    }


_min = budget_breach.minutes
ship_rows, record_ship = budget_ships.ship_rows, budget_ships.record_ship
ship_dir, backfill_ships = ship_records.ship_dir, budget_ships.backfill_ships


GLYPH = {ARMED: "✓", CLEARED: "✓", BREACHED: "✗", UNARMED: "·"}


def render(snap: dict) -> str:
    out = [f"goh budget: {len(snap['subjects'])} subjects in {snap['ledger']}"]
    width = max([len(j["subject"]) for j in snap["subjects"]] + [7])
    out.append(
        f"  {'subject':<{width}}  {'state':<9} {'last':>9} {'median5':>9} {'cpu':>8} "
        f"{'load':>6}  trend"
    )
    for j in snap["subjects"]:
        t = j["trend"]
        shown = t["verdict"] + (
            f" {t['delta']:+.0%} {t['metric'][:-2]}" if t["delta"] is not None else ""
        )
        load = "-" if j["load1"] is None else f"{j['load1']:.1f}"
        red = "" if j["last_ok"] else " (failed)"
        out.append(
            f"{GLYPH[j['state']]} {j['subject']:<{width}}  {j['state']:<9} {_min(j['last_s']):>9} "
            f"{_min(j['median_s']):>9} {_min(j['cpu_s']):>8} {load:>6}  {shown}{red}"
        )
        if j.get("chain"):
            out.append(f"    last: {j['chain']}")
        if j["state"] == BREACHED and j.get("top_steps"):
            out.append(
                "    breach, costliest steps: "
                + ", ".join(f"{a} {b:.0f} s" for a, b in j["top_steps"][:3])
            )
    for u in snap["unmeasured"]:
        out.append(f"· {u['subject']}: unmeasured ({u['why']})")
    for b in snap["breaches"]:
        out.append(f"✗ {b['subject']} BREACHED: {b['action']}")
    out.append("goh reuse audit: each path's skips this window (lib/reuse_audit.py report)")
    out.append(f"  {'path':<9} {'state':<5} " + " ".join(f"{k:>9}" for k in REUSE_COUNTS))
    for r in snap["reuse"]:
        mark = "✗" if r["state"] != "on" else "✓" if r["verdict"] == "sampled" else "·"
        counts = " ".join(f"{r[k]:>9}" for k in REUSE_COUNTS)
        out.append(f"{mark} {r['path']:<9} {r['state']:<5} {counts}  {r['verdict']}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh budget", description="Every subject's time budget.")
    ap.add_argument("--json", action="store_true", help="the same facts as JSON")
    ap.add_argument(
        "--refresh-ships", action="store_true", help="read the ship results first (default)"
    )
    ap.add_argument("--no-backfill", action="store_true", help="do not read the ship results")
    ap.add_argument("cmd", nargs="?", choices=["backfill-ships", "gates"])
    a = ap.parse_args(argv)
    if a.cmd == "gates":  # every estate repo's last push gate, per check (CLS.CIPROFILE)
        return gate_times.main(["--json"] if a.json else [])
    if a.cmd == "backfill-ships":
        print(f"backfilled {backfill_ships()} ship rows")
        return 0
    if not a.no_backfill:
        backfill_ships()  # idempotent: the ships that predate the ledger, or finished without it
        cip_history.backfill()  # the CIP lanes' push gates (GOH.GATEBUDGET2)
        land_attempts.backfill(land_attempts.queue_dir())  # the queue's timed attempts
        land_e2e.backfill(land_attempts.queue_dir())  # each landed item end to end
    snap = snapshot()
    print(json.dumps(snap, ensure_ascii=False, indent=2) if a.json else render(snap))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
