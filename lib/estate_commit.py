"""Write into ANOTHER estate repo and commit exactly what was written, through that repo's own
hooks -- or leave it exactly as it was (GOH.CLASS.ROLLOUT, GOH.REFRESHLEAVES).

The class this closes: goh wrote into consumer trees and committed less than it wrote. On
2026-11-14 `goh hooks --refresh` ran install.sh, which inserted a 17-line block into each
consumer's TRACKED tools/gate.sh, then committed `.githooks` alone: 14 repos were left with an
uncommitted gate.sh, each an orphan tree that blocked dispatch until the coordinator reverted them
by hand. The refresh's own words ("left as it was") were false in two more.

So a write into a consumer goes through ONE door, which owns both halves:

  * the writer may touch only the paths it OWNS (`.githooks` for a hook refresh, the workflow files
    for a pin bump). The tree's status -- and the bytes of every file already uncommitted -- is
    snapshotted first; anything the writer changed outside its paths is put back, byte for byte,
    and NAMED, and nothing is committed. A writer that strays is a goh defect, reported, never a
    dirty consumer.
  * what it owns is committed with `git commit -- <paths>`, through the repo's own hooks; a refusal
    puts the owned paths back as HEAD has them. Either way the tree's status is read again and
    compared with the snapshot, so "left as it was" is said only when it is true.

The owned paths must be clean before the write (the caller checks and sets the repo aside), so
putting them back as HEAD has them drops only what the writer wrote.

The one door goh writes into ANOTHER estate repo through (GOH.CLASS.ROLLOUT, GOH.REFRESHLEAVES):
`set_aside` (on hold, off its default branch, the owned paths uncommitted) and `commit_paths`,
which snapshots the status and the bytes of every uncommitted file, runs the writer, puts back and
names anything it wrote outside its paths, and commits the rest with `git commit -- <paths>`
through the repo's own hooks, or restores them; "left as it was" is said only after the status is
read again. Used by `scripts/hooks_refresh.py` and `scripts/wf_pin.py`. `env()` is that repo's
environment, unbound from any hook's and from the goh ship that runs it (`GOH_SHIP_*`,
`GOH_LAND_LOCK_OWNER`). Pinned by `test_hooks_refresh.py`, `test_wf_pin.py`.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "checks"))
from _gitutil import foreign_repo_env

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_hold

# Bytes of already-uncommitted files kept so a stray write over one can be undone exactly; past this
# a stray over such a file is named and left (it cannot be undone without the bytes).
_KEEP_BYTES = 64 << 20


def env() -> dict[str, str]:
    """Another repo's git, unbound from any hook's, and never taking the optional index lock a
    status or diff would: the tree is someone else's, and a stranded index.lock is theirs to meet
    (GOH.GITKILL). Nor bound to a goh ship that runs this: the consumer's own gates must not read
    the ship's result or take its land lock (GOH.PINPROPAGATE), nor its profile: a consumer's
    gate steps are the consumer's, and the ship times each consumer whole (GOH.SHIPPROFILE)."""
    out = {k: v for k, v in foreign_repo_env().items() if k not in _SHIP_BOUND
           and not k.startswith(("GOH_SHIP_", "GOH_TIMINGS"))}  # fmt: skip
    out["GIT_OPTIONAL_LOCKS"] = "0"
    return out


_SHIP_BOUND = ("GOH_LAND_LOCK_OWNER",)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env(),
        stdin=subprocess.DEVNULL,
    )


def _tail(p: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (p.stdout + p.stderr).splitlines() if ln.strip()]
    return " | ".join(lines[-3:])


def default_branch(repo: Path) -> str | None:
    """The branch a writer may commit on: origin/HEAD's, else the one of main/master it has."""
    head = git(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    if head.startswith("origin/"):
        return head.removeprefix("origin/")
    have = [b for b in ("main", "master") if git(repo, "rev-parse", "-q", "--verify",
                                                  f"refs/heads/{b}").returncode == 0]  # fmt: skip
    return have[0] if len(have) == 1 else None


def set_aside(repo: Path, paths: list[str]) -> str | None:
    """Why nothing may be written into REPO under PATHS, or None. On hold: nothing in it may change.
    Off its default branch: the commit would land on whatever is checked out, a feature branch
    mid-work (GOH.HOOKSBRANCH). PATHS uncommitted: the commit would sweep another agent's edit in,
    and putting them back as HEAD has them would drop it."""
    if (hold := repo_hold.repo_hold(str(repo))) is not None:
        return f"on hold (goh.{hold.key}), nothing in it may change"
    branch = git(repo, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    want = default_branch(repo)
    if not want or branch != want:
        known = want or "none known: no origin/HEAD, and not one of main/master"
        return f"on {branch or 'a detached HEAD'}, not its default branch ({known})"
    dirty = git(repo, "status", "--porcelain", "--", *paths)
    if dirty.returncode != 0 or dirty.stdout.strip():
        return f"{', '.join(paths)} has uncommitted changes (commit or drop them first)"
    return None


@dataclass(frozen=True)
class Snapshot:
    status: dict[str, str]  # path -> porcelain XY, every path not clean
    kept: dict[str, bytes]  # path -> bytes, for each one that exists as a file


def snapshot(repo: Path) -> Snapshot:
    r = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), "status", "--porcelain=v1", "-z",
         "--no-renames",
         "--untracked-files=all"],
        capture_output=True, check=False, env=env(), stdin=subprocess.DEVNULL,
    )  # fmt: skip
    if r.returncode != 0:
        raise OSError(f"git status failed in {repo}: {r.stderr.decode(errors='replace').strip()}")
    status: dict[str, str] = {}
    for entry in r.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
        if len(entry) > 3:
            status[entry[3:]] = entry[:2]
    kept: dict[str, bytes] = {}
    total = 0
    for rel in status:
        p = repo / rel
        if p.is_file() and not p.is_symlink() and total + p.stat().st_size <= _KEEP_BYTES:
            kept[rel] = p.read_bytes()
            total += len(kept[rel])
    return Snapshot(status, kept)


def _owned(rel: str, paths: list[str]) -> bool:
    return any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in paths)


def changed(repo: Path, before: Snapshot) -> list[str]:
    """Every path whose status or (for a file uncommitted before) bytes differ from BEFORE."""
    now = snapshot(repo)
    out = {rel for rel in set(now.status) | set(before.status)
           if now.status.get(rel) != before.status.get(rel)}  # fmt: skip
    out |= {rel for rel, data in before.kept.items() if now.kept.get(rel, b"") != data
            and (repo / rel).is_file()}  # fmt: skip
    return sorted(out)


def _put_back(repo: Path, rels: list[str], before: Snapshot) -> list[str]:
    """Undo each path to BEFORE; the ones that could not be undone, named."""
    left = []
    for rel in rels:
        p = repo / rel
        if rel in before.kept:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(before.kept[rel])
            if before.status[rel][0] in " ?":  # it was not staged: unstage what the writer staged
                git(repo, "reset", "-q", "--", rel)
        elif rel in before.status:
            left.append(rel)  # uncommitted before, too big to keep or not a file: not ours to touch
        elif git(repo, "ls-files", "--error-unmatch", "--", rel).returncode == 0 or \
                git(repo, "cat-file", "-e", f"HEAD:{rel}").returncode == 0:  # fmt: skip
            git(repo, "restore", "--source=HEAD", "--staged", "--worktree", "--", rel)
        elif p.is_file() or p.is_symlink():
            git(repo, "rm", "-q", "--cached", "--ignore-unmatch", "--", rel)
            p.unlink()
    return left


def _restore_owned(repo: Path, paths: list[str]) -> None:
    git(repo, "restore", "--source=HEAD", "--staged", "--worktree", "--", *paths)
    git(repo, "clean", "-fdq", "--", *paths)


def _as_it_was(repo: Path, before: Snapshot) -> str:
    """'' when the tree is as BEFORE, else what still differs."""
    diff = changed(repo, before)
    return "" if not diff else f"left NOT as it was: {', '.join(diff)} still differ"


def commit_paths(repo: Path, paths: list[str], message: str,
                 write: Callable[[], str | None]) -> tuple[bool, str]:  # fmt: skip
    """Run WRITE (which returns None, or why it failed) and commit what it wrote under PATHS.

    (committed or nothing to commit, one line saying what happened). The tree is left either with
    one new commit holding exactly the writer's changes under PATHS, or as it was."""
    before = snapshot(repo)
    why = write()
    strays = [rel for rel in changed(repo, before) if not _owned(rel, paths)]
    if strays:
        left = _put_back(repo, strays, before)
        _restore_owned(repo, paths)
        said = f"NOT committed: the write touched {', '.join(strays)}, outside {', '.join(paths)}"
        said += f"; could not undo {', '.join(left)}" if left else "; put back"
        return False, f"{said} ({_as_it_was(repo, before) or 'left as it was'})"
    if why:
        _restore_owned(repo, paths)
        return False, f"{why} ({_as_it_was(repo, before) or 'left as it was'})"
    git(repo, "add", "-A", "--", *paths)
    if not git(repo, "diff", "--cached", "--name-only", "--", *paths).stdout.strip():
        return True, "nothing to commit"
    done = git(repo, "commit", "-q", "-m", message, "--", *paths)
    if done.returncode != 0:
        _restore_owned(repo, paths)
        state = _as_it_was(repo, before) or "left as it was"
        return False, f"NOT committed: its commit gate refused ({_tail(done)}); {state}"
    sha = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    state = _as_it_was(repo, before)
    return (not state), f"committed in {sha}" + (f"; {state}" if state else "")
