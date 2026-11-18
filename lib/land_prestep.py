"""The land pre-step, run at enqueue (GOH.ENQUEUEPRE; tools/landq.py's THE ENQUEUE PRE-STEP).

It is land.sh's own pre-step: GOH_LAND_PRE_ONLY=1 stops the script before the land lock, so the
set (GOH_LAND_PRE), the short lane and the deadline (GOH_LAND_PRE_MAX) are the lander's. A red
set raises Red naming the failing tests; a deadline is no verdict (GOH.PRESTEPDEADLINE), so the
tip queues and a line says so. Pinned by tests/test_landq.py.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

killtree = importlib.import_module("killtree")
_tui = importlib.import_module("tui.lib")
info, warn = _tui.info, _tui.warn

# land.sh bounds the set at its share's deadline (lib/pre_deadline.py: at most GOH_LAND_PRE_CEIL,
# 25 min) after a wait for a short-lane slot.
TIMEOUT = 30 * 60


class Red(Exception):
    pass


def run(wt: Path, sha: str, script: Path, bindings: tuple[str, ...]) -> float:
    """Run SCRIPT's pre-step in WT; its seconds, or raise Red unless green or past its deadline."""
    argv = ["env", *(f"-u{k}" for k in bindings), "GOH_LAND_PRE_ONLY=1", "bash", "-c",
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
