"""lib/land_judge.py -- main's code over what a landed lane changed, reported and never a refusal
(GOH.ONEGATE, docs/decisions/D-0012).

    land_judge.py report [--last N] [--days N] [--json]   the false-red rate, and what was named
    land_judge.py rule TIP STEP false|true WHY            a would-be red, ruled
    land_judge.py continue                                the report is read: judge the next sample
    land_judge.py drain --main M                          judge every waiting land

ONE GATE A LAND, under the tip's code. A gate's steps are `./gates/...` run in the tip's tree, so a
second gate under main's runner ran the lane's own steps and judged nothing of them. This judge
runs main's version of each step whose closure the lane changed (lib/land_judge_steps.py) and keeps
its verdict beside the tip's (lib/land_judge_rows.py).

OUTSIDE THE LANDER'S LOCK. The second gate it replaces cost a land 16 s (p50); main's versions cost
more (the report's seconds), so the land only writes the waiting row (lib/land_judge_owe.py), and a
session of its own judges it after the lock is gone, in a kept worktree at the tip. One drain at a
time (the `land_judge` lock, never waited on): a second leaves its row to the first.

SPARE WORK. Each step starts only when the box has room and no land is on its way
(lib/land_judge_room.py), in a gate slot of its own (class `work`), and no slot is held in the wait:
the row says `waiting`. A land that had no room for AGE_S reads `unjudged: no room` and is not run.
A SAMPLE, not every land: after GOH_LAND_JUDGE_SAMPLE judged lands the drain stops and later ones
read `unjudged: the sample is full`, until `continue` says the report has been read.

NEVER SILENT. A land whose judge could not run is `unavailable` by its reason, a step that gave no
verdict likewise, a row whose judge died is settled as unavailable by the next, and a landed tip
with no row at all is counted by the report. A red land is judged on the steps its own gate had red
(`broken`: main's version is green there).
Pinned by tests/test_land_judge.py and tests/test_land_driver.py.

GOH.ONEGATE: main's code over what a landed lane changed, reported and never a refusal: `drain`,
`report` (the false-red rate), `rule`. Invoked by `land_judge_owe.py`.
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
import functools
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "lib"), str(ROOT)]
import estate_registry  # noqa: E402 -- lib/ and the root, on the path from the line above
import full_run  # noqa: E402
import goh_db  # noqa: E402
import land_judge_room as room  # noqa: E402
import land_judge_rows as rows  # noqa: E402
import land_judge_steps as steps  # noqa: E402
import lock_order  # noqa: E402
import small_stores  # noqa: E402
import span_store  # noqa: E402
import state_paths  # noqa: E402
import tmp_root  # noqa: E402

from tui.lib import counted, err, info, ok, warn  # noqa: E402

EXPORT = '. "$1/gates/_from_head.sh"; goh_export_rev "$2" "$3" "$3" && printf %s "$_goh_export"'

AGE_S = 24 * 3600.0  # a land waits this long for room
SAMPLE, DEFAULT = "GOH_LAND_JUDGE_SAMPLE", 20
NO_ROOM, FULL = "no room", "the sample is full"


def home() -> Path:
    return state_paths.landq_dir() / "judge"


def export(main: Path, rev: str) -> Path:
    """The export of REV's gates, as a land's own (gates/_from_head.sh); raises with why not."""
    env = {**os.environ, "GOH_PY": os.environ.get("GOH_PY") or sys.executable}
    done = subprocess.run(["bash", "-c", EXPORT, "export", str(ROOT), str(main), rev], env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
                          timeout=300)  # fmt: skip
    if done.returncode or not done.stdout:
        raise LookupError(f"no export of {rev[:12]}: {done.stderr.strip()[-200:]}")
    return Path(done.stdout)


def estate(scratch: Path) -> None:
    """The canary's own copy of the estate's registry and of its state, whose consumer verdicts it
    may reuse: what main's copy of it records is never a ship's."""
    registry, state = estate_registry.registry_path(), estate_registry.state_file()
    if registry.is_file():
        shutil.copy(registry, scratch / registry.name)
    if (old := small_stores.get(state)) is not None and not os.environ.get("GOH_DB"):
        small_stores.put(scratch / steps.STATE, old)


def sample() -> int:
    """How many lands are judged before the report must be read (GOH_LAND_JUDGE_SAMPLE)."""
    raw = os.environ.get(SAMPLE, "").strip()
    if raw and not raw.isdigit():
        warn(f"{SAMPLE}={raw!r} is no whole number: {DEFAULT}")
    return int(raw) if raw.isdigit() else DEFAULT


def one(a: argparse.Namespace, key: rows.Key, rc: int, wait: Callable[[], None],
        clock=time.monotonic) -> None:  # fmt: skip
    """One land judged, its rows written: the failure of any part is its `unavailable`."""
    (_, sha, base), t0 = key, clock()
    scratch = Path(tempfile.mkdtemp(prefix="goh-land-judge.", dir=tmp_root.goh_tmp()))
    run = steps.InSlot(home() / "wt", scratch, wait)
    found: list[rows.Step] = []
    why = ""
    try:
        run.waiting()  # the worktree and the exports are work too
        if said := full_run.prepare(a.main, run.tree, sha):
            raise LookupError(f"no worktree at {sha[:12]}: {said}")
        main, tip = export(a.main, base), export(a.main, sha)
        estate(scratch)
        found = steps.judge(steps.choose(main, tip), main, tip, base, scratch,
                            rows.owed_steps(key), run, landed=rc == 0,
                            clock=lambda: run.ran)  # fmt: skip
    except room.NoRoom as e:
        rows.unjudge(key, f"{NO_ROOM}: {e}"[:300])
        return
    except Exception as e:  # noqa: BLE001 -- its own failure is a row, never silence
        why = f"{e.__class__.__name__}: {e}"[:300]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    rows.end(key, found, why, max(0.0, round(clock() - t0 - run.waited, 2)), time.time())
    for s in found:
        if s.outcome in rows.NAMED:
            warn(f"judge: {sha[:8]}: {s.step}: {s.outcome}: {s.reason}")
    if why:
        err(f"judge: {sha[:8]}: unavailable: {why}")


def told(key: rows.Key, why: str) -> None:
    """KEY's row follows its wait; this code's export stays in use while it waits."""
    if why:
        rows.wait(key, f"{NO_ROOM}: {why}"[:300])
    else:
        rows.begin(key, time.time())
    used = ROOT / ".goh-used"  # gates/_head_cache.py prunes an export by its last use
    if used.is_file():
        used.touch()


def drain(a: argparse.Namespace, run=one, clock=time.time) -> int:
    """Waiting lands judged, oldest first, while the sample has room for one; another drain of this
    home has it: nothing."""
    home().mkdir(parents=True, exist_ok=True)
    fd = os.open(home() / "lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            lock_order.flock(fd, "land_judge", wait=False)  # never waits
        except BlockingIOError:
            return 0
        rows.settle(a.repo)  # the lock is ours: a row still running is a dead judge's
        while True:
            rows.expire(a.repo, clock() - AGE_S, NO_ROOM)
            if rows.judged(a.repo) >= sample():
                rows.expire(a.repo, None, FULL)
                return 0
            if (got := rows.claim(a.repo)) is None:
                return 0
            key, rc, at = got
            run(a, key, rc, room.Wait(at + AGE_S, functools.partial(told, key)))
    finally:
        os.close(fd)


def no_room(lands: list[dict], now: float) -> str:
    """The report's plain word when the box had room for no land of the last AGE_S, or ""."""
    starved = [x for x in lands if x["at"] <= now - AGE_S and x["status"] == "unjudged"
               and str(x["why"]).startswith(NO_ROOM)]  # fmt: skip
    ran = [x for x in lands if max(x["started"] or 0, x["ended"] or 0) > now - AGE_S]
    if not starved or ran:
        return ""
    why = next((x["why"] for x in lands if str(x["why"]).startswith(f"{NO_ROOM}: ")), NO_ROOM)
    return f"the box has had no room for {AGE_S / 3600:g} h: no land was judged ({why})"


def summary(lands: list[dict], unrowed: list[str]) -> dict:
    """The report's numbers. A JUDGED land is one main's code gave at least one verdict on; a land
    with a step unavailable is in `partly` too, so a partial judging never reads as a whole one."""
    of = {s: [x for x in lands if x["status"] == s]
          for s in ("same", "waiting", "running", "done", "unavailable", "unjudged")}  # fmt: skip
    flat = [(x, s) for x in lands for s in x["steps"]]
    counts: dict[str, int] = {}
    for _, s in flat:
        counts[s["outcome"]] = counts.get(s["outcome"], 0) + 1
    ran = [x for x in of["done"] if any(s["main_rc"] is not None for s in x["steps"])]
    reds = [(x, s) for x, s in flat if s["outcome"] == "would_red"]
    tips = lambda which: {x["tip"] for x, s in reds if s["ruled"] == which}  # noqa: E731
    cost = sorted(x["seconds"] for x in of["done"])
    return {
        "lands": len(lands), **{k: len(v) for k, v in of.items()}, "no_row": len(unrowed),
        "judged": len(ran), "partly": sum(any(s["outcome"] == "unavailable" for s in x["steps"])
                                          for x in of["done"]),
        "steps": counts, "would_red": len({x["tip"] for x, _ in reds}),
        "false": len(tips("false")), "true": len(tips("true")), "unruled": len(tips(None)),
        "cost_p50_s": round(statistics.median(cost), 1) if cost else None,
        "cost_max_s": cost[-1] if cost else None,
    }  # fmt: skip


def report(a: argparse.Namespace, clock=time.time) -> int:
    since = clock() - a.days * 86400
    try:
        size, took = sample(), rows.judged(a.repo)
        lands = rows.lands(a.repo, a.last, since)
        for x in lands:  # past its bound with no drain to say so
            if x["status"] == "waiting" and x["at"] <= clock() - AGE_S:
                x.update(status="unjudged", why=x["why"] or NO_ROOM)
        none = rows.unrowed(a.repo, min(x["at"] for x in lands)) if lands else []
    except (OSError, sqlite3.Error) as e:
        err(f"land judge: unavailable: {e}")
        return 1
    if not lands:
        warn(f"land judge: no land of {a.repo} is recorded in {a.days:g} days: its rate is unknown")
        return 1
    n = {**summary(lands, none), "sample": size, "sampled": took, "sample_full": took >= size,
         "no_room": no_room(lands, clock())}  # fmt: skip
    if a.json:
        print(json.dumps({**n, "named": [{"tip": x["tip"], **s} for x in lands for s in x["steps"]
                                         if s["outcome"] in rows.NAMED]}))  # fmt: skip
        return 0
    info(f"land judge: {counted(n['lands'], 'land')}: {n['same']} changed no gate code, "
         f"{n['done']} judged ({n['partly']} in part), {n['unavailable']} unavailable, "
         f"{n['waiting']} waiting, {n['running']} running, {n['unjudged']} unjudged, "
         f"{n['no_row']} with no row")  # fmt: skip
    info("  steps: " + ", ".join(f"{v} {k}" for k, v in sorted(n["steps"].items())))
    if n["judged"]:
        rate = f"{n['false']}/{n['judged']} = {100 * n['false'] / n['judged']:.0f}%"
        info(f"  would-be red: {n['would_red']} of {counted(n['judged'], 'judged land')}; ruled "
             f"false {n['false']}, true {n['true']}, unruled {n['unruled']}; false-red rate {rate}"
             + (f", at most {n['false'] + n['unruled']}/{n['judged']}" if n["unruled"] else "")
             )  # fmt: skip
        info(f"  cost: p50 {n['cost_p50_s']} s, max {n['cost_max_s']} s, outside the land lock")
    else:
        warn("  no land judged yet: the false-red rate is unknown")
    if n["sample_full"]:
        warn(f"  the sample is full: {took} of {size} judged ({SAMPLE}); no land is judged until "
             "the next sample is asked for: python3 lib/land_judge.py continue")  # fmt: skip
    else:
        info(f"  sample: {took} of {size} judged since it was last read ({SAMPLE})")
    if n["no_room"]:
        warn(f"  {n['no_room']}")
    for why in sorted({x["why"] or "" for x in lands if x["status"] == "unjudged"}):
        m = sum(x["status"] == "unjudged" and (x["why"] or "") == why for x in lands)
        info(f"  {counted(m, 'land')} unjudged: {why}")
    for x in lands:
        if x["status"] == "unavailable":
            warn(f"  {x['tip'][:8]} unavailable: {x['why']}")
        for s in x["steps"]:
            if s["outcome"] in (*rows.NAMED, "flaky", "unavailable"):
                say = warn if s["outcome"] in rows.NAMED else info
                ruled = f" (ruled {s['ruled']})" if s["ruled"] else ""
                say(f"  {x['tip'][:8]} {s['outcome']}{ruled}: {s['step']}: {s['reason']}")
    return 0


def go_on(a: argparse.Namespace, clock=time.time) -> int:
    rows.read(a.repo, clock())
    ok(f"land judge: the sample is read; the next {counted(sample(), 'land')} with room are judged")
    return 0


def rule(a: argparse.Namespace) -> int:
    n = rows.rule(a.tip, a.step, a.said, a.why)
    if not n:
        err(f"land judge: no would-be red of {a.tip} in `{a.step}`")
        return 1
    ok(f"land judge: {counted(n, 'would-be red')} of {a.tip[:8]} ruled {a.said}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="do", required=True)
    p = sub.add_parser("drain")
    p.add_argument("--main", type=Path, required=True)
    sub.add_parser("continue")
    p = sub.add_parser("report")
    p.add_argument("--last", type=int, default=20, help="judged lands")
    p.add_argument("--days", type=float, default=14.0)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("rule")
    p.add_argument("tip")
    p.add_argument("step")
    p.add_argument("said", choices=("false", "true"))
    p.add_argument("why")
    a = ap.parse_args(argv)
    a.repo = span_store.key(a.main.name if getattr(a, "main", None) else span_store.GOH_NAME)
    try:
        return {"drain": drain, "report": report, "rule": rule, "continue": go_on}[a.do](a)
    except (OSError, sqlite3.Error, subprocess.SubprocessError, LookupError, goh_db.Uncut) as e:
        err(f"land judge: unavailable: {a.do}: {e.__class__.__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
