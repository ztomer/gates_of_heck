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

A subagent is STOPPED when `agent_stop.stop_marker()` finds a marker on disk: its interrupt record, or a
TaskStop tool_result naming its id in its session (the only trace a stop of an agent that had
already ended its turn leaves; LOOP.LINGERSTOP). Stopped is finished. A transcript that just ends,
with no marker, says nothing about whether the agent lives: its state is "unknown", never alive.

A subagent is STALLED when it is not stopped, its transcript has not been written for
`--minutes` (default 20, `GOH_AGENT_STALL_MINUTES`), and it is not done. "Done" is NOT the meta
file's `"finished": true`: Claude Code sets that on every STOP of a background agent, including one
that ended its turn to wait on its own background job ("stopped with background work of its own
still running"), so a finished agent whose job then died was never reported (GOH.STALLS2). A
finished agent is done when it ends in the report contract's `Status:` line or has no background job
still pending (`is_waiting`, the one definition `.loop`'s tick shares); a finished agent that
stopped with a job pending and no report is a stall. One older than `--max-age-hours` (default 24)
is residue of a session that is gone, not a stall. Each stall is named with its idle time, the last
thing it said, and each background job it is still waiting on: alive (pids) or gone. Exit 1 when
any is stalled, 0 when none, 2 on a bad argument.

`--hook` is the coordinator's UserPromptSubmit hook: it reads the event's own session's subagents
and, when one is stalled, puts the list in front of the coordinator (`additionalContext`) and the
owner (`systemMessage`). It never blocks a prompt and is silent on anything it cannot read.

WHEN THE HOOK FIRES, probed on 2026-11-08 against the desktop app's Claude Code 2.1.317 in its
own mode (`--input-format stream-json`), with every hook event logged (GOH.STALLNOTIFY): a
task-notification delivered to an IDLE coordinator fires UserPromptSubmit, its `prompt` the
`<task-notification>` text -- for a background shell job finishing and for a background subagent
finishing (SubagentStop fires first, in the subagent); a ScheduleWakeup firing fires
UserPromptSubmit too, its `prompt` the wakeup's. TaskCompleted, TeammateIdle and Notification did
not fire on any of them. So the coordinator sees a stall on its next wake of any kind, and the hook
needs nothing more. The gap is a coordinator that never wakes: no owner prompt, no wakeup, and every
agent it waits on stalled, so no notification is coming.

`--notify` is the owner's channel for a coordinator that never wakes: a one-shot pass (no daemon)
that, across every session, finds each stall whose pending jobs are ALL gone -- idle past the
threshold, waiting on background work no live process can still complete, so nothing will ever
notify it (a stall with a live job may be a long build and wakes when it ends; one with no job at
all is "in a tool call", which the hook names). Each such stall is posted as a macOS notification
ONCE: the state file (`--state`, default `~/.cache/goh/agent-stalls-notified.json`) maps an agent id
to the transcript write the owner was told about, holds only the stalls of the latest pass, and a
stall that moves and stalls again is told again. The log is stamped and bounded (moved to `.1` past
256 KiB, lib/load_reap_log.py).

RETIRED JOB (LOOP.CTL2, GOH.STALLS2): goh no longer schedules this pass. The launchd job
com.ztomer.goh-agent-stalls that ran `--notify` every five minutes was replaced by
com.ztomer.loopctl-tick (`.loop`), which is the one agent notifier and calls this module's readers
(`stalls`, `pending_jobs`, `is_waiting`, `full_said`, `stop_marker`) for the agents of other sessions. Its plist
and installer were deleted; tests/test_agent_stalls.py pins that nothing can install it again.

Seams: `now` (the clock), the subagents directories, and `holders` (the process table) are
arguments of `stalls()`; `notify` (the poster) and the state path are arguments of `notify_new()`;
the CLI takes `--now`, `--projects`, `--subagents` and `--state`. Nothing here ever signals a
process. Pinned by tests/test_agent_stalls.py.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_stop import INTERRUPTED, stop_marker  # THE stopped-agent rule (LOOP.LINGERSTOP)

DEFAULT_MINUTES = 20
DEFAULT_MAX_AGE_HOURS = 24
TAIL_BYTES = 1 << 16
LSOF_TIMEOUT_S = 5
LAST_SAID_CHARS = 160
NOTIFY_TIMEOUT_S = 10

LAUNCH = re.compile(
    r"(?:running in background with ID|moved to the background \(ID): (\w+)\)?.*?"
    r"Output is being written to: (\S+?\.output)",
    re.DOTALL,
)
NOTIFIED = re.compile(r"<task-id>(\w+)</task-id>")
# A stop that is not the end: the last words wait on something, and are not the report contract's.
WAIT_WORDS = re.compile(
    r"\b(waiting|wait (for|on)|will resume|resume when|once (it|the \w+) (finishes|completes))\b",
    re.IGNORECASE,
)
REPORT = re.compile(r"^\s*Status:", re.MULTILINE)
UNKNOWN = "unknown"

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
    state: str  # "waiting" (ended its turn), "in a tool call", or "unknown" (it just ends)
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
        return False, UNKNOWN, ""
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
        state = UNKNOWN  # no stop marker and no end: nothing says it lives (LOOP.LINGERSTOP)
    return False, state, said


def _meta(transcript: Path) -> dict:
    try:
        meta = json.loads(transcript.with_name(transcript.stem + ".meta.json").read_text())
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def full_said(path: Path) -> str:
    """The agent's last text, whole (`_last_turn` truncates it for display)."""
    for rec in reversed(list(_records(_tail(path)))):
        content = _content(rec)
        if rec.get("type") == "assistant" and isinstance(content, list):
            words = " ".join(
                str(b.get("text", ""))
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if words.strip():
                return words
    return ""


def is_waiting(finished: bool, said: str, jobs: list[Job]) -> bool:
    """Stopped, but not done. `finished` is set on every STOP of a background agent, including one
    that ended its turn to wait for its own background job; a report (`Status:`) is final, and a
    stop with a job still pending, or whose last words are a wait, is not."""
    return finished and not REPORT.search(said) and bool(jobs or WAIT_WORDS.search(said))


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
            try:
                interrupted, state, said = _last_turn(transcript)
                jobs = pending_jobs(transcript.read_text(encoding="utf-8", errors="replace"))
                if meta.get("finished") and not (
                    jobs and is_waiting(True, full_said(transcript), jobs)
                ):
                    continue  # it returned for good: a report, or nothing it still waits on
            except OSError:
                continue
            if interrupted or stop_marker(transcript):
                continue  # stopped is finished (LOOP.LINGERSTOP)
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
    head = f"{len(found)} subagent(s) idle {int(minutes)}m+ and not done:"
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


def dead_waits(found: list[Stall]) -> list[Stall]:
    """The stalls nothing will ever wake: every pending job gone, and at least one pending."""
    return [s for s in found if s.jobs and not any(j.pids for j in s.jobs)]


def osascript_notify(title: str, body: str) -> None:
    """Post a macOS notification. The text goes in as argv, never spliced into the script."""
    script = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)",
              "end run"]  # fmt: skip
    argv = ["osascript", *(a for line in script for a in ("-e", line)), title, body]
    try:
        subprocess.run(argv, capture_output=True, timeout=NOTIFY_TIMEOUT_S, check=False,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return


def default_state() -> Path:
    return (
        Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        / "goh"
        / ("agent-stalls-notified.json")
    )


def notify_new(
    found: list[Stall], now: float, state: Path,
    notify: Callable[[str, str], None] = osascript_notify,
) -> list[Stall]:  # fmt: skip
    """Post each dead wait the owner has not been told of; the state keeps only this pass's."""
    try:
        told = json.loads(state.read_text())
    except (OSError, ValueError):
        told = {}
    told = told if isinstance(told, dict) else {}
    fresh: list[Stall] = []
    keep: dict[str, int] = {}
    for s in dead_waits(found):
        wrote = round(now - s.idle_s)  # the transcript's last write: the stall's identity
        keep[s.agent_id] = wrote
        if told.get(s.agent_id) != wrote:
            notify(f"Subagent stalled {int(s.idle_s // 60)}m", s.line())
            fresh.append(s)
    state.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.with_name(state.name + ".tmp")
    tmp.write_text(json.dumps(keep))
    os.replace(tmp, state)
    return fresh


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goh agent-stalls", description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=None, help="idle threshold (default 20)")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    ap.add_argument("--projects", type=Path, default=None, help="Claude Code projects root")
    ap.add_argument("--subagents", type=Path, action="append", help="a subagents dir (repeatable)")
    ap.add_argument("--now", type=float, default=None, help="the clock, epoch seconds (a seam)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hook", action="store_true", help="run as the UserPromptSubmit hook")
    ap.add_argument("--notify", action="store_true", help="tell the owner of new dead waits")
    ap.add_argument("--state", type=Path, default=None, help="--notify's record of what it told")
    ap.add_argument("--log-path", type=Path, default=None, help="--notify's log, bounded")
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
    if args.notify:  # the launchd pass: a notice per new dead wait, a line per notice, exit 0
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
        import load_reap_log as log  # the stamped, bounded log the minute job writes too

        for s in notify_new(found, now, args.state or default_state()):
            print(f"{log.stamp()} agent-stalls: notified {s.line()}")
        sys.stdout.flush()
        if args.log_path:
            log.rotate(args.log_path.expanduser())
        return 0
    if args.json:
        print(json.dumps([s.__dict__ | {"jobs": [j.__dict__ for j in s.jobs]} for s in found]))
    elif found:
        print(report(found, minutes))
    else:
        print(f"✓ no subagent idle {int(minutes)}m+ across {len(dirs)} session(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
