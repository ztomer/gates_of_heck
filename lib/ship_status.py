"""`goh ship --status RESULT`: the marks a ship result holds (split from tools/ship.py at its
500-line cap)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import err, info, ok, step


def status(result: str) -> int:
    """Print the marks a result holds. 0 shipped, 1 failed, 2 unreadable, 3 running, 4 the worker
    is gone (killed, or the machine slept through a reboot) while the result still says running."""
    try:
        res = json.loads(Path(result).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        err(f"goh ship: cannot read {result}: {e}")
        return 2
    for s in res["steps"]:
        mark = {"ok": ok, "skipped": step}.get(s["status"], err)
        mark(f"{s['name']}: {s['status']}" + (f" ({s['why']})" if s.get("why") else ""))
    if res["status"] == "running":
        pid = res.get("pid")
        try:
            if pid:
                os.kill(pid, 0)
            info(f"running: {result}")
            return 3
        except OSError:
            err(f"the worker (pid {pid}) is gone and the result says running: {result}")
            return 4
    return 0 if res["status"] == "ok" else 1
