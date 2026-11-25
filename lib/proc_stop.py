"""`goh stop TARGET...`: stop processes the CALLER started, and nothing else (GOH.WAITSTOP).

THE CLASS. 23 of the Bash guard's refusals in a week were agents stopping their own earlier work
by name (`pkill -f "until grep -q"`, `pkill -f "affected_tests.py --run"`), and a name reaches
every process carrying it: other sessions' gates died that way (GOH.PKILLNAME). A pid alone is no
better once it has been kept a while: the number is handed out again (GOH.STRAYSIGNAL).

THE RULE. A TARGET is one of
  * a pid that is a DESCENDANT of the caller -- the shell that ran `goh stop` (`cmd & goh stop $!`),
    the `goh` binary between them skipped;
  * a pid started under the caller's own agent session (GOH.STOPOWNSHELL): each Bash tool call is
    a new shell, so an earlier call's `cmd &` is nobody's child; its start environment carries the
    same `CLAUDE_CODE_SESSION_ID` (`lib/session_owner.py`), another session's does not;
  * a STAMP, `<pid> <start time>` as `goh stop --stamp PID` prints it (and `lib/pid_owner`'s
    `stamp_line` / `goh_pid_stamp`), given as the line itself or as a file holding it. The stamp is
    taken while the process is the caller's (`goh stop --stamp` refuses any other), so it carries
    the ownership to a later command; the start time is checked, so a reused number is gone, never
    signalled.
Every target is judged before any is signalled: one refusal (a pid the caller did not start, or not
a pid) stops nothing, exit 2. A target already gone is said and counts as stopped.

Each owned process is stopped with its descendants (snapshotted and stamped first): SIGTERM, up
to `--grace` seconds for every one to be gone (`pid_owner.gone`: an exiting process listed by `ps`
is not gone yet; a zombie is), then SIGKILL to the survivors, each signal through
`pid_owner.signal`, which refuses a pid that names another process by then.

Exit: 0 every target stopped or already gone; 1 something outlived SIGKILL; 2 refused or usage.

`goh stop TARGET...` (GOH.WAITSTOP): stop only what the CALLER started -- a descendant of the shell
that ran it, or a stamp `goh stop --stamp $!` printed while it was one -- with its descendants,
TERM then `--grace` then KILL, each signal through `pid_owner.signal`; every target judged first,
one refusal stops nothing (exit 2), a reused stamp is gone and signals nothing. Invoked by
`crates/goh/house_scripts.tsv`.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))
import pid_owner
import session_owner

from tui.lib import err, info, ok

KILL_WAIT_S = 5.0
POLL_S = 0.05


class Refused(Exception):
    """A target `goh stop` will not signal."""


def _ps(*fields: str) -> list[list[str]]:
    argv = ["ps", "-Ao", ",".join(f"{f}=" for f in fields)]
    out = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         timeout=30, check=False).stdout  # fmt: skip
    return [line.split(None, len(fields) - 1) for line in out.splitlines() if line.strip()]


def parents() -> dict[int, int]:
    """Every process's parent, now."""
    return {int(r[0]): int(r[1]) for r in _ps("pid", "ppid") if len(r) == 2 and r[1].isdigit()}


def _comm(pid: int) -> str:
    out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=30, check=False).stdout  # fmt: skip
    return os.path.basename(out.strip())


def caller(tree: dict[int, int]) -> int:
    """The process that ran `goh stop`: this script's parent, past the `goh` binary that runs it."""
    pid = os.getppid()
    if _comm(pid) == "goh":
        pid = tree.get(pid, pid)
    return pid


def ancestors(pid: int, tree: dict[int, int]) -> list[int]:
    """PID's parent, its parent, ... up to (not including) pid 1."""
    chain, seen = [], {pid}
    while (pid := tree.get(pid, 0)) > 1 and pid not in seen:
        chain.append(pid)
        seen.add(pid)
    return chain


def descendants(pid: int, tree: dict[int, int]) -> list[int]:
    """Every process below PID, now, parents before their children."""
    kids: dict[int, list[int]] = {}
    for child, parent in tree.items():
        kids.setdefault(parent, []).append(child)
    out, todo = [], list(kids.get(pid, []))
    while todo:
        p = todo.pop(0)
        if p not in out and p != pid:
            out.append(p)
            todo += kids.get(p, [])
    return out


def not_self(pid: int, tree: dict[int, int]) -> None:
    """Raise Refused for pid 0/1, `goh stop` itself, or any process above it (its caller's shell,
    the agent that runs that): stopping one stops the caller."""
    if pid <= 1 or pid == os.getpid() or pid in ancestors(os.getpid(), tree):
        raise Refused(f"{pid} is goh stop itself, its caller or one of their parents")


def owned(pid: int, tree: dict[int, int], me: int) -> None:
    """Raise Refused unless PID is a descendant of the caller ME (never the caller or its own
    ancestors)."""
    not_self(pid, tree)
    if me not in ancestors(pid, tree) and not session_owner.mine(pid):
        raise Refused(
            f"{pid} was not started by this command's shell (pid {me}) nor by this agent "
            "session (CLAUDE_CODE_SESSION_ID), so it is not yours to stop. Stop a process you "
            "started in this same command (`cmd & goh stop $!`), or "
            "stamp it when you start it (`cmd & goh stop --stamp $! > x.pid`) and stop the "
            "stamp later (`goh stop x.pid`); a background task of the Bash tool stops by "
            "TaskStop <id>."
        )


def judge(target: str, tree: dict[int, int], me: int) -> pid_owner.Stamp | None:
    """The stamp of the process TARGET names, None when it is already gone; Refused when it is
    not the caller's or not a target."""
    text = target
    if not target.strip().isdigit() and Path(target).is_file():
        text = Path(target).read_text(encoding="utf-8", errors="replace")
    words = text.split()
    if not words or not words[0].isdigit():
        raise Refused(f"{target!r} is neither a pid, a stamp line nor a file holding one")
    pid = int(words[0])
    not_self(pid, tree)
    if len(words) == 1:
        if pid_owner.number_gone(pid, foreign_gone=False):
            return None  # free or a zombie: nothing to stop (another user's process is judged)
        owned(pid, tree, me)
    return pid_owner.from_line(" ".join(words))


def _wait_gone(stamps: list[pid_owner.Stamp], within: float) -> list[pid_owner.Stamp]:
    end = time.monotonic() + within
    left = [s for s in stamps if not pid_owner.gone(s)]
    while left and time.monotonic() < end:
        time.sleep(POLL_S)
        left = [s for s in left if not pid_owner.gone(s)]
    return left


def stop(stamps: list[pid_owner.Stamp], grace: float) -> list[pid_owner.Stamp]:
    """TERM, GRACE seconds, KILL: the stamped processes still not gone after all of it."""
    for s in stamps:
        pid_owner.signal(s, signal.SIGTERM)
    left = _wait_gone(stamps, grace)
    for s in left:
        pid_owner.signal(s, signal.SIGKILL)
    return _wait_gone(left, KILL_WAIT_S)


def run_stamp(pid_text: str) -> int:
    """`goh stop --stamp PID`: the stamp line of a process the caller started."""
    tree = parents()
    if not pid_text.isdigit():
        err(f"goh stop --stamp: {pid_text!r} is not a pid")
        return 2
    try:
        owned(int(pid_text), tree, caller(tree))
    except Refused as e:
        err(f"goh stop --stamp: {e}")
        return 2
    line = pid_owner.stamp_line(int(pid_text))
    if line is None:
        err(f"goh stop --stamp: {pid_text} is gone")
        return 1
    print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goh stop", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("targets", nargs="*", metavar="TARGET", help="pid, stamp line, or stamp file")
    ap.add_argument("--stamp", metavar="PID", help="print the stamp of a process you started")
    ap.add_argument("--grace", type=float, default=5.0, help="seconds between TERM and KILL")
    args = ap.parse_args(argv)
    if args.stamp is not None:
        return run_stamp(args.stamp)
    if not args.targets:
        ap.error("name at least one TARGET (or --stamp PID)")
    tree = parents()
    me = caller(tree)
    chosen: list[tuple[str, pid_owner.Stamp | None]] = []
    for target in args.targets:
        try:
            chosen.append((target, judge(target, tree, me)))
        except Refused as e:
            err(f"goh stop: {e}")
            return 2
    stamps: list[pid_owner.Stamp] = []
    for target, s in chosen:
        if s is None:
            info(f"goh stop: {target.split()[0]} is already gone")
            continue
        stamps.append(s)
        stamps += [d for d in map(pid_owner.stamp, descendants(s.number, tree)) if d is not None]
    left = stop(stamps, args.grace)
    if left:
        err(f"goh stop: still there after SIGKILL: {' '.join(str(s.number) for s in left)}")
        return 1
    if stamps:
        ok(f"goh stop: stopped {' '.join(str(s.number) for s in stamps)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
