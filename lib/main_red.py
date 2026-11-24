"""lib/main_red.py -- a red idle full suite on main, told to every enqueue (GOH.MAINREDNOTICE).

WHY. The idle suite (lib/idle_suite.py) found main c58986b0 red at 22:19Z (test_filer_live_rules
read the live queue) and nothing told the enqueues: lanes went red on it at their gates, each a
whole land cycle, on a fault none of them made.

    carry(last, sha, outcome)  the idle record's `red`: the newest red verdict's tip, kept across a
                               deferred, yielded or failed run, cleared by a green one (a run that
                               is no verdict must not forget that main is red)
    red(q, repo)               that verdict, when REPO's main still holds its tip: {sha, at, nodes,
                               cause}, the nodes the run's log names FAILED or ERROR, and when it
                               names none the non-test cause (no log, or its last line: a
                               collection error, a deadline); None when main is green
    log_of(q, sha)             the run's log, the one lib/idle_suite.py writes (GOH.MAINREDUNNAMED:
                               this read `idle-suite-<full sha>.log` while the writer wrote
                               `<sha[:12]>`, so every notice said "no failing test named")
    notice(q, req, say)        at the enqueue, before its pre-step (lib/land_check.py at_enqueue):
                               says it and keeps the nodes on the request (`main_red`)
    env(req)                   the pre-step's variable naming them (ENV), for land.sh's set
    also(root)                 tools/affected_tests.py: the named nodes ROOT still defines join the
                               chosen set, so a lane sees main's red in its pre-step, minutes
                               before its gate would

A lane whose change does not touch them goes red on them all the same: main IS red, and the gate
would refuse it later. Pinned by tests/test_main_red.py.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import land_cause  # lib/: FAILED node ids from a log

ENV = "GOH_LAND_PRE_MAIN_RED"
MOST = 50  # nodes carried; a suite red in more than this is read in its log
ERRORED = re.compile(rf"(?:^|\s)ERROR ({land_cause.NODE_ID})", re.MULTILINE)
NOISE = ("load: ",)  # lines load.py prints after any run, red or green: no cause


def log_of(q: Path, sha: str) -> Path:
    """The idle run's log for SHA (lib/idle_suite.py writes it here)."""
    return q / "logs" / f"idle-suite-{sha[:12]}.log"


def nodes_of(log: str) -> list[str]:
    """The test ids LOG names FAILED, then those it names ERROR (a setup or collection error)."""
    out = land_cause.failed_tests(log)
    out += [n for n in dict.fromkeys(m.rstrip(" -") for m in ERRORED.findall(log)) if n not in out]
    return out


def cause_of(log: str | None, path: Path) -> str:
    """Why a red run names no test: its log is missing, or the last thing it said."""
    if log is None:
        return f"its log {path} is missing"
    lines = [ln.strip() for ln in log.splitlines() if ln.strip()]
    last = next((ln for ln in reversed(lines) if not ln.startswith(NOISE)), "")
    return (
        f"no failing test in its log ({path}); its last line: {last[:200]!r}"
        if last
        else (f"its log {path} is empty")
    )


def carry(last: dict, sha: str, outcome: str) -> str:
    """The `red` an idle record keeps (module doc)."""
    if outcome == "red":
        return sha
    if outcome == "green":
        return ""
    return str(last.get("red") or (last.get("sha") if last.get("outcome") == "red" else "") or "")


def red(q: Path, repo: str) -> dict | None:
    """The idle suite's red verdict while REPO's main still holds its tip (module doc)."""
    try:
        last = json.loads((q / "idle-suite" / "last.json").read_text())
    except (OSError, ValueError):
        return None
    sha = carry(last, "", "no verdict") if isinstance(last, dict) else ""  # what it keeps now
    if not sha or not repo:
        return None
    held = subprocess.run(
        ["git", "-C", repo, "merge-base", "--is-ancestor", sha, "HEAD"],
        capture_output=True, stdin=subprocess.DEVNULL, check=False, timeout=60,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )  # fmt: skip
    if held.returncode != 0:
        return None  # another repo's tip, or one main no longer holds
    path = log_of(q, sha)
    try:
        log: str | None = path.read_text(errors="replace")
    except OSError:
        log = None
    at = str(last.get("at") or "") if last.get("sha") == sha else ""  # a later run's time is not it
    nodes = nodes_of(log or "")[:MOST]
    return {"sha": sha, "at": at, "nodes": nodes,
            "cause": "" if nodes else cause_of(log, path)}  # fmt: skip


def notice(q: Path, req: dict, say: Callable[[str], None]) -> list[str]:
    """REQ's enqueue told that main is red; the nodes kept on REQ for its pre-step."""
    try:
        found = red(q, str(req.get("repo") or ""))
    except (OSError, subprocess.SubprocessError):
        found = None
    if not found:
        return []
    nodes = found["nodes"]
    named = ", ".join(nodes[:3]) + (f" and {len(nodes) - 3} more" if len(nodes) > 3 else "")
    say(
        f"main {found['sha'][:8]} is red in the idle full suite ({found['at'] or 'time unknown'}): "
        + (f"{named} -- they join this pre-step's set" if nodes else found["cause"])
    )
    req["main_red"] = {"sha": found["sha"], "nodes": nodes}
    return nodes


def env(req: dict) -> list[str]:
    """`ENV=<nodes, one per line>` for the pre-step's land.sh run; [] when main is not red."""
    nodes = (req.get("main_red") or {}).get("nodes") or []
    return [f"{ENV}=" + "\n".join(nodes)] if nodes else []


def also(root: Path) -> dict[str, str]:
    """{node: why} for the nodes ENV names whose test function ROOT still defines."""
    from lib import fast_tests  # lib/: the functions a test file defines now

    # An id an older list cut (`t[some`, no closing bracket) joins as its base (GOH.REMOTEREDSILENT)
    lines = os.environ.get(ENV, "").splitlines()
    named = [n.split("[")[0] if "[" in n and not n.endswith("]") else n for n in lines if "::" in n]
    have = set(fast_tests.units(root, list(dict.fromkeys(n.split("::")[0] for n in named))))
    why = "main is red on it in the idle full suite (GOH.MAINREDNOTICE)"
    return {n: why for n in named if fast_tests._base(n) in have}
