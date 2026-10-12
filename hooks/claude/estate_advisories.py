#!/usr/bin/env python3
"""A Claude Code SessionStart hook: what the scheduled estate audit found (BACKLOG 6.13).

scripts/estate_audit.py records every advisory that fires against any tracked Cargo.lock in the
estate. A report nobody reads is the old state with extra steps, so this puts it where the owner
already looks: SessionStart stdout is added to every session's context. It prints NOTHING when the
latest run is fresh and clean, one line per firing advisory otherwise, and -- because a job that
stopped running reads exactly like a clean estate -- says so when the state file is missing or
older than STALE_AFTER_H (tests/test_estate_audit.py).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

STALE_AFTER_H = 36  # a daily job, plus a missed run's grace
NEW_WITHIN_H = 48
MAX_LINES = 12
MAX_REPOS = 6
SCRIPT = "$GOH_DIR/scripts/estate_audit.py"


def state_path() -> Path:
    env = os.environ.get("GOH_ESTATE_STATE")
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return Path(env) if env else base / "goh" / "estate_audit.json"


def _age_h(stamp: str, now: dt.datetime) -> float | None:
    try:
        return (now - dt.datetime.fromisoformat(stamp)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def report(state: Path, now: dt.datetime) -> list[str]:
    if not state.exists():
        return [
            f"goh estate audit has never run here: `python3 {SCRIPT} --install` schedules it daily."
        ]
    try:
        data = json.loads(state.read_text())
    except (OSError, ValueError) as e:
        return [f"goh estate audit: {state} is unreadable ({e}); re-run `python3 {SCRIPT}`."]
    lines = []
    age = _age_h(data.get("ran_at"), now)
    if age is None or age > STALE_AFTER_H:
        ran = data.get("ran_at", "never")
        lines.append(
            f"goh estate audit is STALE (last run {ran}): the scheduled job is not running; "
            f"`python3 {SCRIPT} --install`."
        )
    advisories = data.get("advisories", {})
    new, standing = [], []
    for aid, a in sorted(advisories.items()):
        seen = _age_h(a.get("first_seen"), now)
        (new if seen is not None and seen <= NEW_WITHIN_H else standing).append((aid, a))
    for aid, a in new:
        lines.append(
            f"goh estate audit: NEW {aid} ({a.get('kind')}) {', '.join(a.get('crates', []))}"
            f" -- the next push of {', '.join(a.get('lockfiles', []))} is red"
        )
    if standing:
        repos = sorted(
            {lf.split("/Cargo.lock")[0] for _, a in standing for lf in a.get("lockfiles", [])}
        )
        lines.append(
            f"goh estate audit: {len(standing)} standing advisory(ies) in {len(repos)} lockfile(s)"
            f" ({', '.join(repos[:MAX_REPOS])}{', ...' if len(repos) > MAX_REPOS else ''});"
            f" `python3 {SCRIPT} --no-fetch` lists them"
        )
    for e in data.get("errors", []):
        lines.append(f"  not audited: {e}")
    if len(lines) > MAX_LINES:
        lines = [
            *lines[: MAX_LINES - 1],
            f"  ... and {len(lines) - MAX_LINES + 1} more in {state}",
        ]
    return lines


def main() -> int:
    try:
        json.load(sys.stdin)
    except ValueError:
        pass
    for line in report(state_path(), dt.datetime.now(dt.UTC)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
