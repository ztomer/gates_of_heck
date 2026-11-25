"""goh ship's release undo (GOH.SHIPUNDO): a failed release leaves the checkout as it found it.

ztools (LM.AUTOSWITCH, 2026-11-12): the release step committed `chore: version 3.28.0`, failed at
the pre-push gate, and left the bump on main, untagged and unpushed. `snapshot` is taken before the
release step; when it fails, `undo` takes back what it made that no remote holds:

* nothing, when HEAD did not move, the branch changed, or HEAD is no descendant of the snapshot;
* nothing, when any commit it made is on a remote (a remote-tracking ref holds it, or the ship's
  remote has a ref at or past it, or that remote cannot be read): a pushed commit is never reset;
* else the version tags it cut on those commits are deleted and the branch goes back with
  `git reset --keep` -- which refuses, and is reported, rather than overwrite an edit to a file
  those commits changed.

Each returns the lines the result's `undo` carries. Pinned by tests/test_ship_undo.py.

HEAD snapshotted before the release step; when it fails, the version tags it cut and the commits it
made that no remote holds are taken back (`git reset --keep`, which refuses rather than overwrite
an edit); a pushed commit is never reset, and the result's `undo` says what was left. Pinned by
`test_ship_undo.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gitrun

from tui.lib import counted


def _git(repo: Path, *args: str) -> tuple[int, str]:
    done = gitrun.run_reader(args, repo=repo, timeout=60)
    return done.returncode, done.stdout.strip()


def snapshot(repo: Path) -> dict:
    """HEAD, its branch and the tags, before the release step runs."""
    return {
        "head": _git(repo, "rev-parse", "HEAD")[1],
        "branch": _git(repo, "symbolic-ref", "-q", "HEAD")[1],
        "tags": _git(repo, "tag", "--list")[1].splitlines(),
    }


def _on_remote(repo: Path, made: list[str], remote: str) -> str:
    """Why one of MADE is on a remote, or "" when none is."""
    code, unpushed = _git(repo, "rev-list", made[0], "--not", "--remotes")
    if code != 0 or set(made) - set(unpushed.split()):
        return "a remote-tracking ref holds it"
    url = _git(repo, "remote", "get-url", "--push", remote)[1] or remote
    done = gitrun.run_reader(["ls-remote", url], repo=repo, timeout=60)
    if done.returncode != 0:
        return f"{remote} could not be read (git ls-remote exit {done.returncode})"
    tips = {ln.split()[0] for ln in done.stdout.splitlines() if ln.split()}
    for tip in tips:
        for c in made:
            if _git(repo, "merge-base", "--is-ancestor", c, tip)[0] == 0:
                return f"{remote} holds {c[:8]}"
    return ""


def undo(repo: Path, snap: dict, remote: str, log: Path | None = None) -> list[str]:
    """Take back what a failed release step committed and tagged that no remote holds; each line
    said is also appended to LOG (the step's log), so the result's tail carries it."""
    lines = _undo(repo, snap, remote)
    if log is not None:
        with log.open("a", encoding="utf-8") as f:
            f.writelines(f"undo: {ln}\n" for ln in lines)
    return lines


def _undo(repo: Path, snap: dict, remote: str) -> list[str]:
    head = _git(repo, "rev-parse", "HEAD")[1]
    pre = snap["head"]
    if not pre or head == pre:
        return []
    if _git(repo, "symbolic-ref", "-q", "HEAD")[1] != snap["branch"]:
        return [f"left as is: the release moved HEAD off {snap['branch'] or 'its detached HEAD'}"]
    if _git(repo, "merge-base", "--is-ancestor", pre, head)[0] != 0:
        return [f"left as is: HEAD {head[:8]} does not descend from the hand-off {pre[:8]}"]
    made = _git(repo, "rev-list", f"{pre}..{head}")[1].split()
    why = _on_remote(repo, made, remote)
    if why:
        n = counted(len(made), "commit")
        said = f"left as is: {n} of the release, on {remote} or maybe so ({why}): a pushed commit"
        return [said + " is never reset"]
    out = []
    for tag in sorted(set(_git(repo, "tag", "--list")[1].splitlines()) - set(snap["tags"])):
        if _git(repo, "rev-parse", f"{tag}^{{commit}}")[1] in made:
            gitrun.run_writer(["tag", "-d", tag], repo=repo, deadline_s=60)
            out.append(f"tag {tag} undone (it named a commit the release made)")
    done = gitrun.run_writer(["reset", "-q", "--keep", pre], repo=repo, deadline_s=120)
    if done is None or done.returncode != 0:
        why = (done.stderr.strip().splitlines() or ["?"])[-1] if done else "timed out"
        return [*out, f"left as is: git reset --keep {pre[:8]} refused ({why})"]
    n = counted(len(made), "unpushed commit")
    return [*out, f"{n} of the release undone: HEAD is the hand-off {pre[:8]} again"]
