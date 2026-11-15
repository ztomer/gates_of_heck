"""Bash-tool shells blocked reading the harness's stdin, named with the pid to kill (GOH.STDINHANG).

THE CASE (2026-11-15). An agent's `cat > /tmp/x.py`, in a command that also held a heredoc, read the
harness's stdin and hung 5h51m. The agent had finished; its task stayed "running" until the
coordinator found and killed the `cat`. Nothing named it: the stall detector watched transcripts,
and a finished agent's transcript says nothing about a shell it left behind.

WHAT THE PROCESS TABLE SHOWS, probed on this Mac (Claude Code 2.1.325): each Bash tool call is a
`/bin/zsh -c ... eval '<command>' ...` child of the `claude` process. Without a heredoc the eval
reads `< /dev/null` (`lsof -d 0`: `CHR /dev/null`); with one, the shell's fd 0 is the harness's unix
socket (`lsof -F ftd -d 0`: `tunix`, `d0x<addr>`), and a reader that inherited it shows the SAME
type and address, a cumulative CPU time of 0:00.0x, and state `S`. A reader somebody piped into
shows a different fd 0 (`tPIPE`, its own address).

THE RULE. A descendant of a Bash-tool shell is HUNG ON STDIN when its argv is a stdin reader by the
Bash guard's own definition (`lib/stdin_reader.reads_stdin`), it has used under a second of CPU,
and its fd 0 is the shell's own harness socket or pipe. A read BUILTIN (`read`, `mapfile`) has no
process: a Bash-tool shell with no descendant, under a second of CPU, its fd 0 still the harness
socket or pipe and a read builtin in the text it evals (`stdin_reader.shell_reads`) is the reader,
named with its own pid (GOH.STDINFILTERS). It is named when its agent has finished (the
shell has outlived the agent that started it: nothing will ever feed or close that stdin) or when it
has been blocked for `max_hours` (default 2). The line says "kill <pid>" and why. Nothing here
signals a process: the coordinator kills it.

Seams: the process table (`procs`), the fd-0 reader (`stdin`), the harness's pids and the
finished-agent lookup (`ended`) are arguments of `hangs()`. Pinned by tests/test_stdin_hangs.py.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
try:
    import stdin_reader  # THE definition of a stdin reader, shared with the Bash guard
    from shell_segments import SHELLS
except ImportError:  # the shared rule is not there: name nothing rather than guess
    stdin_reader, SHELLS = None, set()

DEFAULT_MAX_HOURS = 2.0
ENDED_FLOOR_S = 300  # a finished agent's reader younger than this may still be winding down
MAX_CPU_S = 1.0
PS_TIMEOUT_S = 5
HARNESS = re.compile(r"(^|/)claude(\s|$)")
EVAL = re.compile(r"\beval '(.*)'(?: < /dev/null)? && pwd -P", re.DOTALL)


@dataclass
class Proc:
    """One row of the process table."""

    pid: int
    ppid: int
    age_s: float
    cpu_s: float
    command: str


@dataclass
class Hang:
    """One reader blocked on the harness's stdin."""

    pid: int
    shell: int
    reader: str
    age_s: float
    why: str

    def line(self) -> str:
        h, m = divmod(int(self.age_s // 60), 60)
        where = (
            "the Bash shell itself" if self.pid == self.shell else f"under Bash shell {self.shell}"
        )
        return (
            f"kill {self.pid}: `{self.reader}` ({where}) has blocked reading "
            f"the harness's stdin for {h}h{m:02d}m at 0% CPU and nothing will ever feed it "
            f"({self.why}); its tool call never returns until it dies"
        )


def _clock(text: str) -> float:
    """Seconds of a ps clock: `[[dd-]hh:]mm:ss[.cc]` (etime) or `m:ss.cc` (cputime)."""
    days, _, rest = text.rpartition("-")
    total = 0.0
    for part in rest.split(":"):
        total = total * 60 + float(part)
    return total + (int(days) * 86400 if days else 0)


def process_table() -> list[Proc]:
    """The real process table, read once (`ps -axww`). Read-only; empty when ps cannot run."""
    try:
        out = subprocess.run(
            ["ps", "-axww", "-o", "pid=,ppid=,etime=,time=,command="], capture_output=True,
            text=True, timeout=PS_TIMEOUT_S, check=False, stdin=subprocess.DEVNULL,
        ).stdout  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for raw in out.splitlines():
        parts = raw.split(None, 4)
        try:
            rows.append(Proc(int(parts[0]), int(parts[1]), _clock(parts[2]), _clock(parts[3]),
                             parts[4] if len(parts) > 4 else ""))  # fmt: skip
        except (ValueError, IndexError):
            continue
    return rows


def lsof_stdin(pids: list[int]) -> dict[int, str]:
    """Each pid's fd 0 as `<type>:<address>` (`unix:0x..`, `PIPE:0x..`, `CHR:0x..`). Read-only."""
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["lsof", "-w", "-F", "ftd", "-a", "-p", ",".join(map(str, pids)), "-d", "0"],
            capture_output=True, text=True, timeout=PS_TIMEOUT_S, check=False,
            stdin=subprocess.DEVNULL,
        ).stdout  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return {}
    found: dict[int, str] = {}
    pid, kind = None, ""
    for raw in out.splitlines():
        tag, value = raw[:1], raw[1:]
        if tag == "p":
            pid, kind = int(value), ""
        elif tag == "t":
            kind = value
        elif tag == "d" and pid is not None:
            found[pid] = f"{kind}:{value}"
    return found


def eval_text(command: str) -> str:
    """The command a Bash-tool shell runs, out of its `eval '...'` (ps shows a newline as \\012)."""
    m = EVAL.search(command)
    if m is None:
        return ""
    return m.group(1).replace("'\"'\"'", "'").replace("'\\''", "'").replace("\\012", "\n")


def _norm(text: str) -> str:
    return " ".join(text.split())


def ended_agents(dirs: Iterable[Path]) -> Callable[[str], str]:
    """A lookup: a shell's command line -> the finished subagent that ran it ("" for none).

    The agent is found by the Bash command in its transcript; it has finished when its meta says
    so or a stop marker names it (agent_stop, the one stopped-agent rule).
    """
    from agent_stop import stop_marker  # beside this file; imported late, only when a hang is seen

    def lookup(shell_command: str) -> str:
        wanted = _norm(eval_text(shell_command))
        if not wanted:
            return ""
        for d in dirs:
            for transcript in d.glob("agent-*.jsonl"):
                try:
                    text = transcript.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if not any(_norm(c) == wanted for c in _bash_commands(text)):
                    continue
                meta = _meta(transcript)
                if meta.get("finished") or stop_marker(transcript):
                    name = meta.get("description") or "agent"
                    return f"{name} [{transcript.stem.removeprefix('agent-')}]"
                return ""
        return ""

    return lookup


def _meta(transcript: Path) -> dict:
    try:
        meta = json.loads(transcript.with_name(transcript.stem + ".meta.json").read_text())
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _bash_commands(text: str) -> Iterable[str]:
    for raw in text.splitlines():
        if '"tool_use"' not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        for block in msg.get("content") or [] if isinstance(msg, dict) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                cmd = (block.get("input") or {}).get("command")
                if isinstance(cmd, str):
                    yield cmd


def hangs(
    procs: list[Proc], stdin: Callable[[list[int]], dict[int, str]] = lsof_stdin,
    ended: Callable[[str], str] = lambda _cmd: "", max_hours: float = DEFAULT_MAX_HOURS,
    harness: set[int] | None = None,
) -> list[Hang]:  # fmt: skip
    """Every reader blocked on its Bash-tool shell's harness stdin past its agent or `max_hours`."""
    if stdin_reader is None:
        return []
    if harness is None:
        harness = {p.pid for p in procs if HARNESS.search(p.command)}
    kids: dict[int, list[Proc]] = {}
    for p in procs:
        kids.setdefault(p.ppid, []).append(p)
    found: list[Hang] = []
    for shell in (p for p in procs if p.ppid in harness):
        if os.path.basename(shell.command.split(" ", 1)[0]) not in SHELLS:
            continue
        below, todo = [], list(kids.get(shell.pid, []))
        while todo:
            p = todo.pop()
            below.append(p)
            todo += kids.get(p.pid, [])
        readers = [
            (p, r) for p in below
            if p.cpu_s < MAX_CPU_S and (r := stdin_reader.reads_stdin(p.command.split()))
        ]  # fmt: skip
        if not below and shell.cpu_s < MAX_CPU_S:
            # no child: a shell blocked at all is in a read BUILTIN (the shell is the reader)
            builtin = stdin_reader.shell_reads(eval_text(shell.command))
            readers += [(shell, builtin)] if builtin else []
        if not readers:
            continue
        fd0 = stdin([shell.pid, *(p.pid for p, _ in readers if p is not shell)])
        own = fd0.get(shell.pid, "")
        if not own.startswith(("unix:", "PIPE:")):
            continue  # /dev/null or a file: it ends
        for p, reader in readers:
            if fd0.get(p.pid) != own:
                continue  # somebody feeds it
            agent = ended(shell.command) if p.age_s >= ENDED_FLOOR_S else ""
            if agent:
                why = f"its agent {agent} has finished"
            elif p.age_s >= max_hours * 3600:
                why = f"blocked past {max_hours:g}h"
            else:
                continue
            found.append(Hang(p.pid, shell.pid, reader, p.age_s, why))
    return sorted(found, key=lambda h: -h.age_s)


def lines(dirs: list[Path], fixture: Path | None = None) -> list[str]:
    """`agent-stalls`' lines: each hang under the harness, its agent looked up in `dirs`.

    `fixture` (`--procs`, a seam) is a JSON `{"procs": [[pid, ppid, age_s, cpu_s, command]...],
    "stdin": {pid: "<type>:<address>"}}` read in place of the real process table and lsof."""
    if fixture is None:
        procs, stdin = process_table(), lsof_stdin
    else:
        data = json.loads(fixture.read_text() or '{"procs": [], "stdin": {}}')  # /dev/null: none
        procs = [Proc(*row) for row in data["procs"]]
        ids = {int(k): v for k, v in data["stdin"].items()}
        stdin = lambda pids: {p: ids[p] for p in pids if p in ids}  # noqa: E731
    return [h.line() for h in hangs(procs, stdin=stdin, ended=ended_agents(dirs))]


__all__ = ["Hang", "Proc", "ended_agents", "hangs", "lines", "lsof_stdin", "process_table"]
