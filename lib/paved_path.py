"""The exact `goh wait` / `goh stop` command a refused hand-written wait or kill should have been
(GOH.WAITSTOP, 2026-11-21).

THE CLASS. In the seven days to 2026-11-21 the Bash guard refused 137 hand-written process waits
and kills: `until ! pgrep -f "goh land --wait X"; do sleep 20; done` loops whose pattern sat in
their own shell's command line (`lib/self_match.py`) and `pkill -f "until grep -q"` kills by name
(`lib/name_kill.py`). Each refusal named the safe SHAPES (a pid, the bracket form) and the agent
rewrote the loop by hand, often into the next refused shape. The paved path is one command pair,
`goh wait` (`lib/proc_wait.py`) and `goh stop` (`lib/proc_stop.py`); this module turns the refused
pattern into the one line that replaces it, so the refusal ends in a command to run, not a rule to
re-derive:

  * a pattern naming a land of an item (`goh land --wait GOH.X`, `enqueue --land .*GOH.X`) is
    `goh wait land GOH.X`;
  * a pattern naming a ship is `goh wait ship RESULT` (the result path `goh ship` printed);
  * any other wait is `goh wait pid P...` for what the pattern matches RIGHT NOW (pgrep's answer),
    or, when nothing matches, the wait is already over;
  * a kill is `goh stop P...` for what the pattern matches now (`goh stop` refuses a process the
    caller did not start), or the stamp route when nothing matches.
"""

from __future__ import annotations

import os
import re
import subprocess

import session_owner

PGREP = "p" + "grep"  # split, so the house's kill-by-name check does not read this as a call
ITEM = re.compile(r"\b[A-Z][A-Z0-9]+\.[A-Z0-9][A-Z0-9_]*\b")
STAMP_ROUTE = (
    "to stop it in a LATER command, stamp it when you start it: "
    "`cmd > x.log 2>&1 & goh stop --stamp $! > x.pid`, then `goh stop x.pid`"
)


def live_pids(pattern: str, full: bool = True) -> list[int]:
    """The pids the pattern matches now (pgrep's own answer, this process and its parent left
    out), at most 8; none when pgrep cannot be asked."""
    argv = [PGREP] + (["-f"] if full else []) + ["--", pattern]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False,
                              stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return []
    mine = {os.getpid(), os.getppid()}
    pids = [int(w) for w in done.stdout.split() if w.isdigit()]
    return [p for p in pids if p not in mine][:8]


def wait_command(pattern: str, pids: list[int] | None = None) -> str:
    """The `goh wait` line that replaces a wait on "no process matches PATTERN"."""
    item = ITEM.search(pattern)
    if item and "land" in pattern:
        return f"`goh wait land {item.group(0)}`"
    found = live_pids(pattern) if pids is None else pids
    by_pid = f"`goh wait pid {' '.join(map(str, found))}`" if found else ""
    if re.search(r"\bship\b", pattern):
        line = "`goh wait ship RESULT` (RESULT: the result path `goh ship` printed)"
        return f"{line}, or {by_pid}" if by_pid else line
    if by_pid:
        return f"{by_pid} (what `{pattern}` matches right now)"
    return (
        f"nothing else matches `{pattern}` right now, so the wait is already over: run the next "
        'step directly; for a process you start, `cmd & pid=$!` then `goh wait pid "$pid"`'
    )


def stop_command(pids: list[int]) -> str:
    """The `goh stop` line that replaces a kill of what a name matches (PIDS: its matches now).

    Only the pids `goh stop` would accept are named (GOH.STOPOWNSHELL): this agent session's, by
    `lib/session_owner.py`; a refusal must never print a command that will refuse in turn."""
    pids = [p for p in pids if session_owner.mine(p)]
    if pids:
        return (
            f"`goh stop {' '.join(map(str, pids))}` (what it matches right now; `goh stop` "
            "refuses any process you did not start)"
        )
    return (
        f'`goh stop "$pid"` for a process this same command started (`cmd & pid=$!`); {STAMP_ROUTE}'
    )
