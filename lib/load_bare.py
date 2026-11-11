"""A gate or a build run BARE while the load slot is held (GOH.LOADBUILDLOCK, 2026-11-11).

THE CASE. Two agents ran `swift test` through `tools/load.py` and met two refusals in a row
(routines RT.DEFAULTSLEAK, transcript agent-aa13b5c3d03e9fd25, 17:35Z):

    load.py --wait --wait-max 2700 1 -- swift test ...                  -> exit 2, "the command is
        a build tool that takes a machine-wide lock (swift test) ... pass `--shared-lock-ok`"
    load.py --wait --wait-max 2700 --shared-lock-ok "..." 0 -- swift test  -> exit 3, "another load
        experiment already holds the slot ... owner pid 24457 ... command: tools/land.sh"

The second was the load.py of before GOH.LOADREFUSE (177712d, committed 12:33:06 -0500; land.sh
pid 24457, started 12:33:07, was landing it): it refused a held slot even under --wait. Today a
0-spinner run waits for the slot. But both agents then ran `swift test` with no load.py at all,
and nothing said a word: a bare build beside a held slot is the queue skipped, and the run it
sits beside measures a machine busier than it recorded.

So the Bash guard asks, of a command that runs a lock-taking build (`lib/load_locks.py`'s one
definition) or a gate script (`gate.sh`) outside `load.py`: is the load slot held by a live owner
that is not this process's own ancestor (a gate started under `load.py 0 -- tools/land.sh` runs
its builds inside the slot it holds)? Then it is refused, naming the holder and the line that
waits for the slot instead: `python3 "$GOH_DIR/tools/load.py" --wait 0 -- <the command>`. The
escape is `# bare-ok: <reason>`.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import load_locks
import load_priority
import load_registry as reg

ESCAPE = re.compile(r"#\s*bare-ok:\s*\S")
GATE_SCRIPTS = {"gate.sh"}
SHELLS = {"bash", "sh", "zsh"}


def wait_line(command: list[str]) -> str:
    """The line that runs `command` the house way: queued for the slot, no spinners."""
    return f'python3 "$GOH_DIR/tools/load.py" --wait 0 -- {shlex.join(command)}'


def _gate_script(argv: list[str]) -> bool:
    if not argv:
        return False
    name = os.path.basename(argv[0])
    if name in SHELLS and len(argv) > 1 and not argv[1].startswith("-"):
        name = os.path.basename(argv[1])
    return name in GATE_SCRIPTS


def runs_bare(segment: list[str]) -> bool:
    """Does this simple command run a build or a gate with no `load.py` in front of it?"""
    if any(os.path.basename(tok) == "load.py" for tok in segment):
        return False
    if load_locks.locking_command(segment):
        return True
    return _gate_script(segment) or any(_gate_script(r) for r in load_priority.operands(segment))


def _ancestors() -> set[int]:
    """This process's ancestors' pids (the hook's own chain), by `ps`."""
    out: set[int] = set()
    pid = os.getppid()
    while pid > 1 and pid not in out:
        out.add(pid)
        try:
            argv = ["ps", "-o", "ppid=", "-p", str(pid)]
            ran = subprocess.run(argv, capture_output=True, text=True, timeout=5,
                                 stdin=subprocess.DEVNULL, check=False)  # fmt: skip
            pid = int(ran.stdout.strip() or 0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            break
    return out


def holder(directory: Path | None = None) -> dict | None:
    """The registry of the live experiment holding the slot, unless it is this process's own
    ancestor; None when the slot is free, its owner is gone, or the registry cannot be read."""
    try:
        files = reg.registry_files(directory)
    except (OSError, RuntimeError):
        return None
    for path in files:
        data = reg.read(path)
        if not isinstance(data, dict) or reg.owner_gone(data):
            continue
        if int(data.get("owner_pid", 0)) in _ancestors():
            return None
        return data
    return None


def _describe(data: dict) -> str:
    command = data.get("command")
    shown = shlex.join(str(c) for c in command) if isinstance(command, list) else "?"
    pids = data.get("pids")
    spinners = len(pids) if isinstance(pids, list) else 0
    noun = "spinner" if spinners == 1 else "spinners"
    return f"pid {data.get('owner_pid')}, {spinners} {noun}, command: {shown}"


def judge(command: str, segments: list[list[str]]) -> str | None:
    """The refusal for a command that runs a build or a gate bare while the slot is held."""
    if ESCAPE.search(command):
        return None
    bare = next((seg for seg in segments if runs_bare(seg)), None)
    if bare is None:
        return None
    held = holder()
    if held is None:
        return None
    return (
        f"`{shlex.join(bare)}` runs a build or a gate outside tools/load.py while the load slot is "
        f"held ({_describe(held)}): a bare run skips the queue every other gate waits in, and the "
        "run holding the slot then measures a machine busier than it recorded -- two agents did "
        "exactly this with `swift test` on 2026-11-11 (GOH.LOADBUILDLOCK). Wait for the slot "
        f"instead: {wait_line(bare)}  (escape: `# bare-ok: <reason>`)"
    )


__all__ = ["ESCAPE", "holder", "judge", "runs_bare", "wait_line"]
