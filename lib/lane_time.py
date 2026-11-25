"""lib/lane_time.py -- a lane's time by kind, from its agent transcripts (GOH.TIMEACCOUNT, 5).

    calls(events)            each tool call as (start, end, kind), from one transcript's events
    lane(events)             {kind: seconds} of one transcript: overlapping calls counted once,
                             the time no call covers is `model`
    lanes(root, since, item) every lane transcript under ROOT since SINCE: (item, wall, kinds)

WHY. The account began at the enqueue: a land took 14 min, the lane that built it 62 (median of 10
lanes), and no review read the 62 (close-the-time-account). A lane is a subagent transcript
(`<root>/*/*/subagents/*.jsonl`) whose first event names an item (`Build GOH.X` or `Item GOH.X`).
Read only, never written.

KINDS are configuration, GOH_LANE_TIME_KINDS: `kind=regex;...`, matched in order against a Bash
call's command; a call that matches none is `other shell`, a non-Bash tool `files and tools`.
Default: land checks, tests, planted faults, commits (their hook gate), waits, measuring.
Pinned by tests/test_lane_time.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

ENV, ROOT_ENV = "GOH_LANE_TIME_KINDS", "GOH_LANE_TIME_ROOT"
DEFAULT_KINDS = (r"land check=land --check;tests=pytest;planted faults=mutat|planted|break it|"
                 r"--plant;commits=git commit|commit --amend;waits=\bsleep\b|\buntil\b|--wait|"
                 r"time\.sleep;measuring=profile|bench|\btime ")  # fmt: skip
MODEL, SHELL, TOOLS = "model", "other shell", "files and tools"
ITEM = re.compile(r"(?:Build|Item) ([A-Z][A-Z0-9]*\.[A-Z0-9]+)")


def kinds(text: str | None = None) -> list[tuple[str, re.Pattern]]:
    raw = (os.environ.get(ENV) or DEFAULT_KINDS) if text is None else text
    out = []
    for part in filter(None, raw.split(";")):
        name, eq, rx = part.partition("=")
        if not eq or not name.strip():
            raise ValueError(f"{ENV}: {part!r} is no kind=regex")
        out.append((name.strip(), re.compile(rx)))
    return out


def _ts(x: str) -> float:
    return dt.datetime.fromisoformat(x).timestamp()


def kind_of(tool: str, command: str, rules: list[tuple[str, re.Pattern]]) -> str:
    if tool != "Bash":
        return TOOLS
    return next((k for k, rx in rules if rx.search(command)), SHELL)


def calls(
    events: list[dict], rules: list[tuple[str, re.Pattern]]
) -> list[tuple[float, float, str]]:
    started: dict[str, tuple[float, str]] = {}
    out = []
    for e in events:
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list) or "timestamp" not in e:
            continue
        t = _ts(e["timestamp"])
        for x in content:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "tool_use":
                cmd = str((x.get("input") or {}).get("command") or "")
                started[x.get("id")] = (t, kind_of(str(x.get("name")), cmd, rules))
            elif x.get("type") == "tool_result" and x.get("tool_use_id") in started:
                t0, k = started.pop(x["tool_use_id"])
                out.append((t0, max(t0, t), k))
    return sorted(out)


def lane(events: list[dict], rules: list[tuple[str, re.Pattern]] | None = None) -> dict[str, float]:
    """{kind: seconds}: each call's time not already covered by an earlier one; the rest `model`."""
    stamped = [_ts(e["timestamp"]) for e in events if "timestamp" in e]
    if not stamped:
        return {}
    got: dict[str, float] = {}
    covered, reach = 0.0, stamped[0]
    for t0, t1, k in calls(events, kinds() if rules is None else rules):
        took = max(0.0, t1 - max(t0, reach))
        got[k] = got.get(k, 0.0) + took
        covered += took
        reach = max(reach, t1)
    got[MODEL] = max(0.0, max(stamped) - stamped[0] - covered)
    return got


def _events(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def root() -> Path:
    return Path(os.environ.get(ROOT_ENV) or Path.home() / ".claude" / "projects")


def lanes(top: Path, since: float, prefix: str = "") -> list[tuple[str, float, dict[str, float]]]:
    """(item, wall, {kind: s}) of every lane transcript under TOP that started since SINCE and
    names an item starting with PREFIX."""
    rules = kinds()
    out = []
    for path in sorted(top.glob("*/*/subagents/*.jsonl")):
        try:
            if path.stat().st_mtime < since:
                continue
            events = _events(path)
        except OSError:
            continue
        stamped = [e for e in events if "timestamp" in e]
        if not stamped or _ts(stamped[0]["timestamp"]) < since:
            continue
        m = ITEM.search(json.dumps(stamped[0])[:6000])
        if not m or not m[1].startswith(prefix):
            continue
        wall = _ts(stamped[-1]["timestamp"]) - _ts(stamped[0]["timestamp"])
        out.append((m[1], wall, lane(events, rules)))
    return out
