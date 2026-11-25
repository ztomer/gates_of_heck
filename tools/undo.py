#!/usr/bin/env python3
"""undo.py -- `goh undo FILE...`: put back only YOUR edits, in YOUR worktree, keeping a copy.

GOH.UNDOMINE (2026-11-21): 65 refusals in 7 days stopped agents discarding uncommitted work (44 by
goh's discard guard, 21 by Claude Code's own check), and each time the agent was undoing its OWN
edits with `git checkout --`, `git stash` or `git reset`; one lane restored files from HEAD in a
broken loop and lost its edits. There was no safe undo, so the destructive one was the reflex.

    goh undo FILE...           each FILE back to HEAD (your uncommitted edit undone)
    goh undo --base FILE...    each FILE back to the lane's base (committed edits too)
    goh undo --back FILE...    one step back: the text the last undo replaced
    goh undo --item ID ...     the queue item you work for (default $GOH_ITEM): a worktree goh's
                               lane records give to ANOTHER item is refused

What it refuses, before it changes anything (one refused FILE changes none of them):
  * a FILE outside the worktree you run it in (another lane's worktree included);
  * the PRIMARY checkout of any repo -- the shared one every session writes to; a linked worktree
    is its creator's;
  * a held repo (`goh.hold`, lib/repo_hold.py);
  * a worktree a landing-queue record (`$GOH_LANDQ_DIR` pending/running/enqueuing) or a loop lane
    claim (`claim.json` beside the worktree) names for another item than yours;
  * a FILE with nothing of yours in it: unchanged against the target.
  * a FILE the worktree's edit ledger (lib/edit_ledger.py, written by the file-tool guard) shows
    another item or agent wrote after your own last write of it (GOH.UNDOOWNER).

The replaced text is kept at `<worktree>/.goh-undo/<path>` (`.goh-undo/` ignores itself), one step
deep: the next undo of the same FILE replaces it, and `--back` swaps the two, so a second `--back`
redoes. A file that did not exist is kept as that fact, and restoring it removes the file.

`goh undo FILE...` (GOH.UNDOMINE): puts back only the caller's own edits in its own linked worktree
-- to HEAD, or to the lane's base with `--base` -- keeping the replaced text under
`<worktree>/.goh-undo/` for one step back (`--back`, which swaps). Refuses, changing nothing, a
path outside the caller's worktree, a repo's main checkout, a held repo, a worktree goh's lane
records (landq requests, a loop lane's `claim.json`) give to another item, and a file with nothing
of the caller's in it. The discard guard and the stash refusal name it. Pinned by
`test_goh_undo.py`. It also refuses a file the worktree's edit ledger shows another item or agent
wrote after the caller's own last write (GOH.UNDOOWNER, `edit_ledger.py`).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
import edit_ledger  # noqa: E402  # who edited which file (GOH.UNDOOWNER)
import landq_state  # noqa: E402  # the land queue's requests (GOH.LANDQDB)
import repo_hold  # noqa: E402  # the one reading of `goh.hold`

KEEP = ".goh-undo"
MANIFEST = "manifest.json"  # path -> {"absent": bool, "mode": int}: what the kept copy stands for
LANDQ_STATES = ("enqueuing", "pending", "running")


class Refused(Exception):
    """A refusal: nothing was changed."""


def _git(cwd: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(
        ["git", "-C", cwd, *args], capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
        env={**env, "GIT_OPTIONAL_LOCKS": "0"}, check=False,
    )  # fmt: skip
    if check and r.returncode != 0:
        raise Refused(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r


def _out(cwd: str, *args: str) -> str:
    return _git(cwd, *args).stdout.decode().strip()


@dataclass(frozen=True)
class Worktree:
    top: str  # the worktree's top, real path
    base: str  # the commit the lane started from (merge-base with the primary checkout's branch)


def own_worktree(cwd: str) -> Worktree:
    """The caller's worktree, or Refused: not a repo, the shared checkout, or held."""
    if _git(cwd, "rev-parse", "--show-toplevel", check=False).returncode != 0:
        raise Refused(f"{cwd} is not inside a git worktree; run goh undo in your own worktree")
    top = os.path.realpath(_out(cwd, "rev-parse", "--show-toplevel"))
    git_dir = os.path.realpath(_out(top, "rev-parse", "--path-format=absolute", "--git-dir"))
    common = os.path.realpath(_out(top, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if git_dir == common:
        raise Refused(
            f"{top} is a repo's main checkout, shared by every session: an undo here could take "
            "another session's edits. Work in a worktree of your own (`git worktree add "
            "<dir> -b <branch>`) and undo there; here, undo by an inverse edit."
        )
    hold = repo_hold.find_hold(top)
    if hold:
        raise Refused(hold.describe("goh undo"))
    return Worktree(top, _base(top))


def _base(top: str) -> str:
    """The lane's base: where HEAD left the primary checkout's branch (HEAD itself if unknown)."""
    listing = _out(top, "worktree", "list", "--porcelain").splitlines()
    branch = next((ln.split(" ", 1)[1] for ln in listing if ln.startswith("branch ")), "")
    if branch:
        r = _git(top, "merge-base", "HEAD", branch, check=False)
        if r.returncode == 0:
            return r.stdout.decode().strip()
    return _out(top, "rev-parse", "HEAD")


def _landq_dir() -> Path:
    return Path(os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")


def lane_items(top: str) -> dict[str, str]:
    """Each item goh's lane records give this worktree, with the record that says so."""
    found: dict[str, str] = {}
    for state in LANDQ_STATES:
        for rec, data in landq_state.rows(_landq_dir(), state):  # goh.db rows (GOH.LANDQDB)
            lanes = {data.get("lane"), data.get("worktree")}
            if any(p and os.path.realpath(p) == top for p in lanes) and data.get("item"):
                found.setdefault(str(data["item"]), str(rec))
    claim = Path(top).parent / "claim.json"
    try:
        item = json.loads(claim.read_text(encoding="utf-8")).get("id")
    except (OSError, ValueError, AttributeError):
        item = None
    if item:
        found.setdefault(str(item), str(claim))
    return found


def check_owner(top: str, item: str | None) -> None:
    if not item:
        return
    others = {k: v for k, v in lane_items(top).items() if k != item}
    if others:
        named = "; ".join(f"{k} ({v})" for k, v in sorted(others.items()))
        raise Refused(
            f"{top} is another agent's lane by goh's records: {named}. You work for {item}; undo "
            "in your own worktree, never in another lane's."
        )


def _rel(wt: Worktree, cwd: str, path: str) -> str:
    full = os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))
    parent = os.path.realpath(os.path.dirname(full))
    real = os.path.join(parent, os.path.basename(full))
    if os.path.commonpath([real, wt.top]) != wt.top or real == wt.top:
        raise Refused(f"{path} is outside your worktree ({wt.top}); goh undo touches only yours")
    rel = os.path.relpath(real, wt.top)
    if rel.split(os.sep)[0] in (".git", KEEP):
        raise Refused(f"{path} is goh's or git's own record, not a file of yours")
    if os.path.islink(real) or os.path.isdir(real):
        raise Refused(f"{path} is a {'symlink' if os.path.islink(real) else 'directory'}; "
                      "goh undo restores regular files, one by one")  # fmt: skip
    return rel


def _at(wt: Worktree, rev: str, rel: str) -> tuple[bytes | None, int]:
    """The file's bytes and mode at `rev`, or (None, 0) when it does not exist there."""
    ls = _out(wt.top, "ls-tree", rev, "--", rel)
    if not ls:
        return None, 0
    mode = ls.split()[0]
    if mode not in ("100644", "100755"):
        raise Refused(f"{rel} is not a regular file at {rev[:12]} (mode {mode})")
    return _git(wt.top, "cat-file", "blob", f"{rev}:{rel}").stdout, int(mode[-3:], 8)


def _now(wt: Worktree, rel: str) -> tuple[bytes | None, int]:
    path = Path(wt.top, rel)
    if not path.exists():
        return None, 0
    return path.read_bytes(), path.stat().st_mode & 0o777


def _write(path: Path, data: bytes | None, mode: int) -> None:
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.goh-undo-tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode or 0o644)
    os.replace(tmp, path)


class Keep:
    """`<worktree>/.goh-undo/`: the text each undo replaced, one step deep."""

    def __init__(self, top: str) -> None:
        self.root = Path(top, KEEP)

    def _manifest(self) -> dict:
        try:
            return json.loads((self.root / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def put(self, rel: str, data: bytes | None, mode: int) -> None:
        self.root.mkdir(exist_ok=True)
        (self.root / ".gitignore").write_text("*\n", encoding="utf-8")
        _write(self.root / rel, data, mode)
        manifest = self._manifest()
        manifest[rel] = {"absent": data is None, "mode": mode}
        (self.root / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    def get(self, rel: str) -> tuple[bytes | None, int]:
        entry = self._manifest().get(rel)
        if entry is None:
            raise Refused(f"{rel}: nothing kept under {self.root} to step back to")
        if entry.get("absent"):
            return None, 0
        return (self.root / rel).read_bytes(), int(entry.get("mode") or 0o644)


def _check_ledger(wt: Worktree, rel: str, item: str | None) -> None:
    """Refused when the worktree's edit ledger shows another item or agent wrote REL after the
    caller's own last write of it (GOH.UNDOOWNER): the caller is ITEM (`--item` / $GOH_ITEM) and
    the agent the Bash guard stamped on its shell ($GOH_LOOP_AGENT)."""
    agent = os.environ.get("GOH_LOOP_AGENT") or None
    other = edit_ledger.foreign_since_mine(wt.top, rel, item, agent)
    if other:
        who = ", ".join(other.get("items") or []) or "no item"
        raise Refused(
            f"{rel} was last edited by agent {other.get('agent')} ({who}) at {other.get('time')}, "
            "after your own last write of it (the worktree's edit ledger, GOH.UNDOOWNER): undoing "
            "it would take their edit. Undo by an inverse edit of your own lines instead."
        )


def plan(
    wt: Worktree, cwd: str, paths: list[str], to_base: bool, item: str | None = None
) -> list[tuple[str, bytes | None, int]]:
    """(rel, bytes, mode) to write for each path, or Refused for the first that is not yours."""
    rev = wt.base if to_base else "HEAD"
    out = []
    for path in paths:
        rel = _rel(wt, cwd, path)
        _check_ledger(wt, rel, item)
        now = _now(wt, rel)
        target = _at(wt, rev, rel)
        if now[0] == target[0]:
            changed_since_base = _at(wt, wt.base, rel)[0] != now[0]
            hint = " (it differs from the lane's base: `goh undo --base` goes back there)" if (
                not to_base and changed_since_base) else ""  # fmt: skip
            raise Refused(f"{rel}: nothing of yours to undo against {rev[:12]}{hint}")
        out.append((rel, *target))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh undo", description=__doc__.strip().splitlines()[0])
    ap.add_argument("files", nargs="+", metavar="FILE")
    ap.add_argument("--back", action="store_true", help="one step back: the text the undo replaced")
    ap.add_argument("--base", action="store_true", help="restore to the lane's base, not HEAD")
    ap.add_argument("--item", default=os.environ.get("GOH_ITEM"), help="the item you work for")
    a = ap.parse_args(argv)
    cwd = os.getcwd()
    keep = None
    try:
        wt = own_worktree(cwd)
        check_owner(wt.top, a.item)
        keep = Keep(wt.top)
        if a.back:
            todo = [(rel, *keep.get(rel)) for rel in (_rel(wt, cwd, p) for p in a.files)]
        else:
            todo = plan(wt, cwd, a.files, a.base, a.item)
    except Refused as why:
        print(f"goh undo: refused, nothing changed: {why}", file=sys.stderr)
        return 1
    for rel, data, mode in todo:
        keep.put(rel, *_now(wt, rel))
        _write(Path(wt.top, rel), data, mode)
        verb = "stepped back" if a.back else "restored"
        print(f"goh undo: {verb} {rel}; the replaced text is kept: `goh undo --back {rel}`")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
