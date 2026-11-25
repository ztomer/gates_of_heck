"""loop_stale.py -- .loop's stale-agent lines, asked of its CLI (GOH.STALEHOOK).

LOOP.STALEAGENT (.loop acf1bef) added two hard tick lines: a closed item whose agent still runs, and
an agent whose transcript began over 2 h ago (.loop tools/tickage.py). The per-turn agent-stalls
hook did not show them, so the coordinator saw them only on a tick. .loop owns that rule; this only
ASKS its CLI (`loopctl stale --projects <root>`, LOOP.STALECLI), never copies it. It reads the
process table (~3 s), so agent_stalls starts it before its own scan and collects it after.
GOH_LOOPCTL names the CLI (default ~/Projects/.loop/tools/loopctl.py); none there, nothing asked.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import state_paths  # lib/, on the path from the line above

LOOPCTL_TIMEOUT_S = 15.0


def loopctl_path() -> Path:
    return state_paths.loopctl()


def start_loop_stale(projects: Path) -> subprocess.Popen[str] | None:
    """`loopctl stale --projects <root>`, started; None where there is no .loop to ask."""
    path = loopctl_path()
    if not path.is_file():
        return None
    try:
        return subprocess.Popen([sys.executable, str(path), "stale", "--projects", str(projects)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True)  # fmt: skip
    except OSError as e:
        print(f"agent_stalls: loopctl stale did not start: {e}", file=sys.stderr)
        return None


def loop_stale_lines(proc: subprocess.Popen[str] | None) -> list[str]:
    """Its lines, on exit 0 (none) or 10 (some). Any other end is said on stderr and never reaches
    the prompt: the hook never fails a prompt, and a broken CLI is not a stale agent."""
    if proc is None:
        return []
    try:
        limit = float(os.environ.get("GOH_LOOPCTL_TIMEOUT_S", LOOPCTL_TIMEOUT_S))
    except ValueError:
        limit = LOOPCTL_TIMEOUT_S
    try:
        out, errs = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        print(f"agent_stalls: loopctl stale ran past {limit:g}s; killed", file=sys.stderr)
        return []
    if proc.returncode not in (0, 10):
        print(f"agent_stalls: loopctl stale exited {proc.returncode}: {errs.strip()[-300:]}",
              file=sys.stderr)  # fmt: skip
        return []
    return [ln for ln in out.splitlines() if ln.strip()]
