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
or enqueue a second item at once, and no git command of the land ever writes to it. The enqueue's
pre-step rebases the slot onto main before it judges (GOH.PREONTIP, tools/land.sh), so the lander
checks the slot out at the enqueued sha again before it runs the request (`ready`), and releases
it with the request's record (`release`).

SLOTS ARE REUSED, NOT MADE PER LAND. A checkout keeps its ignored build output (full_run.prepare:
`checkout -f` and `clean -fd`, never -x), and cargo's build-dir is keyed by the workspace path, so a
reused slot is a warm one; slot numbers past KEEP are removed when released, so the store is
bounded by the lands in flight. Measured on gates_of_heck (1431 files, 2026-11-18): a new slot
0.2-0.4 s, a reused one 0.05-0.15 s.

OWNERSHIP is a goh.db `slot_owners` row (GOH.LANDQDB), named by `<n>.owner` beside the slot:
{token, pid, pid_stamp}. A claim is one transaction that reads the owner and the queue's `requests`
and writes the row, so two claimers never take one slot (the row's key is the slot). An owner is
stale -- its slot free -- when its pid is gone and no request pending or running names its token:
an enqueue killed mid-pre-step, a lander that died after the record. Whether the pid is alive is
read before the transaction (a process listing is never waited on inside one), and the claim
replaces only the owner it read (its token), so a claim made meanwhile is never taken over.
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
import sqlite3
import time
import uuid
from pathlib import Path

import build_dir_reap
import full_run
import goh_db
import landq_state
import pid_owner

KEEP = 4  # slot numbers below this stay checked out between lands (warm build output)


class Unavailable(RuntimeError):
    """No slot could be checked out at the sha."""


def root(q: Path, main: Path) -> Path:
    """The slots of MAIN's repository: one directory per main checkout, named and hashed."""
    resolved = str(Path(main).resolve())
    key = hashlib.sha1(resolved.encode()).hexdigest()[:10]
    return q / "slots" / f"{Path(resolved).name}-{key}"


def _slot(owner: Path) -> tuple[str, int]:
    """(repo key, slot number) of an owner's name `<root>/<n>.owner`."""
    return os.path.realpath(owner.parent), int(owner.name.removesuffix(".owner"))


def _queued(con: sqlite3.Connection, q: Path, token: str) -> bool:
    """A request pending or running names TOKEN as its slot (read in the claim's transaction)."""
    got = con.execute("SELECT body FROM requests WHERE queue = ? AND state IN ('pending', "
                      "'running')", (landq_state.queue_key(q),))  # fmt: skip
    for (body,) in got:
        with contextlib.suppress(ValueError, AttributeError):
            if json.loads(body).get("slot") == token:
                return True
    return False


def _owner(owner: Path) -> dict:
    """The slot's owner row {token, pid, pid_stamp}, {} when it has none."""
    key, n = _slot(owner)
    try:
        with goh_db.ro() as con:
            got = con.execute("SELECT token, pid, pid_stamp FROM slot_owners WHERE repo_key = ? "
                              "AND n = ?", (key, n)).fetchone()  # fmt: skip
    except (OSError, sqlite3.Error):
        return {}
    return dict(zip(("token", "pid", "pid_stamp"), got, strict=True)) if got else {}


def _alive(got: dict) -> bool:
    """By the start time beside the pid (GOH.STRAYSIGNAL2): a claimer's pid handed to another
    process once it ended is no live claim. A claim of a bare pid (an older goh) is judged as is."""
    pid = got.get("pid")
    return isinstance(pid, int) and pid_owner.alive_line(str(got.get("pid_stamp") or pid))


def _take(q: Path, owner: Path, token: str) -> bool:
    """Claim OWNER for TOKEN in one transaction: an empty slot, or one whose owner (the one read
    here, its pid gone) no queued request names."""
    key, n = _slot(owner)
    me = os.getpid()
    stamp = pid_owner.stamp_line(me)
    was = _owner(owner)
    alive = bool(was) and _alive(was)  # read before the transaction: no `ps` is waited on in one
    with goh_db.transaction() as con:
        got = con.execute("SELECT token FROM slot_owners WHERE repo_key = ? AND n = ?",
                          (key, n)).fetchone()  # fmt: skip
        if got and got[0] == token:
            return True  # TOKEN's already: a read before that failed is never "another's"
        if got and (got[0] != was.get("token") or alive or _queued(con, q, got[0])):
            return False  # held: live, queued, or claimed by another since the read
        con.execute("INSERT OR REPLACE INTO slot_owners (repo_key, n, token, pid, pid_stamp, at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (key, n, token, me, stamp, time.time()))  # fmt: skip
    return True


def _free(owner: Path, token: str) -> None:
    key, n = _slot(owner)
    with goh_db.transaction() as con:
        con.execute("DELETE FROM slot_owners WHERE repo_key = ? AND n = ? AND token = ?",
                    (key, n, token))  # fmt: skip


def owners(q: Path) -> list[dict]:
    """Every slot owner row under queue Q: {repo_key, n, token, pid, pid_stamp, at, alive}."""
    base = os.path.realpath(q / "slots") + os.sep
    try:
        with goh_db.ro() as con:  # a prefix, never a LIKE pattern: a path's `_` is any character
            rows = con.execute("SELECT repo_key, n, token, pid, pid_stamp, at FROM slot_owners "
                               "WHERE substr(repo_key, 1, ?) = ? ORDER BY repo_key, n",
                               (len(base), base)).fetchall()  # fmt: skip
    except (OSError, sqlite3.Error):
        return []
    cols = ("repo_key", "n", "token", "pid", "pid_stamp", "at")
    return [{**(r := dict(zip(cols, row, strict=True))), "alive": _alive(r)} for row in rows]


def presteps(q: Path) -> list[dict]:
    """The owner rows of enqueues still in their pre-step: the claimer lives and no request names
    its token yet (an enqueue that files and then `--wait`s lives on, its request queued)."""
    live = [o for o in owners(q) if o["alive"]]
    if not live:
        return []
    try:
        with goh_db.ro() as con:
            return [o for o in live if not _queued(con, q, o["token"])]
    except (OSError, sqlite3.Error):
        return live


def reap(q: Path) -> list[dict]:
    """Every owner row under queue Q that is stale by `_take`'s rule -- its pid gone and no request
    pending or running names its token -- deleted; the rows deleted (GOH.CAPPRESTEP: slot 5's
    dead pid 27042 sat in goh.db a day). The row is re-read in the transaction and deleted only
    while it is still the token read, so a claim made meanwhile stays."""
    gone = []
    for o in owners(q):
        if o["alive"]:
            continue
        with goh_db.transaction() as con:
            got = con.execute("SELECT token FROM slot_owners WHERE repo_key = ? AND n = ?",
                              (o["repo_key"], o["n"])).fetchone()  # fmt: skip
            if not got or got[0] != o["token"] or _queued(con, q, o["token"]):
                continue
            con.execute("DELETE FROM slot_owners WHERE repo_key = ? AND n = ? AND token = ?",
                        (o["repo_key"], o["n"], o["token"]))  # fmt: skip
        gone.append(o)
    return gone


def import_owners(q: Path) -> int:
    """Every `slots/<repo>/<n>.owner` file an older goh made, into a row (INSERT OR IGNORE), the
    file deleted once its row reads back. Under lander.lock (GOH.LANDQDB)."""
    files = sorted(Path(q / "slots").glob("*/*.owner"))
    rows = []
    for f in files:
        with contextlib.suppress(OSError, ValueError, AttributeError):
            got = json.loads(f.read_text())
            if got.get("token") and f.name.removesuffix(".owner").isdigit():
                rows.append((f, (*_slot(f), str(got["token"]), got.get("pid"), got.get("pid_stamp"),
                                 f.stat().st_mtime)))  # fmt: skip
    if not rows:
        return 0
    with goh_db.transaction() as con:
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO slot_owners (repo_key, n, token, pid, pid_stamp, "
                        "at) VALUES (?, ?, ?, ?, ?, ?)", [r for _, r in rows])  # fmt: skip
        added = con.total_changes - before
    for f, _ in rows:
        if _owner(f):
            f.unlink(missing_ok=True)
    return added


def _drop(main: Path, wt: Path, reap: bool = False) -> None:
    """WT gone as a worktree and as a directory; with REAP, the cargo build dirs it alone used
    (lib/build_dir_reap.py: a removed slot's copy no longer waits for a reclaimer)."""
    builds = build_dir_reap.owned(wt, main) if reap else []
    with contextlib.suppress(OSError):
        full_run._git(main, "worktree", "remove", "--force", str(wt), write=True)
    shutil.rmtree(wt, ignore_errors=True)
    with contextlib.suppress(OSError):
        full_run._git(main, "worktree", "prune", write=True)
    build_dir_reap.remove(builds)


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
    not; a request with no slot (enqueued before slots) runs in its worktree, refused when that
    moved (it would gate a commit nobody enqueued)."""
    token, wt = req.get("slot"), req.get("worktree")
    if not token or not wt:
        head = full_run._git(Path(wt or "."), "rev-parse", "HEAD").stdout.strip()
        if head == req["sha"]:
            return ""
        return (f"tip moved after enqueue: {wt} is at {head or '(unreadable)'}, not the enqueued "
                f"{req['sha']}, so land.sh would gate a commit nobody enqueued")  # fmt: skip
    owner = root(q, Path(req["repo"])) / f"{Path(wt).name}.owner"
    if _owner(owner).get("token") != token and not _take(q, owner, token):
        return f"slot unavailable: {wt} is another request's now"
    why = prepare(Path(req["repo"]), Path(wt), req["sha"])
    return f"slot unavailable: {why}" if why else ""


def release(q: Path, main: Path, wt: str, token: str) -> None:
    """The slot free again: its owner removed when it is TOKEN's; a number past KEEP is removed."""
    owner = root(q, main) / f"{Path(wt).name}.owner"
    if _owner(owner).get("token") != token:
        return
    with contextlib.suppress(ValueError):
        if int(Path(wt).name) >= KEEP:
            _drop(main, Path(wt), reap=True)
    _free(owner, token)


def label(req: dict) -> list[str]:
    """`env` words naming REQ's lane branch to tools/land.sh: a slot's own HEAD is detached."""
    return [f"GOH_LAND_BRANCH={req['branch']}"] if req.get("slot") and req.get("branch") else []


def done(q: Path, req: dict) -> dict:
    """REQ recorded: its slot released; what the record keeps of it (the checkout's seconds)."""
    release_req(q, req)
    return {"slot_s": req["slot_s"]} if isinstance(req.get("slot_s"), float) else {}


def release_req(q: Path, req: dict) -> None:
    """REQ's slot released, when it has one."""
    if req.get("slot") and req.get("worktree") and req.get("repo"):
        release(q, Path(req["repo"]), req["worktree"], req["slot"])
