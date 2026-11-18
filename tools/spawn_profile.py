#!/usr/bin/env python3
"""spawn_profile.py -- how many processes a gate starts, of which kind, from which script, at what
cost.

GOH.SPAWNPROFILE (2026-11-17): the owner asked to "measure then port" before any bash->python->Rust
rewrite. A land gate spent 827 s in its coverage step; how much of that is PROCESS START (a python3
costs ~30 ms before it runs a line, bash and git ~18 ms) is the number that prices a port. This is
the instrument that needs no privilege: eslogger and dtrace need root, and `sudo -n` is refused on
this machine without the owner's password.

    tools/spawn_profile.py run --out DIR [--sample 0.5] -- CMD...  # CMD under the shims
    tools/spawn_profile.py report DIR [--top 20] [--costs CAL] [--json]  # counts, cost, callers
    tools/spawn_profile.py calibrate --out DIR [-n 20]  # start cost per kind, the shim's overhead

HOW. `run` compiles one tiny C shim per tool kind (python3, python, bash, sh, git, goh, cargo) into
DIR/shims, with the real binary and the ledger path baked in, puts DIR/shims first on PATH, points
GOH_PY at the python shim and PYTHONEXECUTABLE at it too (macOS: `sys.executable` then names the
shim,
so `subprocess.run([sys.executable, ...])` is seen). Each shim forks the real binary, forwards the
catchable signals to it, waits with wait4, and appends ONE line to DIR/ledger.tsv (one O_APPEND
write, under PIPE_BUF): kind, its own pid, the child's pid, its caller's pid, start, wall, user,
sys, the exit status and argv. The child's rusage includes the descendants it waited for; `report`
subtracts the seen children to get each process's own cpu.

WHAT IT CANNOT SEE, measured rather than guessed: a call by absolute path (`/bin/bash`, `/bin/sh`
in a shebang or subprocess, a goh binary run by path, an env that resets PATH) bypasses every shim.
`--sample S` runs `ps -A` every S seconds and keeps every descendant of CMD; `report` names the
sampled processes the ledger never saw (by kind and comm) and their share of the sampled
process-time -- a process shorter than S is mostly missed by the sampler, so the share is of
process-time, not of count. The cmd tree's exact cpu (wait4 of CMD) is printed beside the ledger's.

COST. `calibrate` times N trivial starts per kind under the load of the moment (python3 -c pass,
bash -c :, git --version, ...) directly and through a shim, so the shim's overhead and each kind's
start cost are numbers of THIS machine at THIS load; `report DIR --costs CAL` prices the counts with
them. Load swings wall and cpu; counts do not -- every number carries the 1-minute load beside it.
Pinned by tests/test_spawn_profile.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    import contextlib as _contextlib

    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

from spawn_ledger import FIELDS, classify, read_ledger, script_of, summarize  # noqa: F401 (re-exported)

KINDS = ("python3", "python", "bash", "sh", "git", "goh", "cargo")

SHIM_C = r"""
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>

static volatile pid_t child = -1;
static void fwd(int s) { if (child > 0) kill(child, s); }

int main(int argc, char **argv) {
    struct timeval t0, t1;
    gettimeofday(&t0, 0);
    pid_t c = fork();
    if (c < 0) { argv[0] = REAL; execv(REAL, argv); return 127; }
    if (c == 0) { argv[0] = REAL; execv(REAL, argv); _exit(127); }
    child = c;
    int sigs[] = {SIGTERM, SIGINT, SIGHUP, SIGQUIT, SIGUSR1, SIGUSR2, SIGALRM};
    struct sigaction sa; memset(&sa, 0, sizeof sa); sa.sa_handler = fwd;
    for (unsigned i = 0; i < sizeof sigs / sizeof *sigs; i++) sigaction(sigs[i], &sa, 0);
    int st = 0; struct rusage ru; memset(&ru, 0, sizeof ru);
    while (wait4(c, &st, 0, &ru) < 0 && errno == EINTR) {}
    gettimeofday(&t1, 0);
    char line[1400]; int n;
    double wall = (t1.tv_sec - t0.tv_sec) + (t1.tv_usec - t0.tv_usec) / 1e6;
    n = snprintf(line, 600, "%s\t%d\t%d\t%d\t%ld.%06d\t%.6f\t%.6f\t%.6f\t%d\t", KIND, (int)getpid(),
                 (int)c, (int)getppid(), (long)t0.tv_sec, (int)t0.tv_usec, wall,
                 ru.ru_utime.tv_sec + ru.ru_utime.tv_usec / 1e6,
                 ru.ru_stime.tv_sec + ru.ru_stime.tv_usec / 1e6, st);
    for (int i = 1; i < argc && n < 1300; i++) {
        for (const char *p = argv[i]; *p && n < 1300; p++)
            line[n++] = (*p == '\t' || *p == '\n' || *p == '\r') ? ' ' : *p;
        if (i + 1 < argc && n < 1300) line[n++] = ' ';
    }
    line[n++] = '\n';
    int fd = open(LEDGER, O_WRONLY | O_APPEND | O_CREAT, 0644);
    if (fd >= 0) { ssize_t w = write(fd, line, (size_t)n); (void)w; close(fd); }
    if (WIFSIGNALED(st)) {
        signal(WTERMSIG(st), SIG_DFL);
        kill(getpid(), WTERMSIG(st));
        return 128 + WTERMSIG(st);
    }
    return WEXITSTATUS(st);
}
"""


def load1() -> float:
    try:
        return round(os.getloadavg()[0], 1)
    except OSError:
        return -1.0


def c_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_shims(out: Path, reals: dict[str, str], cc: str = "cc") -> dict[str, Path]:
    """One compiled shim per kind in out/shims, logging to out/ledger.tsv. Returns kind -> shim."""
    shims = out / "shims"
    shims.mkdir(parents=True, exist_ok=True)
    src = out / "shim.c"
    src.write_text(SHIM_C, encoding="utf-8")
    ledger = (out / "ledger.tsv").resolve()
    made: dict[str, Path] = {}
    for kind, real in reals.items():
        dest = shims / kind
        cmd = [
            cc,
            "-O1",
            "-o",
            str(dest),
            f"-DREAL={c_string(real)}",
            f"-DKIND={c_string(kind)}",
            f"-DLEDGER={c_string(str(ledger))}",
            str(src),
        ]
        done = subprocess.run(
            cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False
        )
        if done.returncode != 0:
            raise SystemExit(
                f"spawn_profile: cannot compile the {kind} shim with {cc}: {done.stderr.strip()}"
            )
        made[kind] = dest
    return made


def real_binaries(path: str, kinds: tuple[str, ...] = KINDS) -> dict[str, str]:
    found: dict[str, str] = {}
    for kind in kinds:
        hit = shutil.which(kind, path=path)
        if hit:
            found[kind] = os.path.abspath(
                hit
            )  # never realpath: a rustup proxy dispatches on argv[0]
    return found


def ps_table() -> list[tuple[int, int, str]]:
    done = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,comm="],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    rows = []
    for line in done.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def descendants(root: int, rows: list[tuple[int, int, str]]) -> dict[int, str]:
    kids: dict[int, list[int]] = defaultdict(list)
    comm = {}
    for pid, ppid, name in rows:
        kids[ppid].append(pid)
        comm[pid] = name
    seen: dict[int, str] = {}
    stack = list(kids.get(root, []))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen[pid] = comm[pid]
        stack.extend(kids.get(pid, []))
    return seen


def sampler(root: int, every: float, dest: Path, stop: threading.Event) -> None:
    with dest.open("a", encoding="utf-8") as fh:
        while not stop.wait(every):
            stamp = time.time()
            for pid, name in descendants(root, ps_table()).items():
                fh.write(f"{stamp:.3f}\t{pid}\t{name}\n")
            fh.flush()


def cmd_run(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    path = os.environ.get("PATH", "")
    reals = real_binaries(path)
    if args.goh:
        reals["goh"] = os.path.abspath(args.goh)
    shims = build_shims(out, reals)
    env = dict(os.environ)
    env["PATH"] = f"{out / 'shims'}:{path}"
    if "python3" in shims:
        env["GOH_PY"] = str(shims["python3"])
        env["PYTHONEXECUTABLE"] = str(shims["python3"])
        env.pop("GOH_PY_OK", None)
    meta = {"cmd": args.cmd, "load_start": load1(), "t0": time.time(), "reals": reals}
    start = time.monotonic()
    before = os.times()
    proc = subprocess.Popen(args.cmd, env=env, stdin=subprocess.DEVNULL)
    stop = threading.Event()
    thread = None
    if args.sample > 0:
        thread = threading.Thread(
            target=sampler, args=(proc.pid, args.sample, out / "samples.tsv", stop), daemon=True
        )
        thread.start()
    try:
        rc = proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        rc = proc.wait()
    stop.set()
    if thread:
        thread.join(5)
    after = os.times()
    meta.update(
        rc=rc,
        wall=round(time.monotonic() - start, 3),
        cpu=round(
            (after.children_user - before.children_user)
            + (after.children_system - before.children_system),
            3,
        ),
        load_end=load1(),
        root_pid=proc.pid,
        sample=args.sample,
    )
    (out / "run.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(
        f"spawn_profile: rc={rc} wall={meta['wall']}s cpu={meta['cpu']}s"
        f" load {meta['load_start']}->{meta['load_end']}; ledger {out / 'ledger.tsv'}"
    )
    return rc


def cmd_report(args: argparse.Namespace) -> int:
    costs = {}
    if args.costs:
        cal = json.loads(Path(args.costs).read_text(encoding="utf-8"))
        costs = {
            k: (v["direct_ms"] / 1000, v["direct_cpu_ms"] / 1000) for k, v in cal["kinds"].items()
        }
    summary = summarize(Path(args.dir), costs, args.top)
    if args.json:
        print(json.dumps(summary, indent=1))
        return 0
    run = summary["run"]
    print(f"processes seen: {summary['rows']}  by kind: {summary['by_kind']}")
    print(
        f"run: wall {run['wall']}s cpu {run['cpu']}s"
        f" load {run['load_start']}->{run['load_end']} rc {run['rc']}"
    )
    print(
        f"start cost (count x calibrated start): cpu {summary['start_cpu_s']}s"
        f" = {summary['start_share_cpu']} of the run's cpu;"
        f" wall {summary['start_wall_s']}s summed over processes"
    )
    print(
        f"{'n':>6} {'start_cpu':>9} {'start_wall':>10} {'self_cpu':>9} {'kind':<7} caller -> callee"
    )
    for g in summary["top"]:
        print(
            f"{g['n']:>6} {g['start_cpu_s']:>9} {g['start_wall_s']:>10} {g['self_cpu']:>9}"
            f" {g['kind']:<7}"
            f" {g['caller']} -> {g['callee']}"
        )
    print(f"blind: {json.dumps(summary['blind'])}")
    return 0


TRIVIAL = {
    "python": ["-c", "pass"],
    "python_IS": ["-I", "-S", "-c", "pass"],
    "bash": ["-c", ":"],
    "git": ["--version"],
    "goh": ["--version"],
    "cargo": ["--version"],
}


def timed(argv: list[str], n: int) -> tuple[float, float]:
    """Median wall ms and mean cpu ms of n runs of argv (output dropped)."""
    walls = []
    before = os.times()
    for _ in range(n):
        t = time.monotonic()
        subprocess.run(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        walls.append((time.monotonic() - t) * 1000)
    after = os.times()
    cpu = (
        (
            (after.children_user - before.children_user)
            + (after.children_system - before.children_system)
        )
        * 1000
        / n
    )
    return round(statistics.median(walls), 2), round(cpu, 2)


def cmd_calibrate(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    reals = real_binaries(os.environ.get("PATH", ""), ("python3", "bash", "git", "goh", "cargo"))
    if args.goh:
        reals["goh"] = os.path.abspath(args.goh)
    true_bin = shutil.which("true") or "/usr/bin/true"
    shims = build_shims(out, {**reals, "true": os.path.realpath(true_bin)})
    result: dict = {"load_start": load1(), "n": args.n, "kinds": {}}
    plan = {
        "python": "python3",
        "python_IS": "python3",
        "bash": "bash",
        "git": "git",
        "goh": "goh",
        "cargo": "cargo",
    }
    for name, real_kind in plan.items():
        if real_kind not in reals:
            continue
        d_wall, d_cpu = timed([reals[real_kind], *TRIVIAL[name]], args.n)
        s_wall, s_cpu = timed([str(shims[real_kind]), *TRIVIAL[name]], args.n)
        result["kinds"][name] = {
            "direct_ms": d_wall,
            "direct_cpu_ms": d_cpu,
            "shim_ms": s_wall,
            "shim_cpu_ms": s_cpu,
        }
    t_wall, t_cpu = timed([true_bin], args.n)
    ts_wall, ts_cpu = timed([str(shims["true"])], args.n)
    result["shim_overhead"] = {
        "wall_ms": round(ts_wall - t_wall, 2),
        "cpu_ms": round(ts_cpu - t_cpu, 2),
        "true_ms": t_wall,
    }
    for alias, kind in (("python3", "python"), ("sh", "bash")):
        if kind in result["kinds"]:
            result["kinds"][alias] = result["kinds"][kind]
    result["load_end"] = load1()
    (out / "ledger.tsv").unlink(missing_ok=True)  # the calibration's own rows are not a profile
    (out / "calibration.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    r = sub.add_parser("run", help="run CMD with a shim per tool kind; one ledger row per process")
    r.add_argument("--out", required=True)
    r.add_argument(
        "--sample",
        type=float,
        default=0.0,
        help="ps sample period in s (0: off) for the blind share",
    )
    r.add_argument("--goh", default="", help="the goh binary to shim (it is rarely on PATH)")
    r.add_argument("cmd", nargs=argparse.REMAINDER)
    p = sub.add_parser("report", help="summarize a run's ledger")
    p.add_argument("dir")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--costs", default="", help="a calibration.json to price each start")
    p.add_argument("--json", action="store_true")
    c = sub.add_parser(
        "calibrate", help="start cost per kind and the shim's overhead, at this load"
    )
    c.add_argument("--out", required=True)
    c.add_argument("-n", type=int, default=20)
    c.add_argument("--goh", default="")
    args = ap.parse_args(argv)
    if args.verb == "run":
        if args.cmd and args.cmd[0] == "--":
            args.cmd = args.cmd[1:]
        if not args.cmd:
            ap.error("run needs a command after --")
        return cmd_run(args)
    if args.verb == "report":
        return cmd_report(args)
    return cmd_calibrate(args)


if __name__ == "__main__":
    sys.exit(main())
