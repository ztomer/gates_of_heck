#!/usr/bin/env python3
"""load_sources.py -- what burns the machine's CPU, by process family and repo (GOH.LOADSOURCES).

    goh load --report [--since 24h|90m|YYYY-MM-DD|EPOCH] [--by family|repo|exe|family+repo] [--json]
    python3 lib/load_sources.py sample [--interval 30] [--for 3600]     # the launchd job's body

WHY. GOH.CPUADMIT's replay (D-0009) found the day's gates averaging 2.89 cores while the median
1-minute load was 69.6 on 16 cores: most of what stretches every gate was outside the gate queue,
and nothing recorded what it was. The sampler (launchd `com.ztomer.goh-load-sources`, started by
tools/install_load_sources.sh) reads the process table every `--interval` seconds and appends one
line per sample to `$GOH_LOAD_SOURCES_DIR` (default `$XDG_STATE_HOME/goh/load_sources`, else
~/.local/state/goh/load_sources), one file per UTC day, 14 days kept.

A SAMPLE is each process's CPU since the last one (lib/proc_table.py). A process born and dead
between two samples is still counted: its CPU arrives in its parent's REAPED-children time, less
what was already seen of the children that vanished (`attribute`), and is booked to the parent's
row as `<exe>+children`. Its FAMILY (`families`) is, in order: a system daemon by its own name
(spotlight, fseventsd, windowserver, kernel); else the OUTERMOST house context in its ancestry
(land, ship, push-gate, commit-gate, gate, pin-sweep, sccache, load-sources) -- a pytest under
land.sh is the land's, a cargo under gate.sh under push_gate.sh the push gate's; else an app bundle
(`app:<Name>`); else the nearest tool in its ancestry (rust-build, rust-test, swift-build, pytest,
python, node, git), or `exe:<name>`; under an agent (claude, opencode, agy, codex) and no house
context, `agent:<that>`. Its REPO is the repository of its working directory and, for a linked
worktree, the worktree (a lane's `<ITEM>/wt` is `<ITEM>`).

THE LEDGER holds names and numbers only: family, repo, worktree, executable basename, cpu-s, rss MB,
and per sample the interval, the machine's busy cpu-s (host CPU ticks), the 1-minute load, the core
count and the sampler's own cpu-s. Never a path beyond the repo's name, never an argument: a command
line can hold a secret. A row under 0.05 cpu-s is merged into its family's `*` row.

THE REPORT ranks by cpu-s against the machine's busy cpu-s over the covered samples; COVERAGE is the
attributed share, and the rest is named `unattributed` (CPU of unreadable parents' short-lived
children, kernel threads, a process's last interval before it died unreaped). The load's own cpu-s,
sum(min(load1, cores) x interval), is printed beside it: load past the core count is waiting, not
burning. Pinned by tests/test_load_sources.py.
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
import resource
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent))
from proc_table import Proc, Snapshot, reader  # noqa: E402

DIR_ENV = "GOH_LOAD_SOURCES_DIR"
FLOOR_S = 0.05
KEEP_DAYS = 14

DAEMONS = {
    "spotlight": {
        "mds",
        "mds_stores",
        "mdworker",
        "mdworker_shared",
        "mdbulkimport",
        "mdsync",
        "corespotlightd",
        "Spotlight",
    },
    "fseventsd": {"fseventsd"},
    "windowserver": {"WindowServer"},
    "kernel": {"kernel_task"},
    "media-analysis": {"mediaanalysisd", "photoanalysisd"},
}
CONTEXTS = (  # matched on an exe or script basename; the OUTERMOST in the ancestry wins
    ("land", {"land.sh", "landq.py", "land_prestep.py", "affected_tests.py"}),
    ("ship", {"ship.py"}),
    ("push-gate", {"push_gate.sh", "pre-push"}),
    ("commit-gate", {"pre-commit", "commit-msg", "commit_msg.sh"}),
    ("pin-sweep", {"pin_sweep.py"}),
    ("gate", {"gate.sh", "structural.sh", "local_ci.sh", "coverage_gate.sh"}),
    ("load-experiment", {"load.py"}),
    ("load-sources", {"load_sources.py", "load_sources.sh"}),
    ("sccache", {"sccache"}),
)
TOOLS = (  # the NEAREST in the ancestry wins
    (
        "rust-build",
        {
            "cargo",
            "rustc",
            "clippy-driver",
            "rustdoc",
            "cargo-clippy",
            "rustfmt",
            "ld",
            "ld64",
            "clang",
            "cc",
            "cargo-llvm-cov",
        },
    ),
    (
        "swift-build",
        {"swift", "swiftc", "swift-frontend", "swift-build", "swift-test", "xcodebuild"},
    ),
    ("pytest", {"pytest", "py.test"}),
    ("node", {"node", "bun", "deno", "npm"}),
    ("git", {"git"}),
)
AGENTS = {"claude", "opencode", "agy", "codex", "gemini"}
_PY = re.compile(r"^[Pp]ython(\d+(\.\d+)?)?$")
_RUST_TEST = re.compile(r"/(target|\.cargo/build)/.*/deps/[^/]+-[0-9a-f]{16}$")


def _labels(p: Proc) -> set[str]:
    return {p.exe} | ({p.script} if p.script else set())


def _chain(pid: int, procs: dict[int, Proc]) -> list[Proc]:
    """pid's process, then each ancestor's, to launchd (a cycle or a gap ends it)."""
    out, seen = [], set()
    while pid in procs and pid not in seen and pid > 1:
        seen.add(pid)
        out.append(procs[pid])
        pid = procs[pid].ppid
    return out


def _tool(p: Proc) -> str | None:
    if _RUST_TEST.search(p.path):
        return "coverage" if "/llvm-cov/" in p.path else "rust-test"
    for fam, names in TOOLS:
        if _labels(p) & names:
            return fam
    return "python" if _PY.match(p.exe) or p.exe == "python3" else None


def family(pid: int, procs: dict[int, Proc]) -> str:
    me = procs[pid]
    for fam, names in DAEMONS.items():
        if me.exe in names:
            return fam
    chain = _chain(pid, procs)
    for anc in reversed(chain):  # outermost first
        for fam, names in CONTEXTS:
            if _labels(anc) & names:
                return fam
    if ".app/Contents/" in me.path:
        return "app:" + me.path.split(".app/Contents/", 1)[0].rsplit("/", 1)[-1]
    agent = any(a.exe in AGENTS for a in chain)
    if me.exe in AGENTS:
        return "agent"
    tool = next((t for t in map(_tool, chain) if t), None) or f"exe:{me.exe}"
    return f"agent:{tool}" if agent else tool


def families(procs: dict[int, Proc]) -> dict[int, str]:
    return {pid: family(pid, procs) for pid in procs}


def attribute(
    prev: dict[int, Proc], cur: dict[int, Proc]
) -> tuple[dict[int, float], dict[int, float]]:
    """(own cpu-s, reaped-children cpu-s) per live pid over the interval between two tables.

    A vanished process's last-seen own+reaped time is already counted; its final total arrives in
    its nearest live ancestor's reaped time, so it is taken off there and only the rest is new."""
    own: dict[int, float] = {}
    reaped: dict[int, float] = {}
    for pid, c in cur.items():
        o = prev.get(pid)
        same = o is not None and o.key == c.key and c.cpu_s >= o.cpu_s
        own[pid] = c.cpu_s - (o.cpu_s if same and o else 0.0)
        if c.child_s is not None:
            base = o.child_s if same and o and o.child_s is not None else 0.0
            reaped[pid] = c.child_s - base
    live = {c.key for c in cur.values()}
    for o in prev.values():
        if o.key in live:
            continue
        anc = o.ppid
        seen = set()
        while anc not in seen and anc in prev and prev[anc].key not in live:
            seen.add(anc)
            anc = prev[anc].ppid
        if anc in reaped and anc in prev and prev[anc].key == cur[anc].key:
            reaped[anc] -= o.cpu_s + (o.child_s or 0.0)
    return (
        {k: round(max(v, 0.0), 3) for k, v in own.items()},
        {k: round(max(v, 0.0), 3) for k, v in reaped.items() if v > 0},
    )


class RepoResolver:
    """cwd -> (repo, worktree): the repository holding the directory and, for a linked worktree,
    its name (`<ITEM>` for a lane's `<ITEM>/wt`). Cached per directory: a sample reads no disk for
    a directory it has seen."""

    def __init__(self) -> None:
        self.cache: dict[str, tuple[str, str]] = {}

    def __call__(self, cwd: str | None) -> tuple[str, str]:
        if not cwd:
            return ("", "")
        if cwd not in self.cache:
            if len(self.cache) > 4096:
                self.cache.clear()
            self.cache[cwd] = self._find(Path(cwd))
        return self.cache[cwd]

    def _find(self, d: Path) -> tuple[str, str]:
        for cand in (d, *d.parents):
            if str(cand) in self.cache and cand != d:
                return self.cache[str(cand)]
            git = cand / ".git"
            try:
                if git.is_dir():
                    return (cand.name, "")
                if git.is_file():
                    target = git.read_text(errors="replace").partition("gitdir:")[2].strip()
                    common = Path(target).parent.parent  # <repo>/.git/worktrees/<x> -> <repo>/.git
                    name = cand.parent.name if cand.name == "wt" else cand.name
                    return (common.parent.name if common.name == ".git" else cand.name, name)
            except OSError:
                return ("", "")
        return ("", "")


def record(prev: Snapshot, cur: Snapshot, repo_of: RepoResolver, self_s: float) -> dict:
    """One ledger line: the interval's CPU, grouped by (family, repo, worktree, exe)."""
    own, reaped = attribute(prev.procs, cur.procs)
    fam = families(cur.procs)
    rows: dict[tuple[str, str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for pid, c in cur.procs.items():
        repo, wt = repo_of(c.cwd)
        for exe, cpu, rss in (
            (c.exe, own.get(pid, 0.0), c.rss_kb),
            (c.exe + "+children", reaped.get(pid, 0.0), 0),
        ):
            if cpu <= 0 and not rss:
                continue
            row = rows[(fam[pid], repo, wt, exe)]
            row[0] += cpu
            row[1] += rss / 1024
    out: dict[tuple[str, str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for (f, repo, wt, exe), (cpu, rss) in rows.items():
        if cpu <= 0:
            continue
        key = (f, repo, wt, exe) if cpu >= FLOOR_S else (f, "", "", "*")
        out[key][0] += cpu
        out[key][1] += rss
    busy = None if prev.busy_s is None or cur.busy_s is None else round(cur.busy_s - prev.busy_s, 2)
    return {
        "t": round(cur.t, 1),
        "dt": round(cur.t - prev.t, 2),
        "busy_s": busy,
        "load1": cur.load1,
        "cores": cur.cores,
        "procs": len(cur.procs),
        "self_s": round(self_s, 3),
        "rows": sorted(
            ([*k, round(v[0], 3), round(v[1], 1)] for k, v in out.items()), key=lambda r: -r[4]
        ),
    }


def ledger_dir() -> Path:
    explicit = os.environ.get(DIR_ENV)
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "goh" / "load_sources"


def _day_file(d: Path, t: float) -> Path:
    return d / f"sources-{dt.datetime.fromtimestamp(t, dt.UTC):%Y-%m-%d}.jsonl"


def append(d: Path, rec: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with _day_file(d, rec["t"]).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")


def prune(d: Path, now: float) -> None:
    keep = {_day_file(d, now - 86400 * i).name for i in range(KEEP_DAYS)}
    for f in d.glob("sources-*.jsonl"):
        if f.name not in keep:
            f.unlink(missing_ok=True)


def read_ledger(d: Path, since: float) -> list[dict]:
    out = []
    for f in sorted(d.glob("sources-*.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("t", 0) - rec.get("dt", 0) >= since:
                out.append(rec)
    return out


_BY = {"family": (0,), "repo": (1, 2), "exe": (3,), "family+repo": (0, 1, 2)}


def report(recs: list[dict], by: str = "family") -> dict:
    """The ranking over `recs`, by `by`, against the machine's busy cpu-s."""
    recs = [r for r in recs if r.get("busy_s") is not None]
    busy = sum(r["busy_s"] for r in recs)
    wall = sum(r["dt"] for r in recs)
    totals: dict[str, float] = defaultdict(float)
    for r in recs:
        for row in r["rows"]:
            key = " ".join(x for x in (row[i] for i in _BY[by]) if x) or "-"
            totals[key] += row[4]
    attributed = sum(totals.values())
    rank = [
        {
            "key": k,
            "cpu_s": round(v, 1),
            "share": round(v / busy, 4) if busy else 0.0,
            "cores": round(v / wall, 2) if wall else 0.0,
        }
        for k, v in sorted(totals.items(), key=lambda kv: -kv[1])
    ]
    load = sum(min(r.get("load1") or 0.0, r.get("cores") or 0) * r["dt"] for r in recs)
    return {
        "samples": len(recs),
        "wall_s": round(wall, 1),
        "busy_s": round(busy, 1),
        "attributed_s": round(attributed, 1),
        "unattributed_s": round(busy - attributed, 1),
        "coverage": round(attributed / busy, 4) if busy else 0.0,
        "load_cpu_s": round(load, 1),
        "mean_load1": round(sum((r.get("load1") or 0) * r["dt"] for r in recs) / wall, 2)
        if wall
        else 0.0,
        "self_pct_core": round(100 * sum(r.get("self_s", 0) for r in recs) / wall, 3)
        if wall
        else 0.0,
        "rank": rank,
    }


def parse_since(text: str, now: float) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd])", text)
    if m:
        return now - float(m[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    return dt.datetime.fromisoformat(text).astimezone().timestamp()


def print_report(rep: dict, top: int) -> None:
    from tui.lib import info, section, warn

    section(f"load sources: {rep['samples']} samples, {rep['wall_s'] / 3600:.2f} h")
    info(
        f"busy {rep['busy_s']:.0f} cpu-s (mean {rep['busy_s'] / max(rep['wall_s'], 1):.2f} cores); "
        f"load {rep['load_cpu_s']:.0f} cpu-s (mean 1-min load {rep['mean_load1']})"
    )
    for i, r in enumerate(rep["rank"][:top], 1):
        pct, cores = 100 * r["share"], r["cores"]
        print(f"  {i:>3}  {r['cpu_s']:>10.1f} cpu-s  {pct:>5.1f}%  {cores:>6.2f} cores  {r['key']}")
    rest = rep["rank"][top:]
    if rest:
        print(f"       {sum(r['cpu_s'] for r in rest):>10.1f} cpu-s  in {len(rest)} more")
    info(
        f"coverage {100 * rep['coverage']:.1f}% of busy cpu-s; "
        f"unattributed {rep['unattributed_s']:.0f} cpu-s "
        "(short-lived children of unreadable parents, kernel threads, unreaped last intervals)"
    )
    if rep["coverage"] < 0.9:
        warn("coverage is under 90%: the ranking names less than nine tenths of the busy CPU")
    info(f"sampler cost {rep['self_pct_core']:.3f}% of one core")


def sample(interval: float, duration: float, d: Path) -> int:
    rd = reader()
    resolve = RepoResolver()

    def snap() -> Snapshot:
        return Snapshot(
            time.time(), rd.busy_s(), os.getloadavg()[0], os.cpu_count() or 1, rd.snapshot()
        )

    def used() -> float:
        s, c = (
            resource.getrusage(resource.RUSAGE_SELF),
            resource.getrusage(resource.RUSAGE_CHILDREN),
        )
        return s.ru_utime + s.ru_stime + c.ru_utime + c.ru_stime

    u0, prev = used(), snap()
    end = time.monotonic() + duration
    while time.monotonic() + interval <= end:
        time.sleep(interval)  # pacing, not readiness: the next read is due, whatever it says
        cur = snap()
        u1 = used()
        append(d, record(prev, cur, resolve, u1 - u0))
        prune(d, cur.t)
        u0, prev = u1, cur
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh load", description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", nargs="?", choices=["sample"], help="run the sampler (the launchd job)")
    ap.add_argument("--report", action="store_true", help="rank the ledger's families by cpu-s")
    ap.add_argument("--since", default="24h", help="24h, 90m, YYYY-MM-DD[THH:MM] or an epoch")
    ap.add_argument("--by", choices=sorted(_BY), default="family")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ledger-dir", type=Path, default=None)
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument(
        "--for", dest="duration", type=float, default=3600.0, help="exit after this many s"
    )
    a = ap.parse_args(argv)
    d = a.ledger_dir or ledger_dir()
    if a.mode == "sample":
        return sample(a.interval, a.duration, d)
    if not a.report:
        ap.error("name --report or `sample`")
    rep = report(read_ledger(d, parse_since(a.since, time.time())), a.by)
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        print_report(rep, a.top)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
