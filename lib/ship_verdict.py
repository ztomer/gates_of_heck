"""`goh ship verdict --sha SHA [--repo DIR]`: the RECORDED gate verdict of the ship running the
caller (GOH.VERDICTAPI; docs/contracts.md section 29).

A release kit started by `goh ship` may skip re-running the full gate over a commit ship's push
step already gated -- but only on a verdict goh RECORDED, never on its log text (koffee_big read
"pre-push: gating <ref> @ <sha>" and "gates: all green" out of the push log, so a reworded line
turned the skip off with no one told). The record: the ship worker exports GOH_GATE_VERDICTS (the
result's `verdicts` path) for its whole run, and gates/push_gate.sh appends `green <sha>` there
for every commit it gated, after every gated ref passed and none moved. A commit the remote
already held was never gated, so it has no line.

The answer is yes (exit 0, the ship record's path printed) only when a ship record of REPO is
`running`, its worker pid is an ANCESTOR of the caller (a stale or hand-written record has no live
worker above the caller), and its verdict file holds `green SHA`. Anything else is no (exit 1,
nothing printed); a bad invocation is 2. Pinned by tests/test_ship_verdict.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ENV = "GOH_GATE_VERDICTS"


def ship_dir() -> Path:
    return Path(os.environ.get("GOH_SHIP_DIR") or Path.home() / ".local/state/goh/ship")


def ancestors(pid: int) -> set[int]:
    """PID and every pid above it, read from `ps` (macOS and Linux alike)."""
    seen: set[int] = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        argv = ["ps", "-o", "ppid=", "-p", str(pid)]
        out = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             check=False).stdout.strip()  # fmt: skip
        if not out.isdigit():
            break
        pid = int(out)
    return seen


def green(verdicts: str, sha: str) -> bool:
    try:
        lines = Path(verdicts).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return f"green {sha}" in lines


def verdict(repo: Path, sha: str, caller: int | None = None) -> Path | None:
    """The record proving SHA green under the running ship above CALLER, or None."""
    repo = repo.resolve()
    live = ancestors(os.getpid() if caller is None else caller)
    for path in sorted((ship_dir() / repo.name).glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or Path(str(rec.get("repo", ""))).resolve() != repo:
            continue
        running = rec.get("status") == "running" and rec.get("pid") in live
        if running and green(str(rec.get("verdicts") or ""), sha):
            return path
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh ship verdict", description=__doc__.splitlines()[0])
    ap.add_argument("--sha", required=True, help="the full commit sha the gate judged")
    ap.add_argument("--repo", default=".", help="the checkout (default: the current one)")
    args = ap.parse_args(argv)
    top = subprocess.run(["git", "-C", args.repo, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         check=False).stdout.strip()  # fmt: skip
    found = verdict(Path(top or args.repo), args.sha)
    if found is None:
        return 1
    print(found)
    return 0
