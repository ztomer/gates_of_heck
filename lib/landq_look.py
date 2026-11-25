"""Reading the landing queue (split from tools/landq.py, GOH.CAPHEADROOM): `goh land --status`,
and `--wait`, which finds a request wherever it is now and blocks until it is done. An enqueue of
the item still in its pre-step (lib/landq_inflight.py) is waited for until it files; with neither,
the wait exits 2 (GOH.WAITUNFILED); a done record while one is in flight is the previous
attempt's, never returned (GOH.WAITSTALE): the enqueue is marked in flight from its first step,
not its pre-step's, and a wait past --timeout before it files says "pre-step running", exit 3
(GOH.WAITSTALE2). Each tick re-reads where the request is and says so
once per change: queued behind whom, running (a batch's gate N), done (GOH.LANDWAITSTALE).
Every request is read through lib/landq_state.py and every record through lib/land_records.py,
goh.db rows both: one moved is read again next tick, never a crash (GOH.CIRED01310). Pinned
by tests/test_landq.py, tests/test_landq_inflight.py, tests/test_landq_look.py,
tests/test_landq_state.py.

THE EXIT IS THE RECORD'S (GOH.WAITEARLY, 2026-11-21): 0 only when the done record says landed, 1
only when it says red, 3 on every other way out -- past --timeout, a SIGTERM, SIGINT or SIGHUP
(a SIGINT a background job inherited ignored included), a record in no state this knows; 2 is
still "no request and no enqueue". A reader gone (a closed stdout) silences the rest and never
ends the wait or turns it red. The last line names the exit, so the output alone carries it: a
zsh tool shell's `${PIPESTATUS[0]}` is empty and `cmd | tail` exits with tail's 0. A state that
does not change is said again every `heartbeat` seconds with how long, so a batch member's wait
shows its gate and its time (GOH.DIFFCOVCARRY's said "gate 1 of a batch of 3" for 15 min).
Pinned by tests/test_landq_wait_exit.py.

Reading the landing queue, split from `tools/landq.py` (GOH.CAPHEADROOM): `goh land --status`, and
`--wait`, which finds a request by path, name or item ID wherever it is now and blocks until done
(exit 0 landed, 1 red with the log tail, 3 timed out); an enqueue of the item still in its pre-step
is waited for until it files, and exit 2 means none runs and none is filed (GOH.WAITUNFILED).
Invoked by `tools/landq.py`. Pinned by `test_landq_inflight.py`.

`lib/landq_report.py`: `goh land --report` (GOH.LANDREPORT): where the landing queue's wall time
goes, read from `done/*.json` and their logs. Per repo and UTC day of the finish: attempts, landed,
red and `cause` (the reds counted by `lib/land_cause.py`'s reading, the commonest in the text
table's column and all of them on the line under it), queue wait (enqueue to start), gate (start to
done) split into slot wait and run, mean load at start, gates per landed, `unknown` (attempts with
no trustworthy start: an older record whose log was born before its request or after its finish, or
shared by two records, is never summed as a negative). The lander writes `started`/`finished` (ISO,
ms), `slot_wait_s`, `load_start`, `load_end` into each new done record. JSON: `{since, unloaded_s,
rows: [{repo, day, attempts, landed, red, cause, queue_wait_s, gate_s, slot_wait_s, run_s,
load_avg, gates, gates_per_landed, unknown, remote}], totals, unclassified: [{item, repo, day,
log}], gate_steps: {step: n}}`; `remote` (GOH.REMOTEMUX) counts the pre-steps' worker shares from
each record's `pre_run.remote`: `{shares, fallbacks: {cause: n}, fresh}`, printed under the row;
`gates` counts a batch's shared gate runs once (GOH.LANDBATCH); `unloaded_s` is the shortest green
run in the window. `selection` (GOH.SELECTAUDIT): the selection-miss ledger by day, `{day: {judged,
missed, tests, full, full_missed}}`, also printed under the table. Not yet in `goh budget`: its
`landq wait` / `landq run` kinds have no writer, which needs the lander to append ledger rows with
`GOH_TIMING_DIR` isolated in every test that drives it.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

land_records = importlib.import_module("land_records")  # the land records (GOH.STATEDB)
landq_inflight = importlib.import_module("landq_inflight")
landq_state = importlib.import_module("landq_state")
terse = importlib.import_module("terse")
_tui = importlib.import_module("tui.lib")
err, info, ok = _tui.err, _tui.info, _tui.ok
poll = 2.0
heartbeat = 60.0  # an unchanged state is said again this often, with how long (GOH.WAITEARLY)
STOPS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


class Stopped(Exception):
    """A signal ended the wait: exit 3, never the shell's 128+N nor a KeyboardInterrupt's 1."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def _stop(signum: int, _frame: object) -> None:
    raise Stopped(signum)


@contextlib.contextmanager
def _signals_end_it():
    """STOPS raise Stopped while the wait runs; a SIG_IGN inherited from `cmd &` is overridden."""
    if threading.current_thread() is not threading.main_thread():
        yield  # signal.signal is the main thread's alone; a test's thread has no signals
        return
    old = {s: signal.signal(s, _stop) for s in STOPS}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def _say(say: Callable[[str], None], text: str) -> None:
    """SAY TEXT; its reader gone (a closed pipe), that stream goes to /dev/null from here on."""
    try:
        say(text)
    except BrokenPipeError:
        stream = sys.stderr if say in (err, _eprint) else sys.stdout
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, stream.fileno())  # the buffered rest flushes there, not into an EPIPE
        os.close(devnull)


def _eprint(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _mins(s: float) -> str:
    """S seconds as m:ss."""
    m, sec = divmod(int(s), 60)
    return f"{m}:{sec:02d}"


def _age(name: str) -> str:
    """'requested m:ss ago; ' -- the land's own age, from its request name's time_ns prefix
    (GOH.WAITELAPSED: the call's own wait read the same on every call), or ""."""
    try:
        return f"requested {_mins(time.time() - int(name.split('-', 1)[0]) / 1e9)} ago; "
    except ValueError:
        return ""


def status(q: Path) -> int:
    for d in ("pending", "running"):
        for _, r in landq_state.rows(q, d):  # one read per state (GOH.LANDQDB)
            repo, sha = Path(str(r.get("repo") or "-")).name, str(r.get("sha") or "-")[:8]
            info(f"{d}: {r.get('item') or '-'} {repo} @ {sha} ({r.get('mode', '?')})")
    for run, r in land_records.records(q)[-10:]:
        info(f"done: {run} {r.get('state', '?')} @ {str(r.get('sha') or '-')[:8]} "
             f"{r.get('log', '')}")  # fmt: skip
    return 0


def find_request(q: Path, what: str) -> Path | None:
    """WHAT's newest request, wherever it is now: a request's path or name, else an item ID."""
    name = Path(what).name.removesuffix(".json")
    newest: tuple[str, Path] | None = None
    # "early": a red enqueue's request until its record is written (GOH.WAITEARLYGAP); in the order
    # a request moves, so one moving on mid-look is still met
    queued = [f for d in ("pending", "running", "early") for f in landq_state.listing(q, d)]
    for f in [*queued, *(land_records.ref(q, run) for run in land_records.runs(q))]:
        if f.stem == name:
            return f
        mine = f.stem.partition("-")[2] == what.replace("/", "_")  # `<ns>-<item>`, exactly
        if mine and (not newest or f.name > newest[0]):
            newest = (f.name, f)
    return newest[1] if newest else None


def _current(q: Path, what: str) -> Path | None:
    """WHAT's newest request, unless it is a done record while an enqueue of WHAT is still in its
    pre-step: that record is the PREVIOUS attempt's, and the enqueue files the one to wait for
    (GOH.WAITSTALE: a re-enqueue's wait returned try 2's red)."""
    found = find_request(q, what)
    if found is not None and found.parent.name == land_records.REF and landq_inflight.live(q, what):
        return None
    return found


def unfiled(q: Path, what: str, start_by: float) -> Path | int | None:
    """WHAT's request; while there is none, wait out an enqueue of it still in its pre-step
    (GOH.WAITUNFILED): None when neither exists, 3 past START_BY."""
    said = False
    while (found := _current(q, what)) is None:
        if not landq_inflight.live(q, what):
            return find_request(q, what)  # it filed, then exited, between the two looks
        if time.monotonic() > start_by:
            return 3
        if not said:
            info(f"goh land --wait: {what}'s enqueue is in its pre-step; waiting for its request")
            said = True
        time.sleep(poll)
    return found


def _names(q: Path, state: str) -> list[str]:
    """The names of queue Q's requests in STATE, oldest first."""
    return [f.name for f in landq_state.listing(q, state)]


def _items(q: Path, state: str, names: list[str]) -> dict[str, str]:
    """Each of NAMES still in STATE, by name: its item."""
    out = {}
    for n in names:
        if (r := landq_state.get(landq_state.name(q, state, n.removesuffix(".json")))) is not None:
            out[n] = r.get("item") or n.removesuffix(".json")
    return out


def where(q: Path, name: str) -> str:
    """Where request NAME is now: queued behind whom, or running (a batch's gate N); "" else.
    Who is ahead comes from ONE listing of pending, read before running's: a request the lander
    moves on meanwhile is read in running, by name once, and NAME is never ahead of itself
    (GOH.FLAKY2: a second listing shifted the slice, "queued behind T.AHEAD, T.S")."""
    pend = _names(q, "pending")
    if name in pend:
        queued = _items(q, "pending", pend[: pend.index(name)])
        running = _items(q, "running", [n for n in _names(q, "running") if n != name])
        ahead = list({**running, **queued}.values())
        return f"queued behind {', '.join(ahead)}" if ahead else "queued, next"
    run = landq_state.listing(q, "running")
    if not any(f.name == name for f in run):
        return ""
    gates = (
        [
            g.name.rpartition(".gate")[2].removesuffix(".log")
            for g in (q / "logs").glob(f"{run[0].stem}.gate*.log")
        ]
        if (q / "logs").is_dir()
        else []
    )
    n = max((int(g) for g in gates if g.isdigit()), default=0)
    if len(run) == 1:
        return "running" + (f", gate {n}" if n else "")
    return f"running, {f'gate {n}' if n else 'stacking'} of a batch of {len(run)}"


def wait(q: Path, what: str, timeout: float, nudge: Callable[[], None]) -> int:
    """Block until WHAT's newest request is done; exit by its outcome (module doc, THE WAIT, THE
    EXIT IS THE RECORD'S): the last line names the exit."""
    try:
        with _signals_end_it():
            code, why = _wait(q, what, timeout, nudge)
    except Stopped as e:
        name = signal.Signals(e.signum).name
        code, why = 3, f"stopped by {name} before its record said landed or red"
    _say(ok if code == 0 else err, f"goh land --wait {what}: exit {code}, {why}")
    return code


def _wait(q: Path, what: str, timeout: float, nudge: Callable[[], None]) -> tuple[int, str]:
    begun = time.monotonic()
    start_by = begun + timeout
    found = unfiled(q, what, start_by)
    if found == 3:  # GOH.WAITSTALE2: running, not stuck; the lane waits again
        return 3, f"pre-step running: its enqueue has filed nothing yet after {timeout:.0f}s"
    if found is None:
        return 2, f"no request for {what} under {q}, and no enqueue of it running"
    assert isinstance(found, Path)
    name = found.name
    nudged, was, since, told, gap = 0.0, "", begun, begun, heartbeat
    run = name.removesuffix(".json")
    while (r := land_records.get(q, run)) is None:
        now = where(q, name)  # a record is a goh.db row: whole or absent, never torn
        tick = time.monotonic()
        if now and now != was:
            _say(info, f"goh land --wait: {what} is {now}")  # GOH.LANDWAITSTALE: each change, once
            was, since, told, gap = now, tick, tick, heartbeat
        elif now and tick - told >= gap:  # unchanged: again, with how long (GOH.WAITEARLY)
            _say(info, f"goh land --wait: {what} is still {now}, for {_mins(tick - since)} "
                       f"({_age(name)}waited {_mins(tick - begun)})")  # fmt: skip
            told, gap = tick, terse.next_gap(gap)  # backs off: a long land is not 60 lines
        if tick > start_by:
            return 3, f"{name} has not finished after {timeout:.0f}s"
        if landq_state.state_of(q, run) == "pending" and time.monotonic() - nudged > 30:
            nudge()  # nobody drains: start the lander
            nudged = time.monotonic()
        time.sleep(poll)
    sha = str(r.get("landed") or r.get("sha", ""))
    state = r.get("state")
    line = f"{what}: {state} @ {sha[:8]} ({r.get('mode', '?')}) log {r.get('log', '')}"
    if state == "landed":
        _say(ok, line)
        return 0, f"landed @ {sha[:8]}"
    if state != "red":
        _say(err, line)
        return 3, f"its record's state is {state!r}, neither landed nor red"
    why = str(r.get("why") or "")
    log = Path(r.get("log") or "/nonexistent")
    text = log.read_text(errors="replace") if log.is_file() else ""
    if terse.full():  # --full: the record's whole why, then the log whole
        text = f"{why}\n{text}"
    first, *said = terse.report(f"{line}: {why.splitlines()[0] if why else ''}", text, str(log))
    _say(err, first)  # GOH.TERSEOUT: a summary, the failing tests and the log path (lib/terse.py)
    for tail in said:
        _say(_eprint, tail)
    return 1, f"red @ {sha[:8]}"
