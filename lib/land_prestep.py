"""The land pre-step, run at enqueue (GOH.ENQUEUEPRE; tools/landq.py's THE ENQUEUE PRE-STEP).

It is land.sh's own pre-step: GOH_LAND_PRE_ONLY=1 stops the script before the land lock, so the
set (GOH_LAND_PRE), the short lane and the deadline (GOH_LAND_PRE_MAX) are the lander's. A red
set raises Red naming the failing tests; a deadline is no verdict (GOH.PRESTEPDEADLINE), so the
tip queues and a line says so. Pinned by tests/test_landq.py.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

killtree = importlib.import_module("killtree")
land_slot = importlib.import_module("land_slot")
landq_inflight = importlib.import_module("landq_inflight")
_tui = importlib.import_module("tui.lib")
info, warn = _tui.info, _tui.warn

# land.sh bounds the set at its share's deadline (lib/pre_deadline.py: at most GOH_LAND_PRE_CEIL,
# 25 min) after a wait for a short-lane slot.
TIMEOUT = 30 * 60


class Red(Exception):
    pass


def keep_path(q: Path, sha: str) -> Path:
    """Where one enqueue keeps its verdict (GOH.PRELANDTWICE): under the queue's `pre/`."""
    (q / "pre").mkdir(parents=True, exist_ok=True)
    return q / "pre" / f"{sha}-{time.time_ns()}"


def adopt(req: dict, keep: Path) -> None:
    """Put a kept green verdict on the request: `keep` names the very sha enqueued, or nothing."""
    with contextlib.suppress(OSError):
        if keep.read_text().split() == [req["sha"]]:
            req["pre"] = {"judged": req["sha"], "keep": str(keep)}


def discard(req: dict) -> None:
    """REQ's kept verdict and its record removed once REQ is recorded: land.sh removes what it
    reads, but a batch item whose gate judged another tree, or a land refused before its pre-step,
    never read its own, and no later land is that enqueue's (GOH.BATCHPREFROM)."""
    if keep := (req.get("pre") or {}).get("keep"):
        for path in (Path(keep), Path(f"{keep}.record")):
            path.unlink(missing_ok=True)


def reap(q: Path, now_ns: int | None = None) -> list[Path]:
    """Every kept verdict under Q's `pre/` whose request is gone, removed with its record; the
    paths removed (GOH.LANDLEAKS: 14 files kept before `discard` existed outlived their lands).

    A verdict is a request's while a request in `pending/` or `running/` names it, and an
    enqueue's while it is younger than the enqueue's own bound (its name carries its birth in ns,
    lib/landq_inflight.py's pre-step bound plus margin): the pre-step keeps it BEFORE the request
    is filed. A record outlives no verdict of its own."""
    named: set[str] = set()
    for d in ("pending", "running"):
        for path in (q / d).glob("*.json") if (q / d).is_dir() else []:
            with contextlib.suppress(OSError, ValueError, AttributeError, TypeError):
                if keep := (json.loads(path.read_text()).get("pre") or {}).get("keep"):
                    named.add(Path(keep).name)
    bound = int(os.environ.get("GOH_ENQUEUE_PRE_TIMEOUT") or landq_inflight.PRE_TIMEOUT_S)
    oldest = (now_ns or time.time_ns()) - (bound + landq_inflight.MARGIN_S) * 10**9
    gone: list[Path] = []
    for path in sorted((q / "pre").iterdir()) if (q / "pre").is_dir() else []:
        keep = path.with_name(path.name.removesuffix(".record"))
        born = keep.name.rpartition("-")[2]
        if keep.name in named or (born.isdigit() and int(born) > oldest):
            continue
        with contextlib.suppress(OSError):
            path.unlink()
            gone.append(path)
    return gone


def lander_env(req: dict) -> list[str]:
    """The lander's tools/land.sh reads the kept verdict from GOH_LAND_PRE_FROM."""
    kept = [f"GOH_LAND_PRE_FROM={req['pre']['keep']}"] if req.get("pre") else []
    return [*kept, *land_slot.label(req)]


def run_kept(req: dict, q: Path, script: Path, bindings: tuple[str, ...]) -> float:
    """`run` on REQ's worktree and sha, its green verdict kept under Q and named on REQ (`pre`);
    the lander's land.sh removes the keep once read (lib/land_pre_reuse.sh)."""
    keep = keep_path(q, req["sha"])
    took = run(Path(req["worktree"]), req["sha"], script, bindings, keep, land_slot.label(req))
    adopt(req, keep)
    return took


def run(
    wt: Path,
    sha: str,
    script: Path,
    bindings: tuple[str, ...],
    keep: Path | None = None,
    label: list[str] | None = None,
) -> float:
    """Run SCRIPT's pre-step in WT; its seconds, or raise Red unless green or past its deadline. A
    green verdict is KEPT at `keep` (the judged sha; its record at `keep`.record) for the lander's
    tools/land.sh, which reuses it where the tip moved only in paths no test reads
    (GOH.PRELANDTWICE)."""
    kept = [f"GOH_LAND_PRE_KEEP={keep}"] if keep else []
    kept += label or []
    argv = ["env", *(f"-u{k}" for k in bindings), "GOH_LAND_PRE_ONLY=1", *kept, "bash", "-c",
            'cd -- "$1" && exec bash "$2"', "land", str(wt), str(script)]  # fmt: skip
    timeout = int(os.environ.get("GOH_ENQUEUE_PRE_TIMEOUT") or TIMEOUT)
    t0 = time.monotonic()
    info(f"pre-step before queueing {sha[:8]} (the lander's own, in {wt})")
    lines: list[str] = []
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)  # fmt: skip
    timer = threading.Timer(timeout, lambda: killtree.stop_group(proc.pid, proc=proc))
    timer.start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            lines.append(line)
            sys.stderr.write(line)
        code = proc.wait()
    finally:
        timer.cancel()
    if code == 0:
        if any("no verdict" in line for line in lines):
            warn("the pre-step reached its deadline -- no verdict; queued, the lander decides")
        return round(time.monotonic() - t0, 1)
    named = [ln.split()[1] for ln in lines if ln.startswith("FAILED ") and len(ln.split()) > 1]
    what = f"failing: {', '.join(named)}" if named else f"exit {code}, see the output above"
    raise Red(
        f"the pre-land tests failed on {sha[:8]} ({what}) -- fix them, or "
        "--no-pre REASON when this tip's set was run another way"
    )
