#!/usr/bin/env python3
"""session_bench.py -- how much N concurrent sessions' gates serialize on each other.

Several Claude sessions gate their repos on one machine at once. What one costs the next is a
property of what they SHARE -- the tree lock (`lib/tree_lock.sh`), cargo's build-directory and
package-cache locks, the HEAD export and its binary (`gates/_from_head.sh`), the desktop lock, the
CPU -- and until this tool nothing measured it: the 2026-10-06 suite ran at load 12-31 beside
other sessions and no number from it was a measurement (docs/BACKLOG.md).

The bench clones the repo's HEAD once per session (`git clone --shared`: each session its own
checkout, as a worktree session has, so the tree lock of ONE checkout is not what is measured),
warms each clone (`--warmup` serial runs: cold caches are a different question), then for each N
in `--sessions` starts the workload in N clones at once and reports:

* makespan, and speedup = N x T1 / makespan (N serial runs over N concurrent ones);
* the Universal Scalability Law fit, C(N) = N / (1 + sigma(N-1) + kappa N(N-1)): sigma is the
  serialized fraction (a shared lock), kappa the cost of sessions interfering (cache thrash);
* every lock wait seen in the sessions' output, by name;
* with `GOH_TIMINGS` lines from the workload (every house gate writes them), the steps whose time
  grew most from 1 session to the largest N -- where the serialization IS.

A session that fails is counted, and the run exits 1: a bench over failing runs is not a
measurement. A load average above 4 at the start of a row is named, for the same reason.

Usage:
    tools/session_bench.py [--sessions 1,2,4] [--workload CMD] [--warmup 1] [--repeat 1]
                           [--json PATH] [--keep] <repo>

The default workload is `structural.sh --full`, the gate every session pays per commit; pass
`--workload 'tools/gate.sh --full'` for a push. Pinned by tests/test_session_bench.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOH = HERE.parent
sys.path.insert(0, str(GOH))
sys.path.insert(0, str(GOH / "checks"))

from _gitutil import foreign_repo_env  # noqa: E402
from tui.lib import err, info, ok, warn  # noqa: E402

DEFAULT_WORKLOAD = f'bash "{GOH}/gates/structural.sh" --full'
QUIET_LOAD = 4.0

# What a waiter prints, per lock. Cargo names the lock it waits on; the house locks say "held by".
SIGNATURES = (
    (re.compile(r"Blocking waiting for file lock on (.+?)\s*$"), lambda m: f"cargo: {m.group(1)}"),
    (re.compile(r"the build tree is held by"), lambda m: "tree lock"),
    (re.compile(r"the desktop is held by"), lambda m: "desktop lock"),
)


def lock_waits(text: str) -> dict[str, int]:
    """Every lock wait in one session's output, by lock name."""
    seen: dict[str, int] = {}
    for line in text.splitlines():
        for pattern, name in SIGNATURES:
            m = pattern.search(line)
            if m:
                key = name(m)
                seen[key] = seen.get(key, 0) + 1
    return seen


def usl_fit(points: dict[int, float]) -> dict[str, float]:
    """sigma, kappa from capacity C(N) = speedup, by least squares on N/C - 1 = s(N-1) + k N(N-1).

    One point above N=1 fixes sigma alone (kappa 0); both are clamped at 0, since a negative
    contention is noise, not a resource that makes sessions faster together."""
    rows = [(n - 1, n * (n - 1), n / c - 1) for n, c in points.items() if n > 1 and c > 0]
    if not rows:
        return {"sigma": 0.0, "kappa": 0.0}
    if len(rows) == 1:
        x1, _, y = rows[0]
        return {"sigma": max(0.0, y / x1), "kappa": 0.0}
    a11 = sum(x1 * x1 for x1, _, _ in rows)
    a12 = sum(x1 * x2 for x1, x2, _ in rows)
    a22 = sum(x2 * x2 for _, x2, _ in rows)
    b1 = sum(x1 * y for x1, _, y in rows)
    b2 = sum(x2 * y for _, x2, y in rows)
    det = a11 * a22 - a12 * a12
    if det == 0:
        return {"sigma": max(0.0, b1 / a11), "kappa": 0.0}
    sigma = (b1 * a22 - b2 * a12) / det
    kappa = (a11 * b2 - a12 * b1) / det
    if kappa < 0:  # refit with sigma alone rather than report a negative interference
        return {"sigma": max(0.0, b1 / a11), "kappa": 0.0}
    if sigma < 0:
        return {"sigma": 0.0, "kappa": max(0.0, b2 / a22)}
    return {"sigma": sigma, "kappa": kappa}


def clone(repo: Path, dest: Path) -> None:
    subprocess.run(
        ["git", "clone", "-q", "--shared", str(repo), str(dest)],
        check=True,
        env=foreign_repo_env(),
    )


def run_row(clones: list[Path], n: int, workload: str, logs: Path, tag: str) -> dict:
    """The workload in the first n clones at once; per-session wall, rc, output and timings."""
    procs = []
    for i in range(n):
        log = logs / f"{tag}-s{i}.log"
        timings = logs / f"{tag}-s{i}.timings.jsonl"
        env = dict(os.environ, GOH_TIMINGS=str(timings), GOH_BENCH_SESSION=str(i))
        handle = log.open("w")
        start = time.monotonic()
        proc = subprocess.Popen(
            ["bash", "-c", workload],
            cwd=clones[i],
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
        procs.append((proc, handle, start, log, timings))
    sessions = []
    try:
        for proc, handle, start, log, timings in procs:
            rc = proc.wait()
            end = time.monotonic()
            handle.close()
            sessions.append({"rc": rc, "start": start, "end": end, "log": log, "timings": timings})
    finally:
        for proc, handle, *_ in procs:  # an interrupted bench reaps what it started
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            handle.close()
    return {
        "makespan": max(s["end"] for s in sessions) - min(s["start"] for s in sessions),
        "walls": [s["end"] - s["start"] for s in sessions],
        "failed": sum(1 for s in sessions if s["rc"] != 0),
        "logs": [s["log"] for s in sessions],
        "timings": [s["timings"] for s in sessions],
    }


def step_means(files: list[Path]) -> dict[str, float]:
    """label -> mean ms over every GOH_TIMINGS line in files."""
    sums: dict[str, list[float]] = {}
    for f in files:
        try:
            lines = f.read_text().splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if "label" in rec and "ms" in rec:
                sums.setdefault(rec["label"], []).append(float(rec["ms"]))
    return {label: statistics.fmean(v) for label, v in sums.items()}


def inflation(alone: dict[str, float], crowded: dict[str, float]) -> list[dict]:
    """Steps present at both ends, by the time they GAINED, most first."""
    rows = [
        {
            "label": label,
            "alone_ms": round(alone[label], 1),
            "crowded_ms": round(crowded[label], 1),
            "ratio": round(crowded[label] / alone[label], 2) if alone[label] > 0 else None,
        }
        for label in alone.keys() & crowded.keys()
    ]
    return sorted(rows, key=lambda r: r["crowded_ms"] - r["alone_ms"], reverse=True)


def bench(args) -> int:
    repo = Path(args.repo).resolve()
    sessions = sorted({1, *(int(n) for n in args.sessions.split(","))})
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    from prune_kept import prune  # a failed bench keeps its work dir; keep the newest few only

    prune(tempfile.gettempdir(), "goh-session-bench.")
    work = Path(tempfile.mkdtemp(prefix="goh-session-bench."))
    logs = work / "logs"
    logs.mkdir()
    try:
        info(f"cloning {repo.name} x{max(sessions)} under {work}")
        clones = []
        for i in range(max(sessions)):
            clone(repo, work / f"s{i}")
            clones.append(work / f"s{i}")
        for w in range(args.warmup):
            info(f"warm-up {w + 1}/{args.warmup}: each clone once, serially")
            for i, c in enumerate(clones):
                r = run_row([c], 1, args.workload, logs, f"warm{w}-c{i}")
                if r["failed"]:
                    err(f"warm-up failed in clone {i}: {r['logs'][0]}")
                    return 1
        rows, timed = [], {}
        for n in sessions:
            load = os.getloadavg()[0]
            if load > QUIET_LOAD:
                warn(
                    f"N={n}: load {load:.1f} at the start -- not a quiet box; read the row as such"
                )
            reps = [
                run_row(clones, n, args.workload, logs, f"n{n}-r{r}") for r in range(args.repeat)
            ]
            makespan = statistics.median(r["makespan"] for r in reps)
            waits: dict[str, int] = {}
            for r in reps:
                for log in r["logs"]:
                    for k, v in lock_waits(log.read_text(errors="replace")).items():
                        waits[k] = waits.get(k, 0) + v
            waits = {k: round(v / args.repeat) for k, v in waits.items()}
            timed[n] = [f for r in reps for f in r["timings"]]
            rows.append(
                {
                    "sessions": n,
                    "makespan_s": round(makespan, 3),
                    "mean_session_s": round(
                        statistics.fmean(w for r in reps for w in r["walls"]), 3
                    ),
                    "failed": sum(r["failed"] for r in reps),
                    "load_at_start": round(load, 2),
                    "lock_waits": waits,
                }
            )
        t1 = rows[0]["makespan_s"]
        for row in rows:
            row["speedup"] = (
                round(row["sessions"] * t1 / row["makespan_s"], 2) if row["makespan_s"] else 0
            )
            row["efficiency"] = round(row["speedup"] / row["sessions"], 2)
        doc = {
            "repo": str(repo),
            "workload": args.workload,
            "rows": rows,
            "usl": {
                k: round(v, 3)
                for k, v in usl_fit({r["sessions"]: r["speedup"] for r in rows}).items()
            },
            "inflation": inflation(step_means(timed[1]), step_means(timed[max(sessions)]))[:15],
        }
        report(doc)
        if args.json:
            Path(args.json).write_text(json.dumps(doc, indent=2) + "\n")
        failed = sum(r["failed"] for r in rows)
        if failed:
            err(
                f"{failed} session run(s) failed -- logs in {logs} (kept); this is not a measurement"
            )
            args.keep = True
            return 1
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            info(f"kept {work}")


def report(doc: dict) -> None:
    print(f"\n  {'N':>3} {'makespan':>10} {'session':>9} {'speedup':>8} {'eff':>5}  lock waits")
    for r in doc["rows"]:
        waits = ", ".join(f"{k} x{v}" for k, v in sorted(r["lock_waits"].items())) or "-"
        print(
            f"  {r['sessions']:>3} {r['makespan_s']:>9.2f}s {r['mean_session_s']:>8.2f}s "
            f"{r['speedup']:>8.2f} {r['efficiency']:>5.2f}  {waits}"
        )
    usl = doc["usl"]
    ok(
        f"USL fit: sigma {usl['sigma']:.3f} (serialized fraction), kappa {usl['kappa']:.4f} (interference)"
    )
    for row in doc["inflation"][:5]:
        info(
            f"{row['label']}: {row['alone_ms']:.0f} -> {row['crowded_ms']:.0f} ms (x{row['ratio']})"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo")
    ap.add_argument("--sessions", default="1,2,4", help="comma list of concurrency levels")
    ap.add_argument("--workload", default=DEFAULT_WORKLOAD, help="bash command run in each clone")
    ap.add_argument("--warmup", type=int, default=1, help="serial warm-up runs per clone")
    ap.add_argument(
        "--repeat", type=int, default=1, help="runs per row; the median makespan counts"
    )
    ap.add_argument("--json", help="write the whole result here")
    ap.add_argument("--keep", action="store_true", help="keep the clones and logs")
    return bench(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
