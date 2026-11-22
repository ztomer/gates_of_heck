"""Reading the landing queue (split from tools/landq.py, GOH.CAPHEADROOM): `goh land --status`,
and `--wait`, which finds a request wherever it is now and blocks until it is done. An enqueue of
the item still in its pre-step (lib/landq_inflight.py) is waited for until it files; with neither,
the wait exits 2 (GOH.WAITUNFILED); a done record while one is in flight is the previous
attempt's, never returned (GOH.WAITSTALE). Each tick re-reads where the request is and says so
once per change: queued behind whom, running (a batch's gate N), done (GOH.LANDWAITSTALE).
Every record is read through lib/landq_state.py: one moved or not whole is read again next
tick, never a crash (GOH.CIRED01310). Pinned by tests/test_landq.py, tests/test_landq_inflight.py,
tests/test_landq_look.py, tests/test_landq_state.py."""

from __future__ import annotations

import importlib
import sys
import time
from collections.abc import Callable
from pathlib import Path

landq_inflight = importlib.import_module("landq_inflight")
landq_state = importlib.import_module("landq_state")
_tui = importlib.import_module("tui.lib")
err, info, ok = _tui.err, _tui.info, _tui.ok
poll = 2.0


def status(q: Path) -> int:
    for d in ("pending", "running"):
        for f in sorted((q / d).glob("*.json")) if (q / d).is_dir() else []:
            if (r := landq_state.read(f)) is None:
                continue  # moved on, or not whole yet: the next look shows it
            repo, sha = Path(str(r.get("repo") or "-")).name, str(r.get("sha") or "-")[:8]
            info(f"{d}: {r.get('item') or '-'} {repo} @ {sha} ({r.get('mode', '?')})")
    for f in sorted((q / "done").glob("*.json"))[-10:] if (q / "done").is_dir() else []:
        if (r := landq_state.read(f)) is None:
            continue
        info(f"done: {f.stem} {r.get('state', '?')} @ {str(r.get('sha') or '-')[:8]} "
             f"{r.get('log', '')}")  # fmt: skip
    return 0


def find_request(q: Path, what: str) -> Path | None:
    """WHAT's newest request, wherever it is now: a request's path or name, else an item ID."""
    name = Path(what).name.removesuffix(".json")
    newest: tuple[str, Path] | None = None
    for d in ("pending", "running", "done"):
        for f in (q / d).glob("*.json") if (q / d).is_dir() else []:
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
    if found is not None and found.parent.name == "done" and landq_inflight.live(q, what):
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


def _names(d: Path) -> list[str]:
    return sorted(f.name for f in d.glob("*.json")) if d.is_dir() else []


def _items(d: Path, names: list[str]) -> dict[str, str]:
    """Each of NAMES in D that is still there and whole, by name: its item."""
    out = {}
    for n in names:
        if (r := landq_state.read(d / n)) is not None:  # else moved or not whole: read next tick
            out[n] = r.get("item") or n.removesuffix(".json")
    return out


def where(q: Path, name: str) -> str:
    """Where request NAME is now: queued behind whom, or running (a batch's gate N); "" else.
    Who is ahead comes from ONE listing of pending, read before running's: a request the lander
    moves on meanwhile is read in running, by name once, and NAME is never ahead of itself
    (GOH.FLAKY2: a second listing shifted the slice, "queued behind T.AHEAD, T.S")."""
    pend = _names(q / "pending")
    if name in pend:
        queued = _items(q / "pending", pend[: pend.index(name)])
        running = _items(q / "running", [n for n in _names(q / "running") if n != name])
        ahead = list({**running, **queued}.values())
        return f"queued behind {', '.join(ahead)}" if ahead else "queued, next"
    run = sorted((q / "running").glob("*.json")) if (q / "running").is_dir() else []
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
    """Block until WHAT's newest request is done; exit by its outcome (module doc, THE WAIT)."""
    start_by = time.monotonic() + timeout
    found = unfiled(q, what, start_by)
    if found == 3:
        err(f"goh land --wait: {what}'s enqueue filed nothing after {timeout:.0f}s")
        return 3
    if found is None:
        err(f"goh land --wait: no request for {what} under {q}, and no enqueue of it running")
        return 2
    assert isinstance(found, Path)
    name = found.name
    nudged, was = 0.0, ""
    while (r := landq_state.read(q / "done" / name)) is None:
        torn = (q / "done" / name).is_file()  # there, not whole: a torn write, read again
        now = "done, its record not whole yet" if torn else where(q, name)
        if now and now != was:
            info(f"goh land --wait: {what} is {now}")  # GOH.LANDWAITSTALE: each change, once
            was = now
        if time.monotonic() > start_by:
            whole = "; its done record is not a whole record" if torn else ""
            err(f"goh land --wait: {what} ({name}) has not finished after {timeout:.0f}s{whole}")
            return 3
        if (q / "pending" / name).is_file() and time.monotonic() - nudged > 30:
            nudge()  # nobody drains: start the lander
            nudged = time.monotonic()
        time.sleep(poll)
    sha = str(r.get("landed") or r.get("sha", ""))
    line = f"{what}: {r.get('state')} @ {sha[:8]} ({r.get('mode', '?')}) log {r.get('log', '')}"
    if r.get("state") == "landed":
        ok(line)
        return 0
    err(f"{line}: {r.get('why', '')}")
    log = Path(r.get("log") or "/nonexistent")
    for tail in log.read_text(errors="replace").splitlines()[-30:] if log.is_file() else []:
        print(f"  {tail}", file=sys.stderr)
    return 1
