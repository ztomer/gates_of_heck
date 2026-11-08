#!/usr/bin/env python3
"""`goh agent-stalls`: each live subagent whose transcript has stopped moving, and what it waits on.

A background subagent that ends its turn "waiting for the completion notification" of a shell job
it started is idle until that notification arrives. When the job hangs or dies without one, the
agent sits there and nothing says so: KB.LIGHT2 sat 48 minutes on 2026-11-08 (08:47 -> 09:35)
until the coordinator noticed its transcript had stopped changing (GOH.STALLAGENT).

What Claude Code leaves on disk, probed on this machine (Claude Code 2.1.317):

* a subagent's transcript is `~/.claude/projects/<proj>/<session>/subagents/agent-<id>.jsonl`,
  beside `agent-<id>.meta.json` (`description`; `"finished": true` once it has returned). The
  coordinator's own transcript is `<proj>/<session>.jsonl`, so a hook event's `transcript_path`
  names its subagents directory;
* an agent the owner interrupted is never marked finished: its last record is the user text
  `[Request interrupted by user...]`;
* a background shell job is announced in a tool_result, `Command running in background with ID:
  <id>` (or `moved to the background (ID: <id>)` when a foreground call outran its limit), with
  `Output is being written to: <path>.output`; its completion arrives as a `queued_command`
  attachment whose prompt is a `<task-notification>` naming `<task-id><id></task-id>`;
* the job's processes hold that output file open as stdout and stderr, so `lsof` on the path is
  the job's process list; nothing holding it means the job is gone.

A subagent is STALLED when it is not finished, not interrupted, and its transcript has not been
written for `--minutes` (default 20, `GOH_AGENT_STALL_MINUTES`). One older than `--max-age-hours`
(default 24) is residue of a session that is gone, not a stall. Each stall is named with its idle
time, the last thing it said, and each background job it is still waiting on: alive (pids) or gone.
Exit 1 when any is stalled, 0 when none, 2 on a bad argument.

`--hook` is the coordinator's UserPromptSubmit hook: it reads the event's own session's subagents
and, when one is stalled, puts the list in front of the coordinator (`additionalContext`) and the
owner (`systemMessage`). It never blocks a prompt and is silent on anything it cannot read.

Seams: `now` (the clock), the subagents directories, and `holders` (the process table) are
arguments of `stalls()`; the CLI takes `--now`, `--projects` and `--subagents`. Nothing here ever
signals a process. Pinned by tests/test_agent_stalls.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MINUTES = 20
DEFAULT_MAX_AGE_HOURS = 24
TAIL_BYTES = 1 << 16
LSOF_TIMEOUT_S = 5
LAST_SAID_CHARS = 160

LAUNCH = re.compile(
    r"(?:running in background with ID|moved to the background \(ID): (\w+)\)?.*?"
    r"Output is being written to: (\S+?\.output)",
    re.DOTALL,
)
NOTIFIED = re.compile(r"<task-id>(\w+)</task-id>")
INTERRUPTED = "[Request interrupted by user"

Holders = Callable[[list[str]], dict[str, list[int]]]


@dataclass
class Job:
    """A background shell job the agent started and has not been notified of."""

    id: str
    output: str
    pids: list[int] = field(default_factory=list)


@dataclass
class Stall:
    """One stalled subagent."""

    agent_id: str
    description: str
    idle_s: float
    state: str  # "waiting" (ended its turn), "in a tool call", or "mid-turn"
    last_said: str
    jobs: list[Job]
    transcript: str

    def line(self) -> str:
        """One human line: who, how long, what it said, what its jobs are doing."""
        jobs = "; ".join(
            f"{j.id} {'alive (pid ' + ','.join(map(str, j.pids)) + ')' if j.pids else 'gone'}"
            for j in self.jobs
        )
        name = self.description or self.agent_id
        parts = [f"{name} [{self.agent_id}] idle {int(self.idle_s // 60)}m, {self.state}"]
        if self.last_said:
            parts.append(f'last said: "{self.last_said}"')
        parts.append(f"jobs: {jobs}" if jobs else "jobs: none pending")
        return "; ".join(parts)


def lsof_holders(paths: list[str]) -> dict[str, list[int]]:
    """The pids holding each path open, by the real process table (`lsof -F pn`). Read-only."""
    if not paths:
        return {}
    try:
        out = subprocess.run(
            ["lsof", "-w", "-F", "pn", "--", *paths], capture_output=True, text=True,
            timeout=LSOF_TIMEOUT_S, check=False, stdin=subprocess.DEVNULL,
        ).stdout  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return {}
    wanted = {os.path.realpath(p): p for p in paths}
    found: dict[str, list[int]] = {}
    pid = None
    for raw in out.splitlines():
        if raw.startswith("p"):
            pid = int(raw[1:])
        elif raw.startswith("n") and pid is not None:
            name = wanted.get(os.path.realpath(raw[1:]))
            if name is not None and pid not in found.setdefault(name, []):
                found[name].append(pid)
    return found


def _records(text: str) -> Iterable[dict]:
    for raw in text.splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            yield rec


def _content(rec: dict) -> object:
    msg = rec.get("message")
    return msg.get("content") if isinstance(msg, dict) else None


def _tool_results(rec: dict) -> Iterable[str]:
    content = _content(rec)
    if rec.get("type") != "user" or not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            body = block.get("content")
            if isinstance(body, list):
                body = " ".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
            yield str(body)


def _notifications(rec: dict) -> Iterable[str]:
    att = rec.get("attachment")
    if rec.get("type") == "attachment" and isinstance(att, dict):
        yield str(att.get("prompt") or "")
    content = _content(rec)
    if rec.get("type") == "user" and isinstance(content, str) and "<task-notification>" in content:
        yield content


def pending_jobs(text: str) -> list[Job]:
    """Background jobs the transcript launched and was never notified about, launch order."""
    launched: dict[str, Job] = {}
    notified: set[str] = set()
    for rec in _records(text):
        for body in _tool_results(rec):
            for m in LAUNCH.finditer(body):
                launched.setdefault(m.group(1), Job(m.group(1), m.group(2)))
        for prompt in _notifications(rec):
            if "<task-notification>" in prompt:
                notified.update(NOTIFIED.findall(prompt))
    return [j for i, j in launched.items() if i not in notified]


def _tail(path: Path) -> str:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - TAIL_BYTES))
        return f.read().decode("utf-8", "replace")


def _last_turn(path: Path) -> tuple[bool, str, str]:
    """(interrupted, state, last text the agent said), from the transcript's tail."""
    recs = [r for r in _records(_tail(path)) if r.get("type") in ("user", "assistant")]
    if not recs:
        return False, "mid-turn", ""
    last = recs[-1]
    content = _content(last)
    texts = [content] if isinstance(content, str) else [
        str(b.get("text", "")) for b in content or [] if isinstance(b, dict)
    ]  # fmt: skip
    if last.get("type") == "user" and any(t.startswith(INTERRUPTED) for t in texts):
        return True, "", ""
    said = ""
    for rec in reversed(recs):
        content = _content(rec)
        if rec.get("type") == "assistant" and isinstance(content, list):
            words = [b.get("text", "") for b in content if b.get("type") == "text"]
            if any(w.strip() for w in words):
                said = " ".join(" ".join(words).split())[:LAST_SAID_CHARS]
                break
    msg = last.get("message") if isinstance(last.get("message"), dict) else {}
    if last.get("type") == "assistant" and msg.get("stop_reason") == "end_turn":
        state = "waiting"
    elif last.get("type") == "assistant" and msg.get("stop_reason") == "tool_use":
        state = "in a tool call"
    else:
        state = "mid-turn"
    return False, state, said


def _meta(transcript: Path) -> dict:
    try:
        meta = json.loads(transcript.with_name(transcript.stem + ".meta.json").read_text())
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def stalls(
    dirs: Iterable[Path], now: float, minutes: float = DEFAULT_MINUTES,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS, holders: Holders = lsof_holders,
) -> list[Stall]:  # fmt: skip
    """Every stalled subagent under `dirs` (each a `subagents` directory), stalest first."""
    found: list[Stall] = []
    for d in dirs:
        for transcript in sorted(d.glob("agent-*.jsonl")):
            try:
                idle = now - transcript.stat().st_mtime
            except OSError:
                continue
            if idle < minutes * 60 or idle > max_age_hours * 3600:
                continue
            meta = _meta(transcript)
            if meta.get("finished"):
                continue
            try:
                interrupted, state, said = _last_turn(transcript)
                jobs = pending_jobs(transcript.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if interrupted:
                continue
            held = holders([j.output for j in jobs]) if jobs else {}
            for j in jobs:
                j.pids = sorted(held.get(j.output, []))
            agent_id = transcript.stem.removeprefix("agent-")
            desc = str(meta.get("description") or "")
            found.append(Stall(agent_id, desc, idle, state, said, jobs, str(transcript)))
    return sorted(found, key=lambda s: -s.idle_s)


def all_subagent_dirs(projects: Path) -> list[Path]:
    """Every session's subagents directory under a Claude Code projects root."""
    return sorted(p for p in projects.glob("*/*/subagents") if p.is_dir())


def default_projects() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "projects"


def _minutes_env() -> float:
    try:
        return float(os.environ.get("GOH_AGENT_STALL_MINUTES", DEFAULT_MINUTES))
    except ValueError:
        return DEFAULT_MINUTES


def report(found: list[Stall], minutes: float) -> str:
    head = f"{len(found)} subagent(s) idle {int(minutes)}m+ and not finished:"
    return "\n".join([head, *(f"  ⚠ {s.line()}" for s in found)])


def hook(event_text: str, now: float, holders: Holders = lsof_holders) -> str | None:
    """The UserPromptSubmit hook's JSON output for one event, or None to stay silent."""
    try:
        event = json.loads(event_text)
        transcript = Path(event["transcript_path"])
    except (ValueError, KeyError, TypeError):
        return None
    minutes = _minutes_env()
    found = stalls([transcript.with_suffix("") / "subagents"], now, minutes, holders=holders)
    if not found:
        return None
    text = report(found, minutes) + (
        "\nEach is waiting on work that may have died or never notified: check its jobs, then "
        "message it (SendMessage) or stop it (TaskStop). `goh agent-stalls` re-lists them."
    )
    return json.dumps({
        "systemMessage": text,
        "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text},
    })  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goh agent-stalls", description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=None, help="idle threshold (default 20)")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    ap.add_argument("--projects", type=Path, default=None, help="Claude Code projects root")
    ap.add_argument("--subagents", type=Path, action="append", help="a subagents dir (repeatable)")
    ap.add_argument("--now", type=float, default=None, help="the clock, epoch seconds (a seam)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hook", action="store_true", help="run as the UserPromptSubmit hook")
    args = ap.parse_args(argv)
    now = time.time() if args.now is None else args.now
    if args.hook:
        try:
            out = hook(sys.stdin.read(), now)
        except (OSError, ValueError) as e:  # never fails a prompt; says why on stderr
            print(f"agent_stalls: {e}", file=sys.stderr)
            return 0
        if out:
            print(out)
        return 0
    minutes = _minutes_env() if args.minutes is None else args.minutes
    dirs = args.subagents or all_subagent_dirs(args.projects or default_projects())
    found = stalls(dirs, now, minutes, args.max_age_hours)
    if args.json:
        print(json.dumps([s.__dict__ | {"jobs": [j.__dict__ for j in s.jobs]} for s in found]))
    elif found:
        print(report(found, minutes))
    else:
        print(f"✓ no subagent idle {int(minutes)}m+ across {len(dirs)} session(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
