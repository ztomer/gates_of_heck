#!/usr/bin/env python3
"""lib/step_timings.py -- one JSON line per gate step, and the report that reads them.

    GOH_TIMINGS=/tmp/t.jsonl tools/gate.sh --full     # every step appends a line
    python3 lib/step_timings.py report /tmp/t.jsonl   # top steps, and totals per label

WHY. `GOH_TIME` printed whole seconds on the ok line, and every cost the 2026-10-05 measurement
found was either under a second (a 0.2 s poll per step, 44 ms per interpreter start) or spread
across 29 crates where no single line looks expensive (a 7 s crates.io lookup, once per crate).
Neither shows up in a per-line seconds counter. The fix is data: one line per step with
milliseconds, the step that contains it, and the directory it ran in, so the same label can be
summed across every crate of a repo. Without it a perf change has no before or after.

WHO WRITES. `lib/bounded_run.py`, which every bounded step of both tiers and every `local_ci.sh`
step already passes through, and the native tier for its in-process steps. Nested gates nest:
`bounded_run.py` hands its child `GOH_TIMINGS_PARENT=<label>`, so a `rust_gate.sh` step inside a
`local_ci.sh` step names its parent.

KIND (BACKLOG 5.3). A gate declares what its steps are (`GOH_TIMINGS_KIND`: `structural.sh`
native, `rust_gate.sh` rust) and every row records it. Each kind's figure is the one that held across
a 2x load on this box (three A-B-B-A profiles of this repo's push gate, 2026-10-14):
  rust   -- CPU over the rust control (`lib/step_control.py`, run by `tools/gate_profile.sh` before
            the gate and after it): clippy's ratio moved +1% and +2% where its raw CPU moved +17%.
            A kind without a control on both sides is uncalibrated, and a control that drifted more
            than CONTROL_DRIFT between the two voids its kind: the load moved under the run. Rows
            that carry their end time (`t`) are matched instead: the gate is also sampled while it
            runs, and each step is divided by the median of the controls around it, void only when
            fewer than MEDIAN_OF of them spread more than CONTROL_DRIFT (GOH.RUSTCONTROLVOID: the
            two ends voided 27% of 115 lands, following the box's load move, r 0.57).
  native -- raw CPU (RAW_KINDS): the native tier's in-process steps moved +4% and -1%, and every
            control tried moved more than the step did (a serial shellcheck -15%, four at once -17%),
            so a ratio would carry the control's drift, not remove the step's.

CONCURRENCY. One `os.write` of one line under `O_APPEND`, shorter than `PIPE_BUF`: concurrent
writers (a 4-way crate fan-out) interleave whole lines, never halves. A write that fails is
dropped silently ON PURPOSE -- the instrument must never be the reason a gate goes red.

The P0 perf instrument: `record()` appends one JSON line per step to `$GOH_TIMINGS` (ms, rc, tier,
parent label, cwd, `load1` the load at its start), called by `bounded_run.py` and, for its
in-process steps, by the native tier (`step_report.rs`); `report FILE` prints the slowest steps and
each label summed across every place it ran; `time LABEL -- CMD` makes a phase that is no bounded
step one row its children nest under (`tools/release_goh.sh`). Pinned by `test_goh_structural.py`.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import counted  # the count meets its noun (GOH.HEDGEOWN)

# json and collections load where they are used: `record` is on every gate step's path
# (lib/bounded_run.py), and a no-op record must not pay for an encoder it never calls.

ENV = "GOH_TIMINGS"
PARENT_ENV = "GOH_TIMINGS_PARENT"
KIND_ENV = "GOH_TIMINGS_KIND"
CONTROL_DRIFT = 0.15  # BACKLOG 5.3's exit: a figure holds within 15%
MEDIAN_OF = 5  # the owner's rule for a cpu-s figure: the median of five holds whatever their spread
# Kinds whose raw CPU is the figure: no control is run or divided by.
RAW_KINDS = {"native": "raw CPU holds across loads for in-process work (BACKLOG 5.3)"}
_MAX_LINE = 4000  # under PIPE_BUF (4096 on macOS and Linux), so an append is atomic


def children_cpu_ms() -> float:
    """User + system CPU, in ms, of every child this process has reaped (and theirs): a delta
    around one step is that step's tree. Untimed, 0 without loading `resource`: this is on every
    gate step's path, like `record`."""
    if not os.environ.get(ENV):
        return 0.0
    import resource

    u = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (u.ru_utime + u.ru_stime) * 1000


def record(
    label: str,
    ms: float,
    rc: int,
    tier: str,
    cache: str = "",
    cpu_ms: float | None = None,
    kind: str = "",
    control: bool = False,
    load1: float | None = None,
) -> None:
    """Append one step's line to $GOH_TIMINGS; a no-op when it is unset. LOAD1 is the box's 1-min
    load average when the step STARTED (`load_now`): a wall time without it measures the box."""
    path = os.environ.get(ENV)
    if not path:
        return
    row = {
        "label": label[:300],
        "ms": round(ms, 1),
        "rc": rc,
        "tier": tier,
        "parent": os.environ.get(PARENT_ENV, "")[:300],
        # GOH_TIMINGS_CWD: where the step REALLY ran. `goh_step_in` changes directory inside the
        # child, so without it all 29 crates of a repo record the repo root.
        "cwd": (os.environ.get("GOH_TIMINGS_CWD") or os.getcwd())[:300],
        # The step's END on the wall clock: a sampled control is matched to the steps it ran
        # beside by time (`calibrated`, GOH.RUSTCONTROLVOID).
        "t": round(time.time(), 3),
    }
    if cache:
        row["cache"] = cache
    if cpu_ms is not None:
        row["cpu_ms"] = round(cpu_ms, 1)
    kind = kind or os.environ.get(KIND_ENV, "")
    if kind:
        row["kind"] = kind[:40]
    if control:
        row["control"] = True
    if load1 is not None:
        row["load1"] = round(load1, 2)
    import json

    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")[:_MAX_LINE]
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        pass


def load_now() -> float | None:
    """The 1-min load average at a step's start, when timings are on (GOH.SHIPPROFILE)."""
    if not os.environ.get(ENV):
        return None
    try:
        return os.getloadavg()[0]
    except OSError:
        return None


def timed(label: str, argv: list[str], tier: str = "phase", cwd: object = None) -> int:
    """Run ARGV (stdout, stderr inherited; in CWD, else here) as one recorded row LABEL whose
    children name it their parent: a phase that is no bounded step (a release's land, its push)
    still shows."""
    import subprocess
    import time

    t0, cpu0, load1 = time.monotonic(), children_cpu_ms(), load_now()
    try:
        rc = subprocess.run(
            argv, env=child_env(label), check=False, stdin=subprocess.DEVNULL, cwd=cwd
        ).returncode
    except OSError as e:
        print(f"step_timings: {label}: {e}", file=sys.stderr)
        rc = 127
    record(label, (time.monotonic() - t0) * 1000, rc, tier, cpu_ms=children_cpu_ms() - cpu0,
           load1=load1)  # fmt: skip
    return rc


def child_env(label: str) -> dict[str, str] | None:
    """The environment for a timed step's child: the parent label set and the gate's kind dropped
    (it labels this gate's steps; a gate the step runs declares its own, 5.3), or None to inherit."""
    if not os.environ.get(ENV) and KIND_ENV not in os.environ:
        return None
    env = dict(os.environ)
    env.pop(KIND_ENV, None)
    if os.environ.get(ENV):
        env[PARENT_ENV] = label
    return env


def load(path: str) -> list[dict]:
    import json

    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a truncated last line from a killed run: skip it, count the rest
    return rows


def report(rows: list[dict], top: int = 15) -> str:
    """Top steps by wall time, then the same label summed across every place it ran."""
    out = [f"{counted(len(rows), 'step')} recorded"]
    out.append(f"-- top {top} steps by wall time --")
    for r in sorted(rows, key=lambda r: -r["ms"])[:top]:
        where = os.path.basename(r.get("cwd", "")) or "."
        hit = f" [{r['cache']}]" if r.get("cache") else ""
        cpu = f" {r['cpu_ms'] / 1000:7.2f} s cpu" if "cpu_ms" in r else ""
        out.append(f"{r['ms'] / 1000:8.2f} s{cpu}  rc={r['rc']:<3} {where:<20} {r['label']}{hit}")
    from collections import defaultdict

    sums: dict[str, list[float]] = defaultdict(list)
    cpus: dict[str, float] = defaultdict(float)
    for r in rows:
        sums[r["label"]].append(r["ms"])
        cpus[r["label"]] += r.get("cpu_ms", 0.0)
    out.append(f"-- top {top} labels, summed over every run of the label --")
    for label, ms in sorted(sums.items(), key=lambda kv: -sum(kv[1]))[:top]:
        cpu = f" {cpus[label] / 1000:7.2f} s cpu" if cpus[label] else ""
        out.append(f"{sum(ms) / 1000:8.2f} s{cpu}  x{len(ms):<4} {label}")
    section = calibrated(rows)
    if section:
        out.append(section)
    return "\n".join(out)


def _gate_kinds(rows: list[dict]) -> dict[str, str]:
    """A step that runs a gate takes the gate's kind: the one kind every kinded row under it carries
    (`./gates/structural.sh --full`, a CI step, is native because structural.sh's steps are)."""
    under: dict[str, set[str]] = {}
    for r in rows:
        if r.get("kind") and not r.get("control"):
            under.setdefault(r.get("parent", ""), set()).add(r["kind"])
    return {label: next(iter(ks)) for label, ks in under.items() if label and len(ks) == 1}


def control_drifts(rows: list[dict]) -> dict[str, float]:
    """Each kind's control drift between the run's two ends: (last - first) / first of the kind's
    control CPU, for a kind with at least two controls. The one definition `calibrated` prints and
    the budget ledger records (lib/timing_ledger.py), so the two never disagree."""
    controls: dict[str, list[float]] = {}
    for r in rows:
        if r.get("control") and r.get("kind") and "cpu_ms" in r:
            controls.setdefault(r["kind"], []).append(r["cpu_ms"])
    return {
        kind: ((ctl[-1] - ctl[0]) / ctl[0] if ctl[0] else float("inf"))
        for kind, ctl in controls.items()
        if len(ctl) >= 2
    }


def worst_control_drift(rows: list[dict]) -> float | None:
    """The control drift furthest from zero, or None when no kind had a control on both sides."""
    drifts = [d for d in control_drifts(rows).values() if d != float("inf")]
    return max(drifts, key=abs) if drifts else None


def _timed(rows: list[dict]) -> bool:
    return all("t" in r for r in rows)


def _bracket(step: dict, ctl: list[dict]) -> tuple[list[dict], str]:
    """The controls around STEP, oldest first: the last one that ended before it started, every one
    that ended while it ran, and the first that ended after it; or why there is no bracket."""
    end = step["t"]
    start = end - step["ms"] / 1000
    before = [c for c in ctl if c["t"] <= start]
    inside = [c for c in ctl if start < c["t"] < end]
    after = [c for c in ctl if c["t"] >= end]
    if not before:
        return [], "no control before it"
    if not after:
        return [], "no control after it"
    return [before[-1], *inside, after[0]], ""


def _sampled(kind: str, steps: list[dict], ctl: list[dict], drift: float | None) -> list[str]:
    """A kind whose rows carry their time: each step over the median of the controls around it. A
    step with MEDIAN_OF or more takes their median; one with fewer is void when they spread more
    than CONTROL_DRIFT -- the load moved under it faster than the control was sampled."""
    import statistics

    ctl = sorted(ctl, key=lambda c: c["t"])
    median = statistics.median(c["cpu_ms"] for c in ctl)
    ends = f", control drift {drift:+.1%} end to end" if drift is not None else ""
    lines, held = [], 0
    for r in sorted(steps, key=lambda r: -r["cpu_ms"]):
        around, why = _bracket(r, ctl)
        if why:
            lines.append(f"    {r['label']}: unbracketed, {why}")
            continue
        vals = [c["cpu_ms"] for c in around]
        spread = (max(vals) - min(vals)) / min(vals) if min(vals) else float("inf")
        if len(vals) < MEDIAN_OF and spread > CONTROL_DRIFT:
            loads = [c.get("load1") for c in (around[0], around[-1])]
            load = f" (load {loads[0]:.0f} -> {loads[1]:.0f})" if None not in loads else ""
            lines.append(
                f"    {r['label']}: void, its {counted(len(vals), 'control')}"
                f" spread {spread:.1%}{load}"
            )
            continue
        lines.append(f"{r['cpu_ms'] / statistics.median(vals):8.2f}  {r['label']}")
        held += 1
    head = (
        f"{kind}: {counted(len(ctl), 'control')}, median {median / 1000:.2f} s cpu{ends};"
        " each step over the controls around it"
    )
    if steps and not held:
        head = f"{kind}: void, no step's controls held within {CONTROL_DRIFT:.0%}{ends}"
    return [head, *lines]


def calibrated(rows: list[dict]) -> str:
    """Each kinded step's figure: its CPU over the mean of its kind's controls, or why a kind has no
    ratio (fewer than two controls, one before the gate and one after, or a control that drifted);
    a RAW_KINDS kind's raw CPU. Rows that carry their time (`t`) are matched to the controls around
    each step instead (`_sampled`)."""
    kinds: dict[str, list[dict]] = {}
    controls: dict[str, list[dict]] = {}
    gate_kind = _gate_kinds(rows)
    for r in rows:
        kind = r.get("kind") or gate_kind.get(r.get("label", ""))
        if not kind or "cpu_ms" not in r:
            continue
        if r.get("control"):
            controls.setdefault(kind, []).append(r)
        else:
            kinds.setdefault(kind, []).append(r)
    if not kinds and not controls:
        return ""
    out = ["-- CPU over a same-kind control (BACKLOG 5.3) --"]
    drifts = control_drifts(rows)
    for kind in sorted(set(kinds) | set(controls)):
        if kind in RAW_KINDS:
            out.append(f"{kind}: {RAW_KINDS[kind]}")
            for r in sorted(kinds.get(kind, []), key=lambda r: -r["cpu_ms"]):
                out.append(f"{r['cpu_ms'] / 1000:8.2f} s  {r['label']}")
            continue
        ctl_rows = controls.get(kind, [])
        ctl = [c["cpu_ms"] for c in ctl_rows]
        if len(ctl) < 2:
            out.append(
                f"{kind}: uncalibrated, {counted(len(ctl), 'control')} -- one before and one after are needed"
            )
            continue
        steps = kinds.get(kind, [])
        if _timed(ctl_rows) and _timed(steps):
            out.extend(_sampled(kind, steps, ctl_rows, drifts.get(kind)))
            continue
        drift = drifts[kind]
        if abs(drift) > CONTROL_DRIFT:
            out.append(f"{kind}: void, the control drifted {drift:+.1%} between the run's two ends")
            continue
        mean = sum(ctl) / len(ctl)
        out.append(f"{kind}: control {mean / 1000:.2f} s cpu, control drift {drift:+.1%}")
        for r in sorted(steps, key=lambda r: -r["cpu_ms"]):
            out.append(f"{r['cpu_ms'] / mean:8.2f}  {r['label']}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) >= 5 and argv[0] == "record":  # record LABEL MS RC TIER [CACHE] (from shell)
        record(argv[1], float(argv[2]), int(argv[3]), argv[4], argv[5] if len(argv) > 5 else "")
        return 0
    if len(argv) >= 4 and argv[0] == "time" and argv[2] == "--":  # time LABEL -- CMD...
        return timed(argv[1], argv[3:])
    if len(argv) >= 2 and argv[0] == "report":
        top = int(argv[2]) if len(argv) > 2 else 15
        print(report(load(argv[1]), top))
        return 0
    print(
        "usage: step_timings.py report FILE [TOP] | record LABEL MS RC TIER [CACHE]"
        " | time LABEL -- CMD...",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
