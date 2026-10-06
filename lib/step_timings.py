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

CONCURRENCY. One `os.write` of one line under `O_APPEND`, shorter than `PIPE_BUF`: concurrent
writers (a 4-way crate fan-out) interleave whole lines, never halves. A write that fails is
dropped silently ON PURPOSE -- the instrument must never be the reason a gate goes red.
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

import json
import os
import sys
from collections import defaultdict

ENV = "GOH_TIMINGS"
PARENT_ENV = "GOH_TIMINGS_PARENT"
_MAX_LINE = 4000  # under PIPE_BUF (4096 on macOS and Linux), so an append is atomic


def record(label: str, ms: float, rc: int, tier: str, cache: str = "") -> None:
    """Append one step's line to $GOH_TIMINGS; a no-op when it is unset."""
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
    }
    if cache:
        row["cache"] = cache
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")[:_MAX_LINE]
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        pass


def child_env(label: str) -> dict[str, str] | None:
    """The environment for a timed step's child: the parent label set, or None to inherit."""
    if not os.environ.get(ENV):
        return None
    env = dict(os.environ)
    env[PARENT_ENV] = label
    return env


def load(path: str) -> list[dict]:
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
    out = [f"{len(rows)} step(s) recorded"]
    out.append(f"-- top {top} steps by wall time --")
    for r in sorted(rows, key=lambda r: -r["ms"])[:top]:
        where = os.path.basename(r.get("cwd", "")) or "."
        hit = f" [{r['cache']}]" if r.get("cache") else ""
        out.append(f"{r['ms'] / 1000:8.2f} s  rc={r['rc']:<3} {where:<20} {r['label']}{hit}")
    sums: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        sums[r["label"]].append(r["ms"])
    out.append(f"-- top {top} labels, summed over every run of the label --")
    for label, ms in sorted(sums.items(), key=lambda kv: -sum(kv[1]))[:top]:
        out.append(f"{sum(ms) / 1000:8.2f} s  x{len(ms):<4} {label}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) >= 5 and argv[0] == "record":  # record LABEL MS RC TIER [CACHE] (from shell)
        record(argv[1], float(argv[2]), int(argv[3]), argv[4], argv[5] if len(argv) > 5 else "")
        return 0
    if len(argv) >= 2 and argv[0] == "report":
        top = int(argv[2]) if len(argv) > 2 else 15
        print(report(load(argv[1]), top))
        return 0
    print(
        "usage: step_timings.py report FILE [TOP] | record LABEL MS RC TIER [CACHE]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
