"""The landing queue's requests (split from tools/landq.py, GOH.CAPHEADROOM): what `goh land
--enqueue` writes for a push/ship of REPO SHA or a land of a worktree's tip, refused where the
checkout cannot land it. Pinned by tests/test_landq.py."""

from __future__ import annotations

import datetime as dt
import importlib
from pathlib import Path

gitrun = importlib.import_module("gitrun")
land_slot = importlib.import_module("land_slot")
landq_alone = importlib.import_module("landq_alone")


class Refused(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    done = gitrun.run_reader(args, repo=repo, timeout=60)
    return done.stdout.strip() if done.returncode == 0 else ""


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def request(repo_arg: str, sha_arg: str, item: str, ship: bool, summary: str) -> dict:
    """The request for SHA in REPO, refused when the checkout cannot land it."""
    top = git(Path(repo_arg), "rev-parse", "--show-toplevel")
    if not top:
        raise Refused(f"{repo_arg} is not a git checkout")
    repo = Path(top)
    found = gitrun.run_reader(
        ("rev-parse", "--verify", f"{sha_arg}^{{commit}}"), repo=repo, timeout=60
    )
    sha = found.stdout.strip() if found.returncode == 0 else ""
    if not sha:
        raise Refused(
            f"{repo}: no commit {sha_arg} in this checkout ({found.stderr.strip()}) -- "
            "commit it, then enqueue"
        )
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not upstream:
        raise Refused(f"{repo}: the branch tracks no remote branch; nothing to push to")
    remote, _, branch = upstream.partition("/")
    if ship and not item:
        raise Refused("--ship needs --item: the ship records that item done")
    return {"repo": str(repo), "sha": sha, "remote": remote, "branch": branch, "item": item,
            "mode": "ship" if ship else "push", "summary": summary,
            "enqueued_at": now()}  # fmt: skip


def land_request(wt_arg: str, item: str, q: Path | None = None) -> dict:
    """The request to land WORKTREE's tip by tools/land.sh, refused where land.sh refuses. Under
    a queue Q it runs in a slot (GOH.LANDSNAPSHOT, lib/land_slot.py): its `worktree` is goh's
    detached checkout of the sha, never the lane's, which it keeps as `lane`."""
    top = git(Path(wt_arg), "rev-parse", "--show-toplevel")
    if not top:
        raise Refused(f"{wt_arg} is not a git checkout")
    wt = Path(top)
    listing = git(wt, "worktree", "list", "--porcelain").splitlines()
    main = listing[0].removeprefix("worktree ") if listing else ""
    if not main or Path(main).resolve() == wt.resolve():
        raise Refused(f"{wt} is the main checkout; land.sh lands a branch's worktree into it")
    dirty = gitrun.run_reader(
        ("status", "--porcelain", "--untracked-files=no"), repo=wt, timeout=60
    )
    if dirty.returncode or dirty.stdout.strip():
        raise Refused(f"{wt}: uncommitted changes to tracked files -- commit them, then enqueue")
    sha = git(wt, "rev-parse", "HEAD")
    branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    req = {"repo": main, "worktree": str(wt), "sha": sha, "remote": "", "branch": branch,
           "item": item, "mode": "land", "summary": "", "enqueued_at": now()}  # fmt: skip
    if q is not None:
        try:
            slot = land_slot.claim(q, Path(main), sha)
        except land_slot.Unavailable as e:
            raise Refused(f"no checkout of {sha[:8]} for the land: {e}") from None
        req.update(lane=str(wt), worktree=slot["path"], slot=slot["token"], slot_s=slot["s"])
        if red := landq_alone.red_last(q, item):  # gates alone until it lands (GOH.REDALONE)
            req["red_last"] = red
    return req
