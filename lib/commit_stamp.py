"""Which commits a commit gate judged, and the land's check of the ones none did.

GOH.CHERRYPICKGATE.

THE GAP (2026-11-22). `git cherry-pick` (and a rebase, an am, `commit --no-verify`) makes a commit
no pre-commit hook judged: an env-spread red from lane-tp.webvision3-uv (294d72e6) was cherry-picked
into a GOH-TINY lane and caught only at land, a whole enqueue cycle later.

THE STAMP. gates/commit_gate.sh, when every step passed, records the TREE it judged (the index being
committed, its tree by lib/run_path.py's index_tree, the hook's carried index included) in
goh.db's `commit_gated` table (lib/small_stores.py, GOH.STATEDBS3; the database beside the
ledger's old file, `$XDG_STATE_HOME/goh/`, read until its import). A commit whose tree is
there was judged as it stands; a cherry-pick onto another base, or any commit made around the
hook, has a tree nobody judged.

THE CHECK. `goh land --check` (lib/land_check.py) lists the range's commits (HEAD's own since its
merge base with `main`, merges aside), and runs the commit gate on each one whose tree is unstamped,
as its pre-commit would have: a detached worktree at its parent with the commit's change staged
(`cherry-pick --no-commit`), `gates/commit_gate.sh` there. Each is NAMED, green or red, and a red
one refuses the check. A green run stamps the tree, so the next check does not run it again.

    commit_stamp.py record REPO     # commit_gate.sh: stamp the index being committed

Which commits a commit gate judged (GOH.CHERRYPICKGATE): `gates/commit_gate.sh` stamps the tree it
passed in `$XDG_STATE_HOME/goh/commit-gated.jsonl`, and `goh land --check` runs the commit gate, as
that commit's pre-commit would have (a detached worktree at its parent, the change staged), on
every commit of the range whose tree is unstamped -- a cherry-pick, a rebase, `--no-verify` --
naming each; a red one refuses the check, a green one is stamped. Invoked by `lib/land_check.py`.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import contextlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import state_paths  # noqa: E402 -- lib/
import tmp_root  # noqa: E402 -- every temp root under goh_tmp (GOH.TMPNOINDEX)

GATE = ROOT / "gates" / "commit_gate.sh"
GATE_ARGV = ["bash", str(GATE)]  # the gate a commit's pre-commit runs (a test's seam)


def ledger() -> Path:
    """The ledger's name: its rows are goh.db's `commit_gated`, in the goh.db beside it."""
    import small_stores  # lib/, on the path above

    return state_paths.state_root() / small_stores.GATED_FILE


def _git(repo: str | Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=False, stdin=subprocess.DEVNULL, env=env)  # fmt: skip


def record(tree: str, repo: str = "") -> None:
    """Stamp TREE as judged by a commit gate (raises OSError when the ledger cannot be written)."""
    import small_stores  # lib/: goh.db's commit_gated (GOH.STATEDBS3)

    row = {"tree": tree, "repo": repo, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        small_stores.gated_add(ledger(), row)
    except sqlite3.Error as e:
        raise OSError(f"commit-gated: {e}") from e


def stamped() -> set[str]:
    """Every tree a commit gate passed."""
    import small_stores

    return {str(r.get("tree")) for r in small_stores.gated(ledger()) if r.get("tree")}


def unstamped(top: str | Path, base: str = "main") -> list[tuple[str, str, str]]:
    """(sha, subject, tree) of each commit of HEAD's own range whose tree no commit gate stamped,
    oldest first; [] when the base cannot be found."""
    mb = _git(top, "merge-base", "HEAD", base)
    if mb.returncode != 0:
        return []
    listed = _git(top, "log", "--reverse", "--no-merges", "--format=%H%x09%T%x09%s",
                  f"{mb.stdout.strip()}..HEAD")  # fmt: skip
    done = stamped()
    out = []
    for line in listed.stdout.splitlines():
        sha, tree, subject = [*line.split("\t", 2), "", ""][:3]
        if sha and tree not in done:
            out.append((sha, subject, tree))
    return out


def _gate_env() -> dict[str, str]:
    """The caller's environment less git's bindings: the gate judges the worktree it runs in."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def gate(top: str | Path, sha: str, argv: list[str]) -> tuple[int, str]:
    """ARGV run as SHA's pre-commit would: in a detached worktree at its parent, its change
    staged. (exit code, the output's tail)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from lib import check_jobs  # the gate and its worktree die with the check (GOH.CHECKVERDICT)

    tmp = Path(tempfile.mkdtemp(prefix="goh-commit-gate-", dir=tmp_root.goh_tmp()))
    wt, env = tmp / "wt", _gate_env()
    with check_jobs.worktree(top, wt, tmp):
        try:
            return _gate_in(wt, top, sha, argv, env, check_jobs)
        finally:
            _git(top, "worktree", "remove", "--force", str(wt), env=env)
            shutil.rmtree(tmp, ignore_errors=True)


def _gate_in(wt: Path, top: str | Path, sha: str, argv: list[str], env: dict[str, str],
             jobs) -> tuple[int, str]:  # fmt: skip
    """`gate`'s run in WT, under the caller's cleanup."""
    made = _git(top, "worktree", "add", "-q", "--detach", str(wt), f"{sha}^", env=env)
    if made.returncode != 0:
        return 2, f"no worktree at {sha[:10]}^: {made.stderr.strip()[:300]}"
    picked = _git(wt, "cherry-pick", "--no-commit", sha, env=env)
    if picked.returncode != 0:
        return 2, f"its change does not apply to its parent: {picked.stderr.strip()[:300]}"
    code, said, _ = jobs.run(argv, wt, env, jobs.STRETCH_CAP_S)
    return code, "\n".join(said.strip().splitlines()[-15:])


def check(
    top: str | Path,
    say: Callable[[str], None],
    argv: list[str] | None = None,
    base: str = "main",
) -> list[str]:
    """Run the commit gate on each unstamped commit of HEAD's range; the refusals, each naming
    its commit. A green run stamps the tree."""
    argv = argv or GATE_ARGV
    refused = []
    for sha, subject, tree in unstamped(top, base):
        say(f"commit gate on {sha[:10]} {subject[:60]}: no commit gate stamped its tree")
        code, tail = gate(top, sha, argv)
        if code == 0:
            say(f"commit gate on {sha[:10]}: green")
            with contextlib.suppress(OSError):  # unstamped, it is only judged again
                record(tree, str(top))
            continue
        refused.append(f"the commit gate on {sha[:10]} ({subject[:60]}), which no commit gate "
                       f"judged (a cherry-pick, a rebase or --no-verify):\n{tail}")  # fmt: skip
    return refused


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "record":
        import run_path  # the index's tree from a private copy: never the shared index.lock

        try:
            tree = run_path.index_tree(argv[1])
        except RuntimeError as e:
            print(f"commit_stamp: no tree to stamp: {e}", file=sys.stderr)
            return 1
        record(tree, argv[1])
        return 0
    print("usage: commit_stamp.py record REPO", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
