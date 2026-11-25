"""lib/check_jobs.py -- a check's jobs: each in its own session, none of them outliving the check
(GOH.CHECKVERDICT).

THE FAULT (2026-11-25). `goh land --check` started every job in a session of its own and stopped a
group only at the job's OWN deadline. `timeout 600 goh land --check WT` ended the check and left its
pytest.sh, structural and affected_tests children running, and the commit gate's worktree
registered: three lanes met it in one day. A group kill missed a grandchild in a session of its own
even at the job's deadline.

THE LIFELINE. `guarded` starts a WATCHER before the first job: a process in a session of its own
(a signal to the check's group never reaches it) that reads the check's end of a pipe. The check
tells it each job's pid, each worktree it registers, and `end`. An end of file with no `end` is a
check that died, by any signal, SIGKILL included: the watcher stops every job with its whole tree
(`sweep`), removes each worktree, and records the run. The CLI's own SIGTERM, SIGINT and SIGHUP
handler asks the watcher for exactly that, waits for it, and only then exits 128+N, so what
`timeout` returns to is already clean. One mechanism, not a handler and a fallback.

    run(argv, cwd, env, ...)    one job to its verdict or its deadline: (exit, output, seconds)
    guarded(door, repo, sha)    the lifeline and the run's record; `.rc` is the outcome
    worktree(top, path, tmp)    PATH registered with the lifeline while the block runs
    sweep(stamps, ...)          TERM, a grace, KILL: every process below the stamps, by birth

THE RECORD. Every run is one timing-ledger row of kind `land check` (`:rules`, `:enqueue`): its
seconds, `ok`, and `exit` -- 0 green, 1 refused, 128+N stopped by signal N, 137 killed (written by
the watcher: nobody else is left). `report` is `goh land-profile --checks`.

`goh land --check`'s jobs (GOH.CHECKVERDICT): `run` starts each in its own session under a deadline
and stops its whole tree there; `guarded` holds the lifeline -- a watcher process that, when the
check dies by any signal, stops every job's tree, removes the worktrees it registered and records
the run -- and writes each run's `land check` row (seconds, exit). Invoked by `lib/land_check.py`,
`lib/land_check_cli.py`, `lib/land_judges.py`, `lib/commit_stamp.py`, `lib/land_profile.py`.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [p for p in (str(ROOT), str(ROOT / "lib")) if p not in sys.path]
import pid_owner  # noqa: E402 -- lib/, on the path from the line above

from lib import proc_deadline  # noqa: E402 -- the module a test's stretch is patched on

STRETCH_CAP_S = 1500  # a job's longest bound
GRACE_S = 3.0  # what a TERMed tree is given to clean up before KILL
WATCHER_S = 30.0  # what the check waits for its watcher: grace, kills, worktree removals
KIND = "land check"
STOPS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
WATCH = "import sys; sys.path.insert(0, sys.argv[1]); import check_jobs; check_jobs.watch()"


def deadline(quiet_s: float, stretch: float) -> float:
    """QUIET_S stretched by STRETCH, never below it, never over STRETCH_CAP_S."""
    return max(quiet_s, min(STRETCH_CAP_S, quiet_s * stretch))


def _table() -> list[tuple[int, int, int]]:
    """(pid, ppid, pgid) of every process, from one `ps`."""
    out = subprocess.run(["ps", "-Ao", "pid=,ppid=,pgid="], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=False, timeout=30).stdout  # fmt: skip
    rows = (line.split() for line in out.splitlines())
    return [(int(a), int(b), int(c)) for a, b, c in (r for r in rows if len(r) == 3)]


def _below(seeds: set[int], table: list[tuple[int, int, int]]) -> set[int]:
    found, todo = set(seeds), list(seeds)
    while todo:
        at = todo.pop()
        for pid, ppid, _ in table:
            if ppid == at and pid not in found:
                found.add(pid)
                todo.append(pid)
    return found


def _mine(jobs: Iterable[pid_owner.Stamp], parent: pid_owner.Stamp | None) -> set[int]:
    """Every process of JOBS (session leaders): its group and all below, and all below PARENT; a
    number a stranger holds by now is nobody's, and a watcher spares itself."""
    table = _table()
    leaders = {s.number for s in jobs if pid_owner.same(s) or pid_owner.number_gone(s.number)}
    seeds = {pid for pid, _, pgid in table if pid in leaders or pgid in leaders}
    if parent is not None and pid_owner.same(parent):  # the watcher, under the check it watches
        seeds |= _below({parent.number}, table) - {parent.number} - _below({os.getpid()}, table)
    return _below(seeds, table) - {os.getpid()}


def sweep(
    jobs: Iterable[pid_owner.Stamp],
    parent: pid_owner.Stamp | None = None,
    grace: float = GRACE_S,
    tick: Callable[[], object] = lambda: None,
) -> list[int]:
    """Every process of JOBS (and below PARENT) TERMed, after GRACE KILLed, looked for again each
    round -- one started meanwhile, one re-parented after its parent died; what outlived it all."""
    jobs = list(jobs)
    held: dict[int, pid_owner.Stamp] = {}
    left: list[pid_owner.Stamp] = []
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0), (signal.SIGKILL, 2.0)):
        held.update((s.number, s) for s in map(pid_owner.stamp, _mine(jobs, parent)) if s)
        left = [s for s in held.values() if not pid_owner.gone(s)]
        for s in left:
            pid_owner.signal(s, sig)
        end = time.monotonic() + wait
        while left and time.monotonic() < end:
            time.sleep(0.05)
            tick()  # the caller reaps its own child: a zombie is gone
            left = [s for s in left if not pid_owner.gone(s)]
    return [s.number for s in left]


class Guard:
    """One check's lifeline (module doc): what it runs, told to its watcher as it starts."""

    def __init__(self, door: str, repo: str, sha: str) -> None:
        self.rc: int | None = None
        self.record = {"door": door, "repo": repo, "sha": sha, "epoch": time.time(),
                       "pid": os.getpid()}  # fmt: skip
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            [sys.executable, "-c", WATCH, str(ROOT / "lib"), json.dumps(self.record)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, start_new_session=True,
        )  # fmt: skip

    def send(self, **msg: object) -> None:
        with self._lock, contextlib.suppress(OSError, ValueError):
            assert self.proc.stdin is not None
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())
            self.proc.stdin.flush()

    def close(self, **msg: object) -> int | None:
        """The last word to the watcher, and its end within WATCHER_S."""
        self.send(**msg)
        with contextlib.suppress(OSError, ValueError):
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        return proc_deadline.wait(self.proc, WATCHER_S)

    def stopped(self, number: int, _frame: object) -> None:
        """A stop signal's handler: the watcher cleans and records, then this process ends."""
        for sig in STOPS:
            signal.signal(sig, signal.SIG_IGN)
        os.write(2, f"goh land --check: {signal.Signals(number).name}: its jobs and worktrees are "
                    "being removed\n".encode())  # fmt: skip
        # Never self._lock: the interrupted frame may hold it. A line the signal cut is skipped by
        # the watcher (the newline first), and the end of file alone already means "clean up".
        fd = self.proc.stdin.fileno() if self.proc.stdin is not None else -1
        with contextlib.suppress(OSError):
            os.write(fd, f"\n{json.dumps({'stop': int(number)})}\n".encode())
        proc_deadline.wait(self.proc, WATCHER_S)
        os._exit(128 + number)


_LIVE: Guard | None = None


@contextlib.contextmanager
def guarded(door: str, repo: str, sha: str, signals: bool = False) -> Iterator[Guard]:
    """The check's lifeline while the block runs, its row when it ends (`.rc`, else 1). SIGNALS
    (the CLI): a stop signal cleans up before the exit; else the process's end does."""
    global _LIVE  # noqa: PLW0603 -- one check a process
    g = Guard(door, repo, sha)
    was: dict[int, object] = {}
    if signals and threading.current_thread() is threading.main_thread():
        was = {s: signal.signal(s, g.stopped) for s in STOPS}
    _LIVE = g
    try:
        yield g
    finally:
        _LIVE = None
        for s, handler in was.items():
            signal.signal(s, handler)  # type: ignore[arg-type]
        note(g.record, 1 if g.rc is None else g.rc)
        g.close(end=1)


@contextlib.contextmanager
def worktree(top: str | Path, path: Path, tmp: Path) -> Iterator[None]:
    """PATH, a worktree of TOP made in the block under its own directory TMP: both are the
    lifeline's to remove if the check dies."""
    g = _LIVE
    if g:
        g.send(tree=[str(top), str(path), str(tmp)])
    try:
        yield
    finally:
        if g:
            g.send(untree=str(path))


def run(
    argv: list[str],
    root: Path,
    env: dict[str, str],
    timeout: float,
    quiet: float | None = None,
    split: bool = False,
) -> tuple[int, str, float]:
    """ARGV in its own session, its output in a file (a child that outlives it holds no pipe), its
    whole tree stopped at TIMEOUT (`sweep`): (exit, 124 at the deadline; the output; seconds).
    QUIET (a job's quiet bound): at the deadline the clamp is measured again and the deadline moves
    to QUIET stretched by it, while that is later (the jobs' own load is not there at the start).
    SPLIT: stdout alone, stderr after it only when ARGV failed (a list read by the line)."""
    limit, t0, g = timeout, time.monotonic(), _LIVE
    with tempfile.TemporaryFile() as log, tempfile.TemporaryFile() as errs:
        proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=errs if split else subprocess.STDOUT,
                                start_new_session=True)  # fmt: skip
        me = pid_owner.stamp(proc.pid)
        if g:
            g.send(pid=proc.pid)
        try:
            rc = proc_deadline.wait(proc, limit)
            while rc is None and quiet:
                later = deadline(quiet, proc_deadline.stretch())
                if later <= time.monotonic() - t0 + 1:
                    break
                limit = later
                rc = proc_deadline.wait(proc, limit - (time.monotonic() - t0))
        except BaseException:
            _end(proc, me)
            raise
        if rc is None:
            _end(proc, me)
            rc = 124
        if g:
            g.send(gone=proc.pid)
        log.seek(0)
        out = log.read().decode(errors="replace")
        if split and rc:
            errs.seek(0)
            out += errs.read().decode(errors="replace")
    if rc == 124:
        out += f"\ntimed out after {limit} s"
    return rc, out, round(time.monotonic() - t0, 1)


def _end(proc: subprocess.Popen, me: pid_owner.Stamp | None) -> None:
    sweep([me] if me else [], tick=proc.poll)
    with contextlib.suppress(OSError):
        proc.kill()
    proc.wait()


def note(record: dict, rc: int) -> None:
    """The run's row (module doc, THE RECORD); never the reason a check fails."""
    import timing_ledger  # lib/: goh.db's spans, through their one writer

    door = record["door"]
    with contextlib.suppress(Exception):
        timing_ledger.append(timing_ledger.row_for(
            f"{KIND}:{door}" if door else KIND, timing_ledger.repo_name(record["repo"]),
            time.time() - record["epoch"], ok=rc == 0, exit=rc, sha=record["sha"][:12],
            ref=f"{record['pid']}-{record['epoch']:.3f}",
        ))  # fmt: skip


def _git(top: str, *words: str) -> bool:
    import gitrun  # lib/: a writer stopped at a deadline drops its locks (GOH.GITKILL)

    with contextlib.suppress(OSError, subprocess.SubprocessError):
        done = gitrun.run_writer(words, repo=top, deadline_s=60)
        return done is not None and done.returncode == 0
    return False


def watch() -> None:
    """The watcher (module doc): its lines from the check, then, unless it said `end`, the sweep."""
    record = json.loads(sys.argv[2])
    parent = pid_owner.stamp(record["pid"])
    jobs: dict[int, pid_owner.Stamp] = {}
    trees: dict[str, list[str]] = {}
    number = int(signal.SIGKILL)
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "end" in msg:
            return
        if "pid" in msg and (s := pid_owner.stamp(msg["pid"])):
            jobs[msg["pid"]] = s
        jobs.pop(msg.get("gone"), None)
        if "tree" in msg:
            trees[msg["tree"][1]] = msg["tree"]
        trees.pop(msg.get("untree"), None)
        if "stop" in msg:
            number = msg["stop"]
            break
    sweep(jobs.values(), parent)
    for top, path, tmp in trees.values():  # forced twice: an add cut half way left it locked
        gone = _git(top, "worktree", "remove", "--force", "--force", path)
        shutil.rmtree(tmp, ignore_errors=True)
        if not gone:  # nothing git could remove: its registration goes with its directory
            _git(top, "worktree", "prune")
    note(record, 128 + number)


def report(rows: list[dict]) -> list[str]:
    """One line per door of ROWS (`land check` ledger rows): runs, seconds, how they ended."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(f"{r.get('repo')} {r['kind']}", []).append(r)
    out = []
    for name, runs in sorted(by.items()):
        walls = sorted(float(r.get("wall_s") or 0) for r in runs)
        ends: dict[str, int] = {}
        for r in runs:
            code = int(r.get("exit") or 0)
            how = {0: "green", 1: "refused"}.get(code, f"stopped ({code})")
            ends[how] = ends.get(how, 0) + 1
        out.append(f"{name}: {len(runs)} runs, p50 {walls[len(walls) // 2]:.1f} s, p90 "
                   f"{walls[len(walls) * 9 // 10]:.1f} s, {sum(walls) / 3600:.2f} h; "
                   + ", ".join(f"{n} {how}" for how, n in sorted(ends.items())))  # fmt: skip
    return out
