"""lib/land_slot.py -- a land judges the COMMIT, never the lane's working tree (GOH.LANDSNAPSHOT).

WHY. `goh land --enqueue --land WT` ran the enqueue's pre-step, and the lander's pre-step, stack
and gate, inside the lane's own worktree. Three incidents on 2026-11-18: a lane edited its tree
during a pre-step and restored it two minutes later (the verdict may have read the edit); one
worktree held two items, so one item's enqueue pre-step and the other's lander pre-step met on
that checkout's suite lock and the land went red with nothing wrong in the code; and the lander
rebased the worktree for the first item while the second was queued, so the second was refused,
"tip moved after enqueue". The lander moved the lane's tip itself.

A SLOT is a detached checkout of the enqueued sha, owned by goh, under the queue's state dir:
`<q>/slots/<repo key>/<n>` (a linked worktree of the repo's main checkout). The enqueue claims
one (`claim`) and puts it on the request as its `worktree`, the lane's path kept as `lane`; every
pre-step, stack, rebase and gate of that request runs there, so the lane's tree may change, move,
or enqueue a second item at once, and no git command of the land ever writes to it. The lander
checks the slot out at the sha again before it runs the request (`ready`), and releases it with
the request's record (`release`).

SLOTS ARE REUSED, NOT MADE PER LAND. A checkout keeps its ignored build output (full_run.prepare:
`checkout -f` and `clean -fd`, never -x), and cargo's build-dir is keyed by the workspace path, so a
reused slot is a warm one; slot numbers past KEEP are removed when released, so the store is
bounded by the lands in flight. Measured on gates_of_heck (1431 files, 2026-11-18): a new slot
0.2-0.4 s, a reused one 0.05-0.15 s.

OWNERSHIP is `<n>.owner` beside the slot: {pid, token}, made with one link(2), so two claimers never
take one slot. An owner is stale -- its slot free -- when its pid is gone and no request in pending/
or running/ names its token: an enqueue killed mid-pre-step, a lander that died after the record.
The reuse paths (SUITEONCE's suite_reused, verdict carry, the enqueue's kept verdict) are keyed on
tree and sha, never on the checkout's path, so they hold across the slot as they did in the lane.
Pinned by tests/test_land_slot.py.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path

import full_run

KEEP = 4  # slot numbers below this stay checked out between lands (warm build output)


class Unavailable(RuntimeError):
    """No slot could be checked out at the sha."""


def root(q: Path, main: Path) -> Path:
    """The slots of MAIN's repository: one directory per main checkout, named and hashed."""
    resolved = str(Path(main).resolve())
    key = hashlib.sha1(resolved.encode()).hexdigest()[:10]
    return q / "slots" / f"{Path(resolved).name}-{key}"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _queued(q: Path, token: str) -> bool:
    """A request in pending/ or running/ names TOKEN as its slot."""
    for d in ("pending", "running"):
        for path in (q / d).glob("*.json"):
            with contextlib.suppress(OSError, ValueError):
                if json.loads(path.read_text()).get("slot") == token:
                    return True
    return False


def _owner(path: Path) -> dict:
    try:
        got = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _stale(q: Path, owner: Path) -> bool:
    got = _owner(owner)
    if not got:  # unreadable: a claim is one link(2) of a whole file, so this is no live claim
        return True
    pid = got.get("pid")
    if isinstance(pid, int) and _alive(pid):
        return False
    return not _queued(q, str(got.get("token", "")))


def _take(q: Path, owner: Path, token: str) -> bool:
    """Claim OWNER for TOKEN: one link(2) of a whole file; a stale owner is moved aside first."""
    tmp = owner.with_name(f".{owner.name}.{token}")
    tmp.write_text(json.dumps({"pid": os.getpid(), "token": token}) + "\n")
    try:
        for _ in range(2):
            try:
                os.link(tmp, owner)
                return True
            except FileExistsError:
                if not _stale(q, owner):
                    return False
                aside = owner.with_name(f".{owner.name}.stale.{token}")
                try:
                    os.rename(owner, aside)  # one claimer wins the stale owner
                except FileNotFoundError:
                    continue
                aside.unlink(missing_ok=True)
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _drop(main: Path, wt: Path) -> None:
    """WT gone as a worktree and as a directory."""
    with contextlib.suppress(OSError):
        full_run._git(main, "worktree", "remove", "--force", str(wt), write=True)
    shutil.rmtree(wt, ignore_errors=True)
    with contextlib.suppress(OSError):
        full_run._git(main, "worktree", "prune", write=True)


def claim(q: Path, main: Path, sha: str) -> dict:
    """A free slot of MAIN's repository, checked out at SHA: {path, token, s}; Unavailable when
    it cannot be checked out (the claim is released). `s` is the checkout's cost, in seconds."""
    t0 = time.monotonic()
    base = root(q, main)
    base.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    n = 0
    while not _take(q, base / f"{n}.owner", token):
        n += 1
    slot = {"path": str(base / str(n)), "token": token, "s": 0.0}
    why = prepare(main, Path(slot["path"]), sha)
    if why:
        release(q, main, slot["path"], token)
        raise Unavailable(why)
    slot["s"] = round(time.monotonic() - t0, 3)
    return slot


def prepare(main: Path, wt: Path, sha: str) -> str:
    """WT at SHA, clean; a checkout that will not move is made again once. "" or why not."""
    why = full_run.prepare(main, wt, sha)
    if why:
        _drop(main, wt)
        why = full_run.prepare(main, wt, sha)
    return why


def ready(q: Path, req: dict) -> str:
    """The lander's check before it runs REQ: its slot still its own and at its sha. "" or why
    not; a request with no slot (enqueued before slots) runs where it names, as it did."""
    token, wt = req.get("slot"), req.get("worktree")
    if not token or not wt:
        return ""
    owner = root(q, Path(req["repo"])) / f"{Path(wt).name}.owner"
    if _owner(owner).get("token") != token and not _take(q, owner, token):
        return f"its slot {wt} is another request's now"
    return prepare(Path(req["repo"]), Path(wt), req["sha"])


def release(q: Path, main: Path, wt: str, token: str) -> None:
    """The slot free again: its owner removed when it is TOKEN's; a number past KEEP is removed."""
    owner = root(q, main) / f"{Path(wt).name}.owner"
    if _owner(owner).get("token") != token:
        return
    with contextlib.suppress(ValueError):
        if int(Path(wt).name) >= KEEP:
            _drop(main, Path(wt))
    owner.unlink(missing_ok=True)


def release_req(q: Path, req: dict) -> None:
    """REQ's slot released, when it has one."""
    if req.get("slot") and req.get("worktree") and req.get("repo"):
        release(q, Path(req["repo"]), req["worktree"], req["slot"])
