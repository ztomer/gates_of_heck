"""lib/land_profile.py -- `goh land-profile`: where a land's minutes and cpu-s go (GOH.LANDPROFILE).

OWNER, 2026-11-18: "we need to get the ci problem solved". The land end to end (lib/land_e2e.py,
GOH.LANDTARGET) is one figure per item; this report splits it into the stages it is made of, so the
gate-cost wave is ordered by what a land actually spends, not by filing order.

One LAND is one landed item's chain (lib/land_e2e.py's: its records from the first after its last
landing to the one that landed). Its stages, each a wall in seconds, cpu-s where one is recorded:

    e2e               the FIRST enqueue attempt (the chain's earliest request, filing or enqueue
                      log: lib/land_spd.py, red ones included) to the landing's `finished`
    earlier attempts  the landing's own request - the first attempt (red enqueue pre-steps)
    enqueue pre-step  filing - `requested` (the enqueue's own pre-step, before the request is filed)
    queue wait        the first record's `started` - its filing
    retries           the landing's `started` - the first record's `started` (0 for one try)
    lander run        the landing's `finished` - its `started`, made of:
      pre-land        the lander's pre-land (its log's "pre-land green in N s"; 0 when reused)
      gate            the land gate (`land` ledger row inside the run: wall_s, cpu_s), made of:
        slot wait     the record's `slot_wait_s`: every gate slot the land waited for, made of:
          slot wait <class>  each class's share (`slot_wait_by_class`, GOH.SLOTPRIORITY): `short`
                      the pre-step's lane, `land` the gate's -- a land queued behind a lower
                      class shows here, never inside one figure
        step <label>  each gate step, summed over the gate's passes (its timings kept beside its
                      log as `<log stem>.timings.jsonl`, else the log's last "top 20 steps"
                      table); nested ones (`> <label>`) under their parent
        phase <name>  the coverage step's phases ("✓ phase 'NAME': N s" in any of the land's logs)
      lander other    run - pre-land - gate (the rebase, the fast-forward, the bookkeeping)

The table groups lands by MODE (plain, instrumented: lib/land_e2e.instrumented) and, with
`--normal-since`, by whether the land started before that time (`loaded`, e.g. before a reboot that
cleared a faulty fseventsd) or after (`normal`). Per stage: n, median and max wall, median cpu-s,
its share of the group's summed e2e, and the median 1-min load at the landing's start.

A BATCH'S SHARED RUNS (the lander run, pre-land, gate, slot wait, steps) count once, on one member
(lib/land_spd.py `once`; GOH.LANDPROFILEFIX). `--last N` and `--since COMMIT` (lands whose landed
commit descends from it) print the SPD exit's figures: commit to landed, the share of lands whose
lander ran the whole pre-land again, the enqueue pre-step's wait, run and workers, and the carry
shadow (GOH.CARRYSHADOW): how many lands' gates measured the proposed carry rule, the tests it would
have run (F) against those run, over every gate run of the land red ones included, and the gate reds
outside F -- a pass it would have carried over a change that broke it.

`--guards` counts the red lands by the guard that refused them (`guard` on each done record,
lib/land_check.py, GOH.LANDCHECK): which structural guard keeps costing lands.

Pinned by tests/test_land_profile.py.
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
import datetime as dt
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bin_reach_audit
import land_e2e
import land_spd
import timing_ledger
from land_steps import gate_steps, kept_timings, phases, texts

PRE_LAND = re.compile(r"✓ pre-land green in (\d+(?:\.\d+)?) s")
PRE_REUSED = re.compile(r"✓ pre-land: the verdict .* not run again")
STEP, NESTED, UNPLACED, PHASE_ROW = "    step ", "      > ", "      ? ", "        phase "
ORDER = ("e2e", "enqueue pre-step", "earlier attempts", "queue wait", "retries", "lander run",
         "  pre-land", "  gate", "    slot wait")  # fmt: skip
SLOT_CLASS = "      slot wait "
SLOT_CLASSES = (
    "short",
    "pre",
    "land",
    "ship",
    "lane",
    "work",
)  # lib/gate_lanes.py CLASSES, by lane and rank


def _secs(a: dt.datetime | None, b: dt.datetime | None) -> float | None:
    return (b - a).total_seconds() if a and b else None


def chains(q: Path) -> list[tuple[str, list[tuple[Path, dict]]]]:
    """Every landed item's chain of `land` records, oldest landing first (land_e2e.rows')."""
    open_: dict[str, list[tuple[Path, dict]]] = {}
    out = []
    for path in sorted((q / "done").glob("*.json")):
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("mode") != "land":
            continue
        item = str(rec.get("item") or path.stem.partition("-")[2])
        open_.setdefault(item, []).append((path, rec))
        if rec.get("state") == "landed":
            out.append((item, open_.pop(item)))
    return out


def land(q: Path, item: str, chain: list[tuple[Path, dict]], gates: list[dict],
         prev: dt.datetime | None = None) -> dict | None:  # fmt: skip
    """One land's stages {name: [wall, cpu]}, its mode and its load; None when it cannot be read.
    PREV is the item's previous landing: the e2e starts at the first enqueue attempt after it, red
    ones too (lib/land_spd.py first_attempt)."""
    (first, head), (last, rec) = chain[0], chain[-1]
    ran = next(((p, r) for p, r in chain if r.get("started")), chain[0])  # the first lander run
    own = rec if rec.get("requested") else head  # the request the landing came of
    asked = land_e2e._when(own["requested"]) if own.get("requested") else None
    filed = land_e2e.filed(last if own is rec else first)
    f_start, l_start = land_e2e._when(ran[1].get("started")), land_e2e._when(rec.get("started"))
    end = land_e2e._when(rec.get("finished"))
    start = land_spd.first_attempt(q, item, chain, prev, end)
    mode = land_e2e.instrumented(q, last, rec)
    if start is None or end is None or l_start is None or mode is None:
        return None
    st: dict[str, list] = {"e2e": [_secs(start, end), None]}
    if asked and filed:
        st["enqueue pre-step"] = [_secs(asked, filed), None]
    if asked and asked > start:  # red enqueue pre-steps and their waits before the landing's own
        st["earlier attempts"] = [_secs(start, asked), None]
    st["queue wait"] = [_secs(land_e2e.filed(ran[0]), f_start), None]
    st["retries"] = [_secs(f_start, l_start), None]
    run = _secs(l_start, end)
    st["lander run"] = [run, None]
    found = texts(q, last, rec)
    pre = [float(x) for t in found[:1] for x in PRE_LAND.findall(t)]
    if pre:
        st["  pre-land"] = [pre[-1], None]
    elif any(PRE_REUSED.search(t) for t in found[:1]):
        st["  pre-land"] = [0.0, None]
    repo = timing_ledger.repo_name(str(rec.get("repo") or ""))
    g = land_e2e.gate_row(gates, repo, rec)
    if g.get("wall_s") is not None:
        st["  gate"] = [float(g["wall_s"]), g.get("cpu_s")]
    if rec.get("slot_wait_s") is not None:
        st["    slot wait"] = [float(rec["slot_wait_s"]), None]
    for klass, secs in (rec.get("slot_wait_by_class") or {}).items():
        st[SLOT_CLASS + str(klass)] = [float(secs), None]
    parents: dict[str, str] = {}
    for s in gate_steps(found, kept_timings(q, last, rec)):
        if not s["parent"]:
            st[STEP + s["label"]] = [s["wall"], s["cpu"]]
            continue
        name = (NESTED if s["parent"] != "?" else UNPLACED) + s["label"]
        st[name] = [s["wall"], s["cpu"]]
        if s["parent"] != "?":
            parents[name] = STEP + s["parent"]
    for name, secs in phases(found).items():
        st[PHASE_ROW + name] = [secs, None]
        parents[PHASE_ROW + name] = NESTED + "coverage"
    if run is not None and "  gate" in st:
        st["  lander other"] = [run - st.get("  pre-land", [0.0])[0] - st["  gate"][0], None]
    return {"item": item, "ref": last.stem, "mode": land_e2e.INSTRUMENTED if mode else
            land_e2e.PLAIN, "start": start, "load": rec.get("load_start"), "stages": st,
            "parents": parents, "end": end, "repo": rec.get("repo"),
            "landed": rec.get("landed") or rec.get("sha"),
            "batch": (rec.get("batch") or {}).get("id"),
            "pre_affected": rec.get("pre_affected"), "pre_run": rec.get("pre_run"),
            "kind": land_spd.lander_kind(found),
            "shadow": land_spd.shadows(gates, repo, chain), "shadowed": "shadow_f" in g,
            "decisions": land_spd.decisions(found)}  # fmt: skip


def lands(q: Path, gates: list[dict]) -> list[dict]:
    """Every landed item's profile, a batch's shared runs counted once (`land_spd.once`)."""
    out, prev = [], {}
    for item, chain in chains(q):
        r = land(q, item, chain, gates, prev.get(item))
        prev[item] = land_e2e._when(chain[-1][1].get("finished"))
        if r is not None:
            out.append(r)
    return land_spd.once(out)


def known_parents(rows: list[dict]) -> dict[str, str]:
    """Each nested row's parent, as any land's timings file gave it."""
    parents: dict[str, str] = {}
    for r in rows:
        parents.update(r.get("parents") or {})
    return parents


def _placed(group: list[dict], known: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
    """GROUP with each log-table row (`? X`) moved under the parent another land's timings gave."""
    parents = dict(known)
    for r in group:
        parents.update(r.get("parents") or {})
    out = []
    for r in group:
        st = {}
        for n, v in r["stages"].items():
            moved = NESTED + n.removeprefix(UNPLACED)
            st[moved if n.startswith(UNPLACED) and moved in parents else n] = v
        out.append({**r, "stages": st})
    return out, parents


def _ordered(names: list[str], parents: dict[str, str], median: dict[str, float]) -> list[str]:
    """The fixed stages, then each gate step by cost with its nested rows (and theirs) under it."""
    kids: dict[str, list[str]] = {}
    for n in names:
        if n in parents:
            kids.setdefault(parents[n], []).append(n)
    out = []
    for n in (n for n in ORDER if n in names):
        out.append(n)
        if n == "    slot wait":  # each class's wait under the whole, in rank order
            out += [SLOT_CLASS + c for c in SLOT_CLASSES if SLOT_CLASS + c in names]

    def put(n: str) -> None:
        out.append(n)
        for k in sorted(kids.get(n, []), key=lambda k: -median[k]):
            put(k)

    tops = [n for n in names if n not in out and (n.startswith(STEP) or (
            n not in ORDER and n not in parents and n != "  lander other"
            and not n.startswith(UNPLACED)))]  # fmt: skip
    for n in sorted(tops, key=lambda n: -median[n]):
        put(n)
    out += sorted((n for n in names if n.startswith(UNPLACED)), key=lambda n: -median[n])
    out += [n for n in names if n not in out]  # an orphan: a child whose parent never ran here
    return out


def table(group: list[dict], min_s: float = 0.0, known: dict[str, str] | None = None) -> list[dict]:
    """One row per stage of GROUP's lands: n, median/max wall, median cpu, share, median load. A
    stage whose max wall is under MIN_S is left out (the fixed stages never are)."""
    group, parents = _placed(group, known or {})
    total = sum(r["stages"]["e2e"][0] for r in group) or 1.0
    names: list[str] = []
    for r in group:
        names += [n for n in r["stages"] if n not in names]
    rows: dict[str, dict] = {}
    for n in names:
        have = [r for r in group if n in r["stages"] and r["stages"][n][0] is not None]
        if not have:
            continue
        walls = [r["stages"][n][0] for r in have]
        cpus = [r["stages"][n][1] for r in have if r["stages"][n][1] is not None]
        loads = [float(r["load"]) for r in have if r.get("load") is not None]
        rows[n] = {"stage": n, "n": len(have), "median_s": statistics.median(walls),
                   "max_s": max(walls), "cpu_s": statistics.median(cpus) if cpus else None,
                   "share": sum(walls) / total,
                   "load": statistics.median(loads) if loads else None}  # fmt: skip
    order = _ordered(list(rows), parents, {n: r["median_s"] for n, r in rows.items()})
    return [
        rows[n] for n in order if n in ORDER or n == "  lander other" or rows[n]["max_s"] >= min_s
    ]


def groups(rows: list[dict], normal_since: dt.datetime | None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        tag = (
            "" if normal_since is None else (" normal" if r["start"] >= normal_since else " loaded")
        )
        out.setdefault(r["mode"].removeprefix("land e2e ") + tag, []).append(r)
    return dict(sorted(out.items()))


def _fmt(s: float | None) -> str:
    if s is None:
        return "-"
    return f"{s / 60:.1f} min" if abs(s) >= 600 else f"{s:.0f} s"


def render(by: dict[str, list[dict]], min_s: float = 0.0) -> str:
    out = []
    known = known_parents([r for g in by.values() for r in g])
    for name, group in by.items():
        out.append(f"── {name}: {len(group)} lands ({', '.join(r['item'] for r in group)})")
        out.append(f"{'stage':<58} {'n':>3} {'median':>9} {'max':>9} {'cpu-s':>8} {'share':>6} "
                   f"{'load':>6}")  # fmt: skip
        for t in table(group, min_s, known):
            cpu = "-" if t["cpu_s"] is None else f"{t['cpu_s']:.0f}"
            load = "-" if t["load"] is None else f"{t['load']:.1f}"
            out.append(f"{t['stage'][:58]:<58} {t['n']:>3} {_fmt(t['median_s']):>9} "
                       f"{_fmt(t['max_s']):>9} {cpu:>8} {t['share']:>6.0%} {load:>6}")  # fmt: skip
    return "\n".join(out) if out else "no landed land in the queue's records"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh land-profile", description=__doc__.splitlines()[0])
    ap.add_argument(
        "--since",
        help="only lands started at or after this ISO time, or (a commit) "
        "whose landed commit descends from it; prints the SPD figures for them",
    )
    ap.add_argument(
        "--last",
        type=int,
        metavar="N",
        help="print the SPD figures of the last N "
        "lands: commit to landed, whole pre-land re-runs, the pre-step's wait/run",
    )
    ap.add_argument(
        "--normal-since", help="split lands at this ISO time: loaded before, normal after"
    )
    ap.add_argument(
        "--min-s", type=float, default=5.0, help="hide a step whose max wall is under this"
    )
    ap.add_argument("--json", action="store_true", help="the same tables as JSON")
    ap.add_argument("--guards", action="store_true", help="red lands counted by refusing guard")
    ap.add_argument("--landq", help="the landing queue dir (default $GOH_LANDQ_DIR)")
    a = ap.parse_args(argv)
    q = Path(a.landq or os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")
    if a.guards:
        import land_check

        tally = land_check.count(q)
        print(json.dumps(tally, indent=2) if a.json else "\n".join(
            f"{n:5d}  {g}" for g, n in tally.items()) or "no red land names its guard")  # fmt: skip
        return 0
    gates = [r for r in timing_ledger.read() if r.get("kind") == "land"]
    rows = lands(q, gates)
    since = land_e2e._when(a.since) if a.since else None
    sha = None if since else land_spd_sha(rows, a.since)
    if (a.since and since is None and sha is None) or (
        a.normal_since and land_e2e._when(a.normal_since) is None
    ):
        ap.error("--since takes an ISO time, e.g. 2026-11-18T14:55Z, or a commit of the landed "
                 "repo; --normal-since an ISO time")  # fmt: skip
    rows = [r for r in rows if since is None or r["start"] >= since]
    figures = {}
    if a.last:
        figures[f"last {a.last}"] = land_spd.summary(
            q, sorted(rows, key=lambda r: r["end"])[-a.last :]
        )
    if sha:
        rows = [r for r in rows if land_spd.descends(r["repo"], sha, r["landed"])]
        figures[f"since {a.since}"] = land_spd.summary(q, rows)
    by = groups(rows, land_e2e._when(a.normal_since) if a.normal_since else None)
    audits = bin_reach_audit.audits()  # GOH.BINREACH: the reach's audit, a number
    if a.json:
        known = known_parents(rows)
        tables = {k: table(v, a.min_s, known) for k, v in by.items()}
        reach = {"audits": len(audits), "disagreements": bin_reach_audit.disagreements(audits)}
        spd = {"spd": figures, "binreach": reach} if figures else {}
        print(json.dumps({**tables, **spd}, indent=2))
    else:
        print(render(by, a.min_s))
        print("\n".join(land_spd.render(k, v) for k, v in figures.items()))
        print(bin_reach_audit.audit_line(audits))
    return 0


def land_spd_sha(rows: list[dict], ref: str | None) -> str | None:
    """The commit REF names in a landed repo, for `--since`; None when it names none."""
    for repo in dict.fromkeys(r["repo"] for r in rows if r.get("repo")):
        if ref and (got := land_spd.resolves(repo, ref)):
            return got
    return None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
