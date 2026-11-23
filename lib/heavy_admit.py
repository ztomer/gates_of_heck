#!/usr/bin/env python3
"""A heavy job from ANY session comes in through the one admission (GOH.ADMISSION).

THE INCIDENT (2026-11-22). A shellcheck burst from another session drove the load to 80-113 on 16
cores and failed the v0.150.0 ship on a load flake. Every gate came in by a gate slot
(lib/admission.py, D-0008), but a session's own heavy job -- a cargo build, a swift test, a
shellcheck sweep -- ran at once beside them, admitted by nothing.

THE WAY IN. The Bash guard (hooks/claude/discard_guard.py, PreToolUse) asks `heavy` of every
command; a heavy one is rewritten (`updatedInput`) to start with `prefix`:

    eval "$(python3 lib/heavy_admit.py take --owner $$ --label 'session 1a2b3c4d: cargo build')"

`take` admits the tool's shell as a gate (lib/gate_slot.py `hold --for-caller`: memory, then a
slot of the shell's class -- `lane` in a loop lane's worktree, else `work`), says on stderr who
holds every slot while it waits, and prints the `export GOH_GATE_SLOT=...` that makes a gate the
command runs nested in it. The holder watches the shell and frees the slot when the shell exits.
A wait past `--max-wait` (GOH_HEAVY_ADMIT_WAIT, WAIT_S) runs the command anyway and says beside
whom: a Bash call has a timeout of its own, and an unbounded wait reads as a hang. The holder
record's label names the session and the command, so every waiter -- a gate's included -- names
who held the machine (lib/gate_slot.py `describe`).

WHAT IS HEAVY: a build tool taking a machine-wide lock as lib/load_locks.py defines it (cargo,
swift, xcodebuild, gradle, go; its node installs are network, not CPU, and stay out), and a
shellcheck over SHELLCHECK_FILES or more operands, a glob, or under xargs (one file is no burst;
the gates' own shellcheck tokens stay theirs, D-0010). Anything a command only quotes is text.
`# admit-ok: <reason>` in the command is the escape; GOH_GATE_SLOTS=0 takes nothing.
Pinned by tests/test_heavy_admit.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import os
import re
import select
import shlex
import subprocess
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
import load_bare  # noqa: E402
import load_locks  # noqa: E402
import load_priority  # noqa: E402
import shell_segments  # noqa: E402

FAMILIES = ("cargo", "swift", "xcodebuild", "gradle", "go")
SHELLCHECK_FILES = 4
WAIT_S = 300.0
ESCAPE = re.compile(r"#\s*admit-ok:\s*\S")


def _shellcheck_burst(argv: list[str]) -> bool:
    name, args = Path(argv[0]).name, argv[1:]
    if name == "xargs":
        return any(Path(a).name == "shellcheck" for a in args)
    files = [a for a in args if not a.startswith("-")]
    return name == "shellcheck" and (len(files) >= SHELLCHECK_FILES or any("*" in a for a in files))


def heavy(command: str) -> str | None:
    """The heavy family COMMAND runs ("cargo", "shellcheck", ...), or None. A build is read as
    lib/load_bare.py reads a bare one: a heredoc body is text unless a shell runs it, and a build
    behind `load.py` is already admitted; a gate script admits itself."""
    if ESCAPE.search(command) or load_bare.ESCAPE.search(command):
        return None
    try:
        seg = load_bare.bare_segment(command)
        segs, bodies = shell_segments.split(command)
    except ValueError:  # a command shlex cannot split runs, unjudged
        return None
    if seg and (found := load_locks.locking_command(seg)) and found[0] in FAMILIES:
        return found[0]
    for plain in ([t for t in s if t not in bodies] for s in segs):
        if any(Path(t).name == "load.py" for t in plain):
            continue
        if plain and any(_shellcheck_burst(r) for r in load_priority.operands(plain) if r):
            return "shellcheck"
    return None


def prefix(label: str, wait: float, py: str = sys.executable, take: str = __file__) -> str:
    """The line a heavy command is given before it: its admission (module doc, THE WAY IN)."""
    argv = [py, take, "take", "--label", label, "--max-wait", f"{wait:g}"]
    return f'eval "$({" ".join(map(shlex.quote, argv))} --owner $$)"'


def rewrite(command: str, session: str) -> str | None:
    """COMMAND with its admission first when it is heavy, else None."""
    if heavy(command) is None:
        return None
    first = command.strip().splitlines()[0] if command.strip() else ""
    label = f"session {session[:8] or '?'}: {first[:100]}"
    wait = float(os.environ.get("GOH_HEAVY_ADMIT_WAIT") or WAIT_S)
    return f"{prefix(label, wait)}\n{command}"


def hook_output(event: dict) -> dict | None:
    """The PreToolUse answer for a Bash EVENT whose command is heavy: `updatedInput` alone, which
    replaces the input and makes no permission decision; None for any other event."""
    tool_input = event.get("tool_input") or {}
    if event.get("tool_name") != "Bash" or not isinstance(tool_input, dict):
        return None
    new = rewrite(str(tool_input.get("command") or ""), str(event.get("session_id") or ""))
    if new is None:
        return None
    updated = {**tool_input, "command": new}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}


def _say(text: str) -> None:
    print(f"admission: {text}", file=sys.stderr, flush=True)


def _handshake(fifo: str, holder: subprocess.Popen) -> str:
    """The holder's one line, read without ever blocking on a holder that died."""
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)  # read-write: the open never blocks
    got = b""
    try:
        while not got.endswith(b"\n"):
            ready, _, _ = select.select([fd], [], [], 1.0)
            if ready:
                got += os.read(fd, 4096)
            elif holder.poll() is not None:
                break
    finally:
        os.close(fd)
    return got.decode(errors="replace").strip()


def take(owner: int, label: str, max_wait: float) -> int:
    """Admit the shell OWNER (module doc); the export on stdout, what happened on stderr."""
    import gate_lanes
    import gate_slot
    import tmp_root

    if os.environ.get("GOH_GATE_SLOTS", "") == "0":
        return 0
    directory = os.path.realpath(
        os.environ.get("GOH_GATE_SLOT_DIR") or os.path.expanduser("~/.cache/goh/slots")
    )
    os.makedirs(directory, exist_ok=True)
    if (found := gate_slot.nested(directory)) is not None:
        _say(f"inside a gate that holds {found} -- not taking another")
        return 0
    cls = gate_lanes.default_class(os.getcwd(), dict(os.environ))
    lane, _ = gate_lanes.lane_of(cls)
    slots = int(os.environ.get("GOH_GATE_SLOTS") or gate_slot.default_slots())
    lane_n = gate_lanes.lane_slots(lane, slots)
    paths = [gate_slot.slot_path(directory, i, lane) for i in range(lane_n)]
    if paths and all(gate_slot.held(p) for p in paths):  # every slot taken: say by whom, first
        held = "; ".join(gate_slot.describe(p, time.time()) for p in paths)
        _say(f"{label}: waiting for a gate slot (class {cls}), held by: {held}")
    fifo = os.path.join(tmp_root.goh_tmp(), f"goh-heavy-admit.{os.getpid()}.{time.time_ns()}")
    os.mkfifo(fifo)
    try:
        holder = subprocess.Popen(
            [sys.executable, "-S", str(LIB / "gate_slot.py"), "hold", "--dir", directory,
             "--slots", str(slots), "--owner", str(owner), "--repo", label, "--handshake", fifo,
             "--class", cls, "--for-caller", "--max-wait", f"{max_wait:g}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )  # fmt: skip
        line = _handshake(fifo, holder)
    finally:
        os.unlink(fifo)
    if line.startswith("expired"):
        _say(f"waited {max_wait:g}s for a gate slot; running anyway beside: {line[8:]}")
        return 0
    parts = line.split()
    if len(parts) < 4:
        _say(f"the slot holder gave no slot ({line or 'no handshake'}); running unadmitted")
        return 0
    idx, total, waited, path = parts[:4]
    _say(f"gate slot {int(idx) + 1}/{total} taken after {waited}s (class {cls}) for {label}")
    print(f"export GOH_GATE_SLOT={shlex.quote(f'{path} {owner}')}")
    return 0


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("take")
    t.add_argument("--owner", type=int, required=True)
    t.add_argument("--label", required=True)
    t.add_argument("--max-wait", type=float, default=WAIT_S)
    args = ap.parse_args(argv)
    return take(args.owner, args.label, args.max_wait)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
