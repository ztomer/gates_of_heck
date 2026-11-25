"""lib/time_account.py -- `goh land-profile --account`: the land and ship time account, closed
(GOH.TIMEACCOUNT; skill attribute-latency, close-the-time-account).

ONE COMMAND prints, from goh.db's rows (and goh-cache.db's gate steps), read only:

  1. THE ACCOUNT. Each land's wall (lib/land_profile.py's e2e) less its named phases -- the
     enqueue pre-step, earlier attempts, queue wait, retries, pre-land, slot wait and every
     top-level gate step, each measured, never a remainder -- is the phase `unaccounted`; named
     phases that overlap (a step that ran beside another) are `overlap`, never a negative. A ship's
     wall less its steps' seconds likewise. Over GOH_ACCOUNT_UNACCOUNTED_PCT (default 5) of the
     wall, summed over the group, is a FAULT line.
  2. THROWN AWAY. Seconds that bought no kept verdict, summed by cause: every `run_ends` row that
     ended red, stopped or refused (lib/run_ends.py), and, for the runs before it, the replay of
     the rows that already say so -- red land attempts (`lands`), red or killed checks (`spans`
     `land check*`), red idle runs (`suite*`), failed ships.
  3. THE YIELD of every gate step: lands it ran in, seconds, the lands it was red in and those it
     was the ONLY red step in (goh-cache.db `steps`, top-level rows of land runs). A step red
     alone 0 times over GOH_ACCOUNT_YIELD_DAYS (default 14) is named: a design question.
  4. THE FIVE READINGS: the pre-step's phases, the enqueue check (and its commit gates when
     recorded), tests chosen / reused / refused and the read that refused them, CPU by owner
     (lib/cpu_owners.py) and lane time by kind (lib/lane_time.py, the agent transcripts).

`--since ISO` (default GOH_ACCOUNT_DAYS, 14 days back), `--json`. Pinned by
tests/test_time_account.py.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_cache_db
import goh_db
import run_ends

PCT_ENV, DAYS_ENV, YIELD_ENV = (
    "GOH_ACCOUNT_UNACCOUNTED_PCT",
    "GOH_ACCOUNT_DAYS",
    "GOH_ACCOUNT_YIELD_DAYS",
)
NAMED = ("enqueue pre-step", "earlier attempts", "queue wait", "retries", "  pre-land",
         "    slot wait")  # fmt: skip
STEP = "    step "
AGE = re.compile(r"\s*\(on [0-9a-f]+, [^)]*\)")  # the kept verdict's sha and age: not the read


def number(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env) or default)
    except ValueError:
        return default


def land_account(stages: dict[str, list]) -> dict | None:
    """One land's wall, named seconds, unaccounted and overlap (module doc 1); None with no e2e.
    Each container's unnamed rest is unaccounted: the e2e less its phases and the lander run, the
    run less pre-land and gate, the gate less slot wait and steps. Steps that ran side by side sum
    past their gate: that excess is `overlap`, never a negative rest."""

    def v(name: str) -> float | None:
        got = stages.get(name)
        return None if not got or got[0] is None else float(got[0])

    wall, run, gate = v("e2e"), v("lander run"), v("  gate")
    if wall is None:
        return None
    top = sum(v(n) or 0.0 for n in NAMED[:4])
    steps = sum(float(x[0]) for n, x in stages.items() if n.startswith(STEP) and x[0] is not None)
    inner = (v("    slot wait") or 0.0) + steps
    rests = [wall - top - (run or 0.0)]
    if run is not None:
        rests.append(run - (v("  pre-land") or 0.0) - (gate or 0.0))
    if gate is not None:
        rests.append(gate - inner)
    un = sum(max(0.0, r) for r in rests)
    return {"wall": wall, "named": wall - un, "unaccounted": un,
            "overlap": sum(max(0.0, -r) for r in rests)}  # fmt: skip


def ship_accounts(con: sqlite3.Connection, since: float) -> list[dict]:
    out = []
    for result, started, finished, steps in con.execute(
        "SELECT s.result, s.started, s.finished, (SELECT sum(seconds) FROM ship_steps t "
        "WHERE t.result = s.result) FROM ships s WHERE s.finished IS NOT NULL"):  # fmt: skip
        a, b = _when(started), _when(finished)
        if a is None or b is None or a < since:
            continue
        wall, named = b - a, float(steps or 0.0)
        out.append({"ref": result, "wall": wall, "named": named,
                    "unaccounted": max(0.0, wall - named),
                    "overlap": max(0.0, named - wall)})  # fmt: skip
    return out


def _when(text: object) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    try:
        return dt.datetime.fromisoformat(str(text)).timestamp()
    except ValueError:
        return None


def totals(accounts: list[dict], pct: float) -> dict:
    wall = sum(a["wall"] for a in accounts)
    un = sum(a["unaccounted"] for a in accounts)
    share = 100.0 * un / wall if wall else 0.0
    return {"n": len(accounts), "wall_s": wall, "named_s": sum(a["named"] for a in accounts),
            "unaccounted_s": un, "overlap_s": sum(a["overlap"] for a in accounts),
            "unaccounted_pct": share, "fault": bool(accounts) and share > pct,
            "median_unaccounted_s": statistics.median([a["unaccounted"] for a in accounts])
            if accounts else None}  # fmt: skip


def thrown(con: sqlite3.Connection, since: float, ended: list[dict]) -> dict[str, list[float]]:
    """{cause: [runs, seconds]} of the work thrown away (module doc 2)."""
    out: dict[str, list[float]] = {}

    def add(cause: str, secs: float | None) -> None:
        got = out.setdefault(cause, [0, 0.0, 0])  # runs, seconds, runs whose seconds are unknown
        got[0] += 1
        got[1] += float(secs or 0.0)
        got[2] += secs is None

    known = {(r["kind"], r["run"]) for r in ended}
    for r in ended:
        if r["outcome"] in run_ends.THROWN:
            add(f"{r['kind']} {r['outcome']}: {r['cause'] or '?'}", r["wall_s"])
    for stage, cause, red_class, why, started, finished, pre_s in con.execute(
        "SELECT stage, cause, red_class, why, started, finished, pre_s FROM lands "
        "WHERE state = 'red' AND at >= ?", (since,)):  # fmt: skip
        a, b = _when(started), _when(finished)
        said = cause or stage or red_class or str(why or "?").partition(":")[0][:40]
        add(f"land red: {said}", (b - a) if a and b else pre_s)
    for kind, run, wall, exit_ in con.execute(
        "SELECT kind, run, wall_s, exit FROM spans WHERE ok = 0 AND at >= ? AND (kind LIKE "
        "'land check%' OR kind LIKE 'suite%')", (since,)):  # fmt: skip
        if ("check", run) in known:
            continue
        what = run_ends.outcome_of_exit(int(exit_)) if exit_ is not None else "red"
        add(f"{kind} {what}", wall)
    for started, finished, step in con.execute(
        "SELECT started, finished, failed_step FROM ships WHERE status = 'failed'"):  # fmt: skip
        a, b = _when(started), _when(finished)
        if a is not None and a >= since:
            add(f"ship failed: {step or '?'}", (b - a) if b else None)
    return dict(sorted(out.items(), key=lambda kv: -kv[1][1]))


def yields(since: float) -> list[dict]:
    """Each top-level gate step's yield over land runs since SINCE (module doc 3)."""
    runs: dict[str, dict[str, tuple[float, bool]]] = {}
    with goh_cache_db.ro() as con:
        for run, label, ms, rc in con.execute(
            "SELECT run, label, sum(ms), max(coalesce(rc, 0) <> 0) FROM steps WHERE at >= ? "
            "AND coalesce(parent, '') = '' AND tier = 'step' AND run GLOB '0*-*' "
            "GROUP BY run, label", (since,)):  # fmt: skip
            runs.setdefault(run, {})[label] = (float(ms or 0) / 1000.0, bool(rc))
    by: dict[str, dict] = {}
    for steps in runs.values():
        reds = [lab for lab, (_, red) in steps.items() if red]
        for label, (secs, red) in steps.items():
            y = by.setdefault(label, {"step": label, "runs": 0, "seconds": 0.0, "red": 0,
                                      "only_red": 0})  # fmt: skip
            y["runs"] += 1
            y["seconds"] += secs
            y["red"] += red
            y["only_red"] += red and len(reds) == 1
    return sorted(by.values(), key=lambda y: -y["seconds"])


def _median(vals: list) -> float | None:
    got = [float(v) for v in vals if v is not None]
    return statistics.median(got) if got else None


def readings(con: sqlite3.Connection, since: float) -> dict:
    """The five readings (module doc 4), each from its rows."""
    lands = con.execute(
        "SELECT pre_s, pre_run_wait_s, pre_run_run_s, remote_worker_s, remote_mac_s, "
        "pre_affected_tests, pre_affected_ran, remote_tests FROM lands WHERE at >= ? "
        "AND pre_s IS NOT NULL",
        (since,),
    ).fetchall()
    pre = {
        "n": len(lands),
        "pre-step": _median([r[0] for r in lands]),
        "wait": _median([r[1] for r in lands]),
        "run": _median([r[2] for r in lands]),
        "remote worker": _median([r[3] for r in lands]),
        "remote mac": _median([r[4] for r in lands]),
    }
    for p in run_ends.phases(since):
        if p["kind"] == "prestep":
            pre.setdefault("recorded " + p["phase"], []).append(p["seconds"])
    checks = con.execute(
        "SELECT wall_s, ok, exit FROM spans WHERE kind LIKE 'land check%' AND at >= ?", (since,)
    ).fetchall()
    gates = [p for p in run_ends.phases(since) if p["kind"] == "check"]
    refused: dict[str, int] = {}
    for r in lands:
        if r[6]:
            read = AGE.sub("", str(r[6]))
            refused[read] = refused.get(read, 0) + 1
    ended = run_ends.rows(since)
    tests = {
        "pre-steps": len(lands),
        "reused": sum(1 for r in lands if r[5]),
        "refused": sum(1 for r in lands if r[6]),
        "by": refused,
        "chosen (remote)": _median([r[7] for r in lands]),
        "recorded": sum(1 for r in ended if r.get("refused_by") or r.get("tests_reused")),
    }
    import cpu_owners
    import lane_time

    lanes = lane_time.lanes(lane_time.root(), since, "GOH.")
    kinds: dict[str, float] = {}
    for _, _, k in lanes:
        for name, s in k.items():
            kinds[name] = kinds.get(name, 0.0) + s
    return {
        "pre-step phases": pre,
        "enqueue check": {
            "n": len(checks),
            "median_s": _median([c[0] for c in checks]),
            "green": sum(1 for c in checks if c[1]),
            "commit gates recorded": len(gates),
        },
        "tests": tests,
        "cpu by owner": cpu_owners.summary(cpu_owners.rows(since)),
        "lane time": {
            "lanes": len(lanes),
            "wall_s": sum(w for _, w, _ in lanes),
            "median_wall_s": _median([w for _, w, _ in lanes]),
            "by kind": kinds,
        },
    }


def gather(since: float) -> dict:
    import land_profile
    import state_paths
    import timing_ledger

    run_ends.sweep()
    pct = number(PCT_ENV, 5.0)
    rows = land_profile.lands(Path(state_paths.landq_dir()), timing_ledger.read(kinds=("land",)))
    accounts = [
        a for r in rows if r["end"].timestamp() >= since if (a := land_account(r["stages"]))
    ]
    with goh_db.ro() as con:
        ships = ship_accounts(con, since)
        out = {
            "since": since,
            "lands": totals(accounts, pct),
            "ships": totals(ships, pct),
            "thrown": thrown(con, since, run_ends.rows(since)),
            "readings": readings(con, since),
        }
    try:
        out["yield"] = yields(time.time() - 86400 * number(YIELD_ENV, 14.0))
    except (OSError, sqlite3.Error) as e:
        out["yield"] = []
        out["yield_unknown"] = str(e)
    return out


def _dur(v: float | None) -> str:
    if v is None:
        return "-"
    return (
        f"{v / 3600:.1f} h"
        if abs(v) >= 36000
        else f"{v / 60:.1f} min"
        if abs(v) >= 600
        else f"{v:.0f} s"
    )


def render(a: dict) -> list[str]:
    out = []
    for what in ("lands", "ships"):
        t = a[what]
        fault = "  FAULT: over " + f"{number(PCT_ENV, 5.0):g}%" if t["fault"] else ""
        out.append(
            f"── account, {what}: {t['n']} -- wall {_dur(t['wall_s'])} = named "
            f"{_dur(t['named_s'])} + unaccounted {_dur(t['unaccounted_s'])} "
            f"({t['unaccounted_pct']:.1f}%, median {_dur(t['median_unaccounted_s'])}) "
            f"- overlap {_dur(t['overlap_s'])}{fault}"
        )
    out.append("── thrown away, by cause (runs, seconds, runs with no seconds recorded):")
    out += [
        f"  {int(n):5d}  {_dur(secs):>9}  {int(u):4d}  {c}"
        for c, (n, secs, u) in a["thrown"].items()
    ]
    out.append("── yield of each gate step (land runs, seconds, red, the only red):")
    for y in a["yield"]:
        zero = "  <- never the only red" if y["only_red"] == 0 else ""
        out.append(
            f"  {y['runs']:5d} {_dur(y['seconds']):>9} {y['red']:4d} {y['only_red']:4d}  "
            f"{y['step'][:70]}{zero}"
        )
    if a.get("yield_unknown"):
        out.append(f"  unknown: {a['yield_unknown']}")
    r = a["readings"]
    p = r["pre-step phases"]
    out.append(
        f"── 1 pre-step phases ({p['n']} lands, medians): pre-step {_dur(p['pre-step'])}, wait "
        f"{_dur(p['wait'])}, run {_dur(p['run'])}, remote worker {_dur(p['remote worker'])}, "
        f"remote mac {_dur(p['remote mac'])}; recorded phases: "
        + (
            ", ".join(
                f"{k.removeprefix('recorded ')} {len(v)}"
                for k, v in p.items()
                if k.startswith("recorded ")
            )
            or "none yet"
        )
    )
    c = r["enqueue check"]
    out.append(
        f"── 2 enqueue check: {c['n']} runs, median {_dur(c['median_s'])}, {c['green']} green; "
        f"commit gates recorded: {c['commit gates recorded']}"
    )
    t = r["tests"]
    out.append(
        f"── 3 tests: {t['pre-steps']} pre-steps, kept verdict reused {t['reused']}, refused "
        f"{t['refused']}, tests chosen (remote, median) {t['chosen (remote)']}; "
        f"recorded rows {t['recorded']}"
    )
    out += [
        f"  {n:5d}  refused by: {why[:90]}"
        for why, n in sorted(t["by"].items(), key=lambda kv: -kv[1])[:5]
    ]
    u = r["cpu by owner"]
    cost = "-" if u["sampler_pct"] is None else f"{u['sampler_pct']:.3f}%"
    out.append(
        f"── 4 cpu by owner: {u['windows']} windows over {_dur(u['wall_s'])}; sampler {cost} of "
        f"one core (bound {u['max_pct']:g}%)"
    )
    wall = u["wall_s"] or 1.0
    out += [
        f"  {o:<12} {s / wall:6.2f} cores"
        for o, s in sorted(u["cpu_s"].items(), key=lambda kv: -kv[1])
    ]
    lane = r["lane time"]
    out.append(
        f"── 5 lane time: {lane['lanes']} lanes, {_dur(lane['wall_s'])}, median "
        f"{_dur(lane['median_wall_s'])}"
    )
    total = lane["wall_s"] or 1.0
    out += [
        f"  {k:<16} {_dur(secs):>9} {100 * secs / total:5.1f}%"
        for k, secs in sorted(lane["by kind"].items(), key=lambda kv: -kv[1])
    ]
    return out


def main(since_text: str | None, as_json: bool) -> int:
    import json

    since = _when(since_text) if since_text else time.time() - 86400 * number(DAYS_ENV, 14.0)
    if since is None:
        raise SystemExit(f"goh land-profile --account: --since {since_text!r} is no ISO time")
    a = gather(since)
    print(json.dumps(a, indent=2, default=str) if as_json else "\n".join(render(a)))
    return 0
