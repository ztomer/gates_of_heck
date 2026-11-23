#!/usr/bin/env python3
"""A day of gate admissions replayed under two rules: a count of gates, or a CPU budget.

    python3 lib/admit_replay.py [--day YYYY-MM-DD] [--rule count:2] [--rule cpu:16]
                                [--cores N] [--background CORES] [--scale S] [--json]

THE QUESTION (GOH.CPUADMIT, 2026-11-17 review). Admission counts gates (`GOH_GATE_SLOTS`, 2 on 16
cores), and a goh land gate burns ~6 cores: is a count the wrong unit, and would admitting each
gate by its CPU weight from the one timing record (lib/capacity.py `gate_weight`) do better?
The ruling is docs/decisions/D-0009; this is the instrument it was decided with.

THE JOBS are the day's `push` and `land` rows of the timing record (lib/timing_ledger.py) that carry
a CPU reading. A job's ARRIVAL is its span's start (epoch - wall_s): a push gate's span starts after
its slot, so the wait it records (`slot_wait_s`, since GOH.LOADSOURCES; none before, where the
replay's waits are a LOWER bound) is taken off its arrival; a land's span starts at the land lock,
before the slot, so the slot wait the queue recorded for that sha (`$GOH_LANDQ_DIR/done/*.json`
`slot_wait_s`) is taken off its run. Its WORK is cpu_s; its DEMAND is cpu_s over the run (the cores
it actually burned, x `--scale` for the cores it would burn on an idle box, which the record cannot
see); its WEIGHT, what the CPU rule admits it by, is the median cpu-s per wall-s of its repo's runs
BEFORE it (every repo's when it has none; cores / slots, which is what a count assumes, when there
are none) -- never a later run.

THE MACHINE is a fluid share: `cores` cores, `background` cores of other work always runnable, and
while the demand (background + every running job's) exceeds the cores each job runs at demand x
cores / total. THE QUEUE is first come first served, as the slots' tickets are (lib/gate_lanes.py):
`count:N` admits the head while fewer than N run; `cpu:B` admits the head while the running weights
plus its own stay within B cores, and always when nothing runs (a gate heavier than the budget runs
alone, never starves).

THE FIGURES per rule: wait_s (summed queue time), gate_s (summed run time: a gate's wall), lane_s
(their sum: what the lanes spent from arrival to verdict), makespan_s, and peak concurrency.
Pinned by tests/test_admit_replay.py.
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
import math
import os
import statistics
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_records  # lib/: the land records' one door (GOH.STATEDB)
import timing_ledger

KINDS = ("push", "land")
EPS = 1e-9


@dataclass(frozen=True)
class Job:
    name: str
    arrive: float
    work: float  # cpu-s
    demand: float  # cores it burns when it has them
    weight: float  # cores the CPU rule admits it as


@dataclass(frozen=True)
class Rule:
    kind: str  # count | cpu
    limit: float

    @classmethod
    def parse(cls, text: str) -> Rule:
        kind, _, raw = text.partition(":")
        try:
            limit = float(raw)
        except ValueError:
            raise ValueError(f"rule {text!r}: want count:N or cpu:CORES") from None
        if kind not in ("count", "cpu") or not math.isfinite(limit) or limit <= 0:
            raise ValueError(f"rule {text!r}: want count:N or cpu:CORES, N and CORES over 0")
        return cls(kind, limit)

    def __str__(self) -> str:
        return f"{self.kind}:{self.limit:g}"

    def admits(self, running: list[Job], head: Job) -> bool:
        if not running:
            return True
        if self.kind == "count":
            return len(running) < self.limit
        return sum(j.weight for j in running) + head.weight <= self.limit + EPS


@dataclass
class Result:
    rule: str
    jobs: int = 0
    wait_s: float = 0.0
    gate_s: float = 0.0
    lane_s: float = 0.0
    makespan_s: float = 0.0
    peak: int = 0
    per_job: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "per_job"}


def replay(jobs: Iterable[Job], rule: Rule, *, cores: float, background: float = 0.0) -> Result:
    """Run `jobs` through `rule` on the fluid machine; every figure rounded to 0.1 s."""
    pending = sorted(jobs, key=lambda j: j.arrive)
    out = Result(str(rule), jobs=len(pending))
    if not pending:
        return out
    queue: list[Job] = []
    running: dict[int, tuple[Job, float, float]] = {}  # id -> (job, work left, admitted at)
    t = t0 = pending[0].arrive
    i = 0
    while i < len(pending) or queue or running:
        while i < len(pending) and pending[i].arrive <= t + EPS:
            queue.append(pending[i])
            i += 1
        while queue and rule.admits([r[0] for r in running.values()], queue[0]):
            j = queue.pop(0)
            running[id(j)] = (j, j.work, t)
            out.wait_s += t - j.arrive
        out.peak = max(out.peak, len(running))
        total = background + sum(r[0].demand for r in running.values())
        share = 1.0 if total <= cores else cores / total
        rates = {k: r[0].demand * share for k, r in running.items()}
        step = math.inf
        for k, (_, left, _) in running.items():
            step = min(step, left / rates[k])
        if i < len(pending):
            step = min(step, pending[i].arrive - t)
        if step == math.inf:  # nothing runs and nothing arrives: only a zero-demand job is left
            raise ValueError("a job with no demand never ends")
        t += step
        for k in list(running):
            j, left, since = running[k]
            left -= rates[k] * step
            if left <= EPS * max(1.0, j.work):
                del running[k]
                out.gate_s += t - since
                out.per_job.append({"name": j.name, "wait_s": round(since - j.arrive, 1),
                                    "run_s": round(t - since, 1)})  # fmt: skip
            else:
                running[k] = (j, left, since)
    out.makespan_s = t - t0
    out.lane_s = out.wait_s + out.gate_s
    for name in ("wait_s", "gate_s", "lane_s", "makespan_s"):
        setattr(out, name, round(getattr(out, name), 1))
    return out


def _ratio(row: dict) -> float | None:
    cpu, wall = row.get("cpu_s"), row.get("wall_s")
    if isinstance(cpu, (int, float)) and isinstance(wall, (int, float)) and wall > 0 and cpu > 0:
        return cpu / wall
    return None


def jobs_from(
    rows: list[dict],
    *,
    slot_waits: dict[str, float] | None = None,
    unknown_weight: float = 8.0,
    scale: float = 1.0,
) -> list[Job]:
    """The replayable jobs of `rows` (push and land rows with a CPU reading), oldest first."""
    waits = slot_waits or {}
    keyed = []
    for r in rows:
        if r.get("kind") not in KINDS or _ratio(r) is None:
            continue
        start = float(r["epoch"]) - float(r["wall_s"])
        if r["kind"] == "push":  # its span starts after the slot; the wait it recorded comes first
            start -= float(r.get("slot_wait_s") or 0.0)
        keyed.append((start, r))
    keyed.sort(key=lambda p: p[0])
    seen: list[tuple[float, str, float]] = []  # (end, repo, ratio) of every row, for the weights
    for r in rows:
        if (ratio := _ratio(r)) is not None and r.get("kind") in KINDS:
            seen.append((float(r["epoch"]), timing_ledger.repo_name(str(r.get("repo", ""))), ratio))
    jobs = []
    for start, r in keyed:
        repo = timing_ledger.repo_name(str(r.get("repo", "")))
        slot = waits.get(str(r.get("sha", ""))[:12], 0.0) if r["kind"] == "land" else 0.0
        run = max(float(r["wall_s"]) - slot, 0.1)
        before = [(end, rp, x) for end, rp, x in seen if end <= start]
        own = [x for _, rp, x in before if rp == repo]
        pool = own or [x for _, _, x in before]
        weight = statistics.median(pool[-10:]) if pool else unknown_weight
        jobs.append(Job(f"{repo} {r['kind']}", start, float(r["cpu_s"]),
                        float(r["cpu_s"]) / run * scale, weight))  # fmt: skip
    return jobs


def landq_slot_waits(queue: Path) -> dict[str, float]:
    """sha[:12] -> the slot wait the landing queue recorded for that land (none when unrecorded)."""
    out: dict[str, float] = {}
    for _, rec in land_records.records(queue):
        wait = rec.get("slot_wait_s")
        if isinstance(wait, (int, float)) and rec.get("sha"):
            out[str(rec["sha"])[:12]] = float(wait)
    return out


def day_rows(rows: list[dict], day: str) -> list[dict]:
    """The rows whose span STARTED on UTC `day`."""
    out = []
    for r in rows:
        try:
            start = float(r["epoch"]) - float(r["wall_s"])
        except (KeyError, TypeError, ValueError):
            continue
        if dt.datetime.fromtimestamp(start, dt.UTC).date().isoformat() == day:
            out.append(r)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--day", default=dt.datetime.now(dt.UTC).date().isoformat(), help="UTC day")
    ap.add_argument("--rule", action="append", default=[], help="count:N or cpu:CORES")
    ap.add_argument("--cores", type=float, default=16.0, help="the box replayed (default 16)")
    ap.add_argument("--background", type=float, default=0.0, help="cores of other work")
    ap.add_argument("--scale", type=float, default=1.0, help="idle-box demand over recorded")
    ap.add_argument("--ledger", type=Path, default=None, help="the timing record")
    ap.add_argument("--landq", type=Path, default=None, help="the landing queue's directory")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rules = [Rule.parse(r) for r in (args.rule or ["count:2", f"cpu:{args.cores:g}"])]
    queue = args.landq or Path(
        os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq"
    )
    rows = day_rows(timing_ledger.read(args.ledger), args.day)
    slots = next((r.limit for r in rules if r.kind == "count"), 2.0)
    jobs = jobs_from(rows, slot_waits=landq_slot_waits(queue), unknown_weight=args.cores / slots,
                     scale=args.scale)  # fmt: skip
    results = [replay(jobs, r, cores=args.cores, background=args.background) for r in rules]
    if args.json:
        print(json.dumps({"day": args.day, "jobs": len(jobs), "cores": args.cores,
                          "background": args.background, "scale": args.scale,
                          "results": [r.summary() for r in results]}, sort_keys=True))  # fmt: skip
        return 0
    print(f"{args.day}: {len(jobs)} gates, {args.cores:g} cores, background {args.background:g}, "
          f"scale {args.scale:g}")  # fmt: skip
    for r in results:
        print(
            f"  {r.rule:<9} wait {r.wait_s:>8.0f} s  gate {r.gate_s:>8.0f} s  "
            f"lane {r.lane_s:>8.0f} s  makespan {r.makespan_s:>7.0f} s  peak {r.peak}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
