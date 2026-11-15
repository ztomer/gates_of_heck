"""goh ship's verify step (GOH.CIWAIT, split from tools/ship.py at its line cap): HEAD's hosted CI
read ONCE, never waited on, then the `loopctl verify` claim card.

Hosted CI re-runs the gate the push already passed, so it is ADVISORY (owner ruling 2026-11-11;
.loop LOOP.CIADVISORY, tools/loopadvisory.py): `loopctl done` closes an item on the local push gate
while the run is queued or running and records it, and `loopctl tick` re-reads it and names a red
one.
The ship matches it: red (or unread) fails here, a finding; green or no verdict passes; pending or
throttled passes ADVISORY when the checkout's pre-push hook is goh's push gate, and fails at once
without one (done would refuse it the same way). The card reads the same pending run, so with an
advisory CI its CI line is the one failing line it may carry. Pinned by tests/test_ship.py.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import canary_grace
import gitrun  # tools/ship.py, the one importer, puts goh's root and lib/ on sys.path
import push_gated as gated  # the ONE reading of "this hook runs goh's push gate"
import ship_export

from tui.lib import err, step

# `loopctl ci`'s exits (.loop tools/loopci.py): green, red or unread, pending, no verdict exists
# (Actions off, no workflows), throttled; 2 is a refused call.
CI_GREEN, CI_RED, CI_PENDING, CI_NONE, CI_THROTTLED = 0, 1, 3, 4, 75
ANSI = re.compile(r"\x1b\[[0-9;]*m")
GITHUB = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


def git(repo: Path, *args: str) -> str:
    done = gitrun.run_reader(args, repo=repo, timeout=60)
    return done.stdout.strip() if done.returncode == 0 else ""


def github_slug(repo: Path, remote: str) -> str:
    """OWNER/NAME of REMOTE when it is GitHub's (its fetch URL), else ""."""
    m = GITHUB.search(git(repo, "remote", "get-url", remote))
    return m.group(1) if m else ""


def ci_read(repo: Path, remote: str, loopctl_py: str, sha: str = "") -> tuple[bool, str]:
    """HEAD's hosted CI, read ONCE (never waited on): (passes, why). Green or no verdict passes; red
    or unread fails; pending or throttled passes ADVISORY on a push-gated checkout, else fails."""
    slug = github_slug(repo, remote)
    if not slug:
        return True, "CI: the remote is not GitHub's; no CI verdict to read"
    sha = sha or git(repo, "rev-parse", "HEAD")
    argv = [sys.executable, loopctl_py, "ci", sha, "--repo", slug]
    code = subprocess.run(argv, cwd=repo, env=gitrun.repo_env(), stdin=subprocess.DEVNULL,
                          check=False).returncode  # fmt: skip
    if code in (CI_GREEN, CI_NONE):
        return True, ""
    if code not in (CI_PENDING, CI_THROTTLED):
        what = "CI red" if code == CI_RED else f"loopctl ci failed (exit {code})"
        return False, f"{what} for {sha[:8]} on {slug}: the ship stops at verify"
    state = "pending" if code == CI_PENDING else "throttled"
    hook = gated.push_gated(repo)
    if not hook:
        return False, (
            f"CI {state} for {sha[:8]} on {slug}, and {repo.name}'s pre-push hook is not goh's "
            "push gate, so nothing local says the commit was gated: hosted CI is advisory only "
            "over the push gate (re-ship once it is green)"
        )
    return True, (f"CI: advisory -- {state} for {sha[:8]} on {slug}, closed on the local push gate "
                  f"({hook}); loopctl done records the run and tick re-reads it")  # fmt: skip


def reopen(res: dict, dry: bool = False) -> bool:
    """A ship that failed AT verify, made resumable there (GOH.SHIPREVERIFY): its failed verify
    moved to `retried`, the failure fields dropped, status `running`; False (RES untouched) for any
    other result. `goh ship --verify RESULT` by hand on such a result re-runs verify and, green, the
    done step: a transient on the card (a stray another build removed) no longer leaves the round
    to be recorded by hand. DRY only answers."""
    if res.get("status") != "failed" or res.get("failed_step") != "verify":
        return False
    if not dry:
        res.setdefault("retried", []).extend(s for s in res["steps"] if s["name"] == "verify")
        res["steps"] = [s for s in res["steps"] if s["name"] != "verify"]
        for key in ("failed_step", "tail", "finished"):
            res.pop(key, None)
        res["status"] = "running"
    return True


def failing_lines(text: str) -> list[str]:
    """The card's failing (✗) and throttled (⚠) lines, colour stripped."""
    lines = (ANSI.sub("", ln).strip() for ln in text.splitlines())
    return [ln for ln in lines if ln.startswith(("✗", "⚠"))]


def verify(result: str) -> int:
    """The verify step: HEAD's CI read once (ci_read), then the claim card. With an advisory CI the
    card's own CI line (it reads the same pending run) is the one failing line it may carry."""
    res = json.loads(Path(result).read_text(encoding="utf-8"))
    repo = Path(res["repo"])
    passes, why = ci_read(repo, res["remote"], res["loopctl"], res["sha"])
    if not passes:
        err(why)
        return 1
    if why:
        step(why)
    if said := canary_grace.line(res):
        step(said)  # a `·` line: `loopctl done --shipped` keeps it from the card (GOH.CANARYGRACE)
    try:  # the card judges a clean export of the shipped sha, never the live checkout
        with ship_export.exported(repo, res["name"], res["sha"], res["remote"],
                                  res["branch"]) as root:  # fmt: skip
            card = subprocess.run([sys.executable, res["loopctl"], "verify", res["name"]],
                                  cwd=root / res["name"], env=gitrun.repo_env({**os.environ,
                                  "LOOPCTL_ROOT": str(root)}), stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, check=False)  # fmt: skip
    except ship_export.ExportError as e:
        err(f"verify: no clean export of {res['sha'][:8]} to judge: {e}")
        return 1
    sys.stdout.write(card.stdout)
    sys.stdout.write(card.stderr)
    if card.returncode == 0:
        return 0
    advisory = why.startswith("CI: advisory")
    bad = failing_lines(card.stdout + card.stderr)
    if advisory and bad and all(ln[1:].strip().startswith("CI:") for ln in bad):
        step(why)  # the line `loopctl done --shipped` keeps from the card
        return 0
    err(f"the claim card is not green (exit {card.returncode})")
    return 1
