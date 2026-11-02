#!/usr/bin/env python3
"""A Claude Code SessionStart hook: what the scheduled estate audit found (BACKLOG 6.13).

scripts/estate_audit.py records every advisory that fires against any tracked Cargo.lock in the
estate. A report nobody reads is the old state with extra steps, so this puts it where the owner
already looks: SessionStart stdout is added to every session's context. It prints NOTHING when the
latest run is fresh and clean, one line per firing advisory otherwise, and -- because a job that
stopped running reads exactly like a clean estate -- says so when the state file is missing or
older than STALE_AFTER_H (tests/test_estate_audit.py). It names, in one line, the repos the audit set
aside for having no `.gatesrc`.

The state is a day old by design, and a lock can move on in that day (GOH.EA3: zinc's pulldown-cmark
0.13.4 -> 0.13.6 landed after the run, and this hook still said the next push was red). So before
any lock is called red, the lock is RE-READ as it is now: one that no longer carries any crate
version the run named is said to be "fixed since the run", never red. A lock that is gone or will
not parse proves nothing, so it keeps the run's verdict -- it is never reported fixed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tomllib
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


def _carries(lock: Path, crates: list[str]) -> bool:
    """Whether `lock` still holds any of `crates` ("name version", as the run recorded them).
    True when the lock cannot be read: only a lock read and found clean is evidence of a fix."""
    try:
        pkgs = tomllib.loads(lock.read_text()).get("package", [])
    except (OSError, ValueError):
        return True
    have = {f"{p.get('name')} {p.get('version')}" for p in pkgs if isinstance(p, dict)}
    return not crates or any(c in have for c in crates)


def _recheck(advisories: dict, root: str | None) -> tuple[dict, list[tuple[str, list[str]]]]:
    """(the advisories with each lock re-read now, [(id, locks fixed since the run)]). A lock is
    named relative to the run's estate root, or by its own path when it lives outside it."""
    still, fixed = {}, []
    base = Path(root) if root else Path.cwd()
    for aid, a in advisories.items():
        locks = a.get("lockfiles", [])
        gone = [lf for lf in locks if not _carries(base / lf, a.get("crates", []))]
        if gone:
            fixed.append((aid, gone))
        if len(gone) < len(locks):
            still[aid] = {**a, "lockfiles": [lf for lf in locks if lf not in gone]}
    return still, fixed


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
    advisories, fixed = _recheck(data.get("advisories", {}), data.get("root"))
    new, standing = [], []
    for aid, a in sorted(advisories.items()):
        seen = _age_h(a.get("first_seen"), now)
        (new if seen is not None and seen <= NEW_WITHIN_H else standing).append((aid, a))
    for aid, a in new:
        lines.append(
            f"goh estate audit: NEW {aid} ({a.get('kind')}) {', '.join(a.get('crates', []))}"
            f" -- the next push of {', '.join(a.get('lockfiles', []))} is red"
        )
    for aid, gone in fixed:
        lines.append(
            f"goh estate audit: {aid} fixed since the run ({data.get('ran_at')}) in"
            f" {', '.join(gone)}: the lock no longer carries the named version"
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
    # A repo with no .gatesrc is not gated, so none of its pushes is red and the audit set it
    # aside (scripts/estate_audit.py); one line says how many and why, so a house repo that lost
    # its .gatesrc is visible rather than silently unaudited.
    ungated = data.get("ungated", [])
    if ungated:
        lines.append(
            f"goh estate audit: {len(ungated)} repo(s) set aside as ungated (no .gatesrc, so no house"
            f" gate runs there and no push of theirs is red): {', '.join(ungated[:MAX_REPOS])}"
            f"{', ...' if len(ungated) > MAX_REPOS else ''}"
        )
    # A lock no gate step of its repo audits is NOT red at the next push -- the push never looks
    # at it -- and that is the finding: an ungated lock (lib/gate_coverage.py). What fires there
    # is said too, because nobody else will see it.
    unaudited = data.get("unaudited", {})
    if unaudited:
        firing = sorted({i for ids in unaudited.values() for i in ids})
        lines.append(
            f"goh estate audit: {len(unaudited)} tracked lockfile(s) NOT AUDITED AT PUSH (no gate"
            f" step of their repo audits them): {', '.join(sorted(unaudited)[:MAX_REPOS])}"
            f"{', ...' if len(unaudited) > MAX_REPOS else ''}"
            + (f"; {len(firing)} advisory(ies) fire there unseen" if firing else "")
        )
    undetermined = data.get("undetermined", {})
    if undetermined:
        lines.append(
            f"goh estate audit: {len(undetermined)} lockfile(s) whose push-time audit goh cannot"
            f" read (a gate step names its target through a shell variable):"
            f" {', '.join(sorted(undetermined)[:MAX_REPOS])}"
            f"{', ...' if len(undetermined) > MAX_REPOS else ''}"
        )
    for e in data.get("errors", []):
        lines.append(f"  not audited: {e}")
    # A registered path that is gone (BACKLOG 6.14): a consumer that left the estate, named
    # here because the owner reads this at the start of every session.
    for p in data.get("problems", []):
        lines.append(f"  estate registry: {p}")
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
