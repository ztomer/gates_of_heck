"""Which commits a commit gate judged, and the land's check of the ones none did.

GOH.CHERRYPICKGATE.

THE GAP (2026-11-22). `git cherry-pick` (and a rebase, an am, `commit --no-verify`) makes a commit
no pre-commit hook judged: an env-spread red from lane-tp.webvision3-uv (294d72e6) was cherry-picked
into a GOH-TINY lane and caught only at land, a whole enqueue cycle later.

THE STAMP. gates/commit_gate.sh, when every step passed, records the KEY of the commit it judged
(lib/commit_key.py: its change, and every path of its tree but the prose the gate was measured not
to read; the index being committed, the hook's carried index included, over HEAD) in goh.db's
`commit_gated` table (lib/small_stores.py, GOH.STATEDBS3; the database beside the ledger's old file,
`$XDG_STATE_HOME/goh/`). A commit whose key is there was judged as it stands, a rebase over unread
prose included; a cherry-pick whose file moved, a rebase over what the gate reads, a resolved
conflict, an amendment's whole commit and any commit made around the hook has a key nobody judged.
A row from before the key (a bare tree) stamps nothing (GOH.STAMPREBASE).

THE CHECK. `goh land --check` (lib/land_check.py) lists the range's commits (HEAD's own since its
merge base with `main`, merges aside), and runs the commit gate on each unstamped one, as its
pre-commit would have: a detached worktree at its parent with the commit's change staged,
`gates/commit_gate.sh` there. Each is NAMED, green or red, and a red one refuses the check. A green
run stamps the commit, so the next check does not run it again. The commits are gated side by side,
as wide as the share of the gate that asks (`width`; GOH.PYSLOW4's 18 took about 400 s in turn),
each worker in ONE worktree it moves from commit to commit, beside the rest of the check
(lib/land_check_cli.py).

    commit_stamp.py record REPO     # commit_gate.sh: stamp the index being committed

Which commits a commit gate judged (GOH.CHERRYPICKGATE, GOH.STAMPREBASE): `gates/commit_gate.sh`
stamps the key of the commit it passed (`commit_key.py`) in goh.db's `commit_gated`, and
`goh land --check` runs the commit gate, as that commit's pre-commit would have (a worktree at its
parent, the change staged), on every commit of the range whose key is unstamped -- a cherry-pick, a
rebase over what the gate reads, `--no-verify` -- side by side on the gate's share, naming each; a
red one refuses the check, a green one is stamped. Invoked by `lib/land_check_cli.py`.
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
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "lib"), str(ROOT)]
import commit_key  # noqa: E402 -- lib/
import land_share  # noqa: E402 -- lib/
import state_paths  # noqa: E402 -- lib/
import tmp_root  # noqa: E402 -- every temp root under goh_tmp (GOH.TMPNOINDEX)
import worker_share  # noqa: E402 -- lib/

from lib import check_jobs  # noqa: E402 -- the module whose lifeline the check holds

GATE = ROOT / "gates" / "commit_gate.sh"
GATE_ARGV = ["bash", str(GATE)]  # the gate a commit's pre-commit runs (a test's seam)


def ledger() -> Path:
    """The ledger's name: its rows are goh.db's `commit_gated`, in the goh.db beside it."""
    import small_stores  # lib/, on the path above

    return state_paths.state_root() / small_stores.GATED_FILE


def _git(repo: str | Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=False, stdin=subprocess.DEVNULL, env=env)  # fmt: skip


def record(key: str) -> None:
    """Stamp the commit of KEY as judged green (raises OSError when the ledger is not writable)."""
    import small_stores  # lib/: goh.db's commit_gated (GOH.STATEDBS3)

    row = {"tree": key, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        small_stores.gated_add(ledger(), row)
    except sqlite3.Error as e:
        raise OSError(f"commit-gated: {e}") from e


def stamped() -> set[str]:
    """The key of every commit a commit gate passed."""
    import small_stores

    return {str(r.get("tree")) for r in small_stores.gated(ledger()) if r.get("tree")}


def unstamped(top: str | Path, base: str = "main") -> list[tuple[str, str, str]]:
    """(sha, subject, key) of each commit of HEAD's own range whose key no commit gate stamped,
    oldest first; a key that cannot be read is "" and stamped by nothing; [] when the base cannot
    be found."""
    mb = _git(top, "merge-base", "HEAD", base)
    if mb.returncode != 0:
        return []
    listed = _git(top, "log", "--reverse", "--no-merges", "--format=%H%x09%P%x09%s",
                  f"{mb.stdout.strip()}..HEAD")  # fmt: skip
    done = stamped()
    out = []
    for line in listed.stdout.splitlines():
        sha, parent, subject = line.split("\t", 2)
        key = ""
        with contextlib.suppress(RuntimeError):
            key = commit_key.key(top, parent or commit_key.EMPTY_TREE, sha)
        if key not in done:  # nor is "" ever there (`stamped`)
            out.append((sha, subject, key))
    return out


def width(gate_class: str | None = None) -> int:
    """How many commit gates run at once: the share of the gate that asks (its admission's, else
    counted from the slots as one more of GATE_CLASS), never the machine."""
    if os.environ.get(worker_share.ENV, "").strip():
        return worker_share.share()
    where = os.path.realpath(worker_share.slot_dir(os.environ))
    return land_share.share_of(where, dict(os.environ), worker_share.cores(),
                               worker_share.own(where), gate_class)  # fmt: skip


def _gate_env() -> dict[str, str]:
    """The caller's environment less git's bindings: the gate judges the worktree it runs in."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class _Seat:
    """One worker's worktree, made at its first commit and moved to each after it."""

    def __init__(self, top: str | Path, wt: Path) -> None:
        self.top, self.wt, self.env, self.made = top, wt, _gate_env(), False

    def _put(self, sha: str) -> str:
        """The worktree at SHA's parent, SHA's change staged and nothing else; else why not."""
        if not self.made:
            step = _git(self.top, "worktree", "add", "-q", "--detach", str(self.wt), f"{sha}^",
                        env=self.env)  # fmt: skip
            if step.returncode != 0:
                return f"no worktree at {sha[:10]}^: {step.stderr.strip()[:300]}"
            self.made = True
        for words in (("checkout", "-q", "-f", "--detach", f"{sha}^"), ("clean", "-ffdxq"),
                      ("read-tree", "-m", "-u", "HEAD", sha)):  # fmt: skip
            step = _git(self.wt, "-c", "core.hooksPath=/dev/null", *words, env=self.env)
            if step.returncode != 0:
                return f"git {words[0]} for {sha[:10]}: {step.stderr.strip()[:300]}"
        return ""

    def gate(self, sha: str, argv: list[str]) -> tuple[int, str]:
        """ARGV run as SHA's pre-commit would: (exit code, the output's tail)."""
        if why := self._put(sha):
            return 2, why
        code, said, _ = check_jobs.run(argv, self.wt, self.env, check_jobs.STRETCH_CAP_S)
        return code, "\n".join(said.strip().splitlines()[-15:])

    def close(self) -> None:
        _git(self.top, "worktree", "remove", "--force", str(self.wt), env=self.env)


def _work(top: str | Path, todo: queue.SimpleQueue, argv: list[str],
          say: Callable[[str], None]) -> dict[str, str]:  # fmt: skip
    """One worker: each commit of TODO in its turn, in this worker's worktree; {sha: refusal}."""
    tmp = Path(tempfile.mkdtemp(prefix="goh-commit-gate-", dir=tmp_root.goh_tmp()))
    seat, out = _Seat(top, tmp / "wt"), {}
    with check_jobs.worktree(top, seat.wt, tmp):  # dies with the check (GOH.CHECKVERDICT)
        try:
            while True:
                try:
                    sha, subject, key = todo.get_nowait()
                except queue.Empty:
                    return out
                say(f"commit gate on {sha[:10]} {subject[:60]}: no commit gate stamped it")
                code, tail = seat.gate(sha, argv)
                if code == 0:
                    out[sha] = ""
                    say(f"commit gate on {sha[:10]}: green")
                    if key:
                        with contextlib.suppress(OSError):  # unstamped, it is only judged again
                            record(key)
                    continue
                why = "no commit gate judged (a cherry-pick, a rebase or --no-verify)"
                out[sha] = f"the commit gate on {sha[:10]} ({subject[:60]}), which {why}:\n{tail}"
        finally:
            seat.close()
            shutil.rmtree(tmp, ignore_errors=True)


def check(
    top: str | Path,
    say: Callable[[str], None],
    argv: list[str] | None = None,
    base: str = "main",
    gate_class: str | None = None,
) -> list[str]:
    """Run the commit gate on each unstamped commit of HEAD's range, `width` at once; the refusals
    in the range's order, each naming its commit. A green run stamps the commit."""
    owed = unstamped(top, base)
    if not owed:
        return []
    todo: queue.SimpleQueue = queue.SimpleQueue()
    for item in owed:
        todo.put(item)
    n = min(len(owed), width(gate_class))
    with ThreadPoolExecutor(max_workers=n) as pool:
        jobs = [pool.submit(_work, top, todo, argv or GATE_ARGV, say) for _ in range(n)]
    ended = {sha: red for job in jobs for sha, red in job.result().items()}
    return [red for sha, _, _ in owed if (red := ended[sha])]  # one nobody judged is no green


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "record":
        import run_path  # the index's tree from a private copy: never the shared index.lock

        try:
            key = commit_key.staged(argv[1], run_path.index_tree(argv[1]))
        except RuntimeError as e:
            print(f"commit_stamp: no commit to stamp: {e}", file=sys.stderr)
            return 1
        record(key)
        return 0
    print("usage: commit_stamp.py record REPO", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
