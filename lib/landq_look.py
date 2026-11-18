"""Reading the landing queue (split from tools/landq.py, GOH.CAPHEADROOM): `goh land --status`,
and `--wait`, which finds a request wherever it is now and blocks until it is done. An enqueue of
the item still in its pre-step (lib/landq_inflight.py) is waited for until it files; with neither,
the wait exits 2 (GOH.WAITUNFILED). Pinned by tests/test_landq.py, tests/test_landq_inflight.py."""

from __future__ import annotations

import importlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

landq_inflight = importlib.import_module("landq_inflight")
_tui = importlib.import_module("tui.lib")
err, info, ok = _tui.err, _tui.info, _tui.ok
poll = 2.0


def status(q: Path) -> int:
    for d in ("pending", "running"):
        for f in sorted((q / d).glob("*.json")) if (q / d).is_dir() else []:
            r = json.loads(f.read_text())
            info(
                f"{d}: {r.get('item') or '-'} {Path(r['repo']).name} @ {r['sha'][:8]} ({r['mode']})"
            )
    for f in sorted((q / "done").glob("*.json"))[-10:] if (q / "done").is_dir() else []:
        r = json.loads(f.read_text())
        info(f"done: {f.stem} {r['state']} @ {r['sha'][:8]} {r.get('log', '')}")
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


def unfiled(q: Path, what: str, start_by: float) -> Path | int | None:
    """WHAT's request; while there is none, wait out an enqueue of it still in its pre-step
    (GOH.WAITUNFILED): None when neither exists, 3 past START_BY."""
    said = False
    while (found := find_request(q, what)) is None:
        if not landq_inflight.live(q, what):
            return find_request(q, what)  # it filed, then exited, between the two looks
        if time.monotonic() > start_by:
            return 3
        if not said:
            info(f"goh land --wait: {what}'s enqueue is in its pre-step; waiting for its request")
            said = True
        time.sleep(poll)
    return found


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
    nudged = 0.0
    while not (q / "done" / name).is_file():
        if time.monotonic() > start_by:
            err(f"goh land --wait: {what} ({name}) has not finished after {timeout:.0f}s")
            return 3
        if (q / "pending" / name).is_file() and time.monotonic() - nudged > 30:
            nudge()  # nobody drains: start the lander
            nudged = time.monotonic()
        time.sleep(poll)
    r = json.loads((q / "done" / name).read_text())
    sha = r.get("landed") or r.get("sha", "")
    line = f"{what}: {r['state']} @ {sha[:8]} ({r.get('mode', '?')}) log {r.get('log', '')}"
    if r["state"] == "landed":
        ok(line)
        return 0
    err(f"{line}: {r.get('why', '')}")
    log = Path(r.get("log") or "/nonexistent")
    for tail in log.read_text(errors="replace").splitlines()[-30:] if log.is_file() else []:
        print(f"  {tail}", file=sys.stderr)
    return 1
