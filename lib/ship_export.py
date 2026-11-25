"""The checkout goh ship's verify step judges: a clean export of the SHIPPED commit, never the live
checkout (GOH.CLASS.CHECKOUT).

The claim card (`loopctl verify NAME`) reads `LOOPCTL_ROOT/NAME`: its branch against the remote, its
`git status`, its tags, its declared installs, its pre-push hook. Run on the live checkout it judged
whatever else was in that tree: v0.116.0's ship failed at verify on a `bin/goh.*.staging` another
build wrote and removed ("working tree: 1 uncommitted"), and any lane's stray could do the same.
So verify builds `<temp>/NAME`, a `git clone --shared` of the repository (objects borrowed, so it
is quick) with the ship's remote under the same name and URL, the shipped branch checked out at
the shipped sha and tracking that remote's branch, the repository's own pre-push hook (the card
reads it as the local gate record), and runs the card with LOOPCTL_ROOT at `<temp>`. The temp root
sits outside every work tree and is removed after. Pinned by tests/test_ship_export.py.

The checkout goh ship's verify judges (GOH.CLASS.CHECKOUT): a `git clone --shared` of the repo
under a temp root, at the shipped sha, on the shipped branch tracking the ship's remote (same name
and URL), with the repo's pre-push hook; the card runs with LOOPCTL_ROOT there, so a stray in the
live checkout never fails a ship.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import gitrun
from tmp_root import goh_tmp  # every temp root under $TMPDIR/goh.noindex (GOH.TMPNOINDEX)


class ExportError(Exception):
    """The export could not be made; the message says which git step refused."""


def _git(where: Path | None, *args: str, write: bool = False) -> str:
    """`git ARGS` in WHERE (a reader, or with WRITE a writer under its own deadline), its output."""
    done = (gitrun.run_writer(args, repo=where, deadline_s=300) if write
            else gitrun.run_reader(args, repo=where, timeout=120))  # fmt: skip
    if done is None or done.returncode != 0:
        why = "stopped at its deadline" if done is None else done.stderr.strip()[:200]
        raise ExportError(f"git {' '.join(args)} in {where or '.'}: {why}")
    return done.stdout.strip()


@contextlib.contextmanager
def exported(repo: Path, name: str, sha: str, remote: str, branch: str) -> Iterator[Path]:
    """The LOOPCTL_ROOT under which NAME is a clean checkout of SHA (see the module doc)."""
    root = Path(tempfile.mkdtemp(dir=goh_tmp(), prefix="goh-ship-verify."))
    try:
        co = root / name
        co.parent.mkdir(parents=True, exist_ok=True)
        _git(None, "clone", "-q", "--shared", "--no-checkout", "-o", remote, str(repo), str(co),
             write=True)  # fmt: skip
        _git(co, "remote", "set-url", remote, _git(repo, "remote", "get-url", remote), write=True)
        tracked = gitrun.run_reader(
            ("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}"),
            repo=repo,
            timeout=60,
        )
        _git(co, "update-ref", f"refs/remotes/{remote}/{branch}", tracked.stdout.strip() or sha,
             write=True)  # fmt: skip
        _git(co, "checkout", "-q", "-B", branch, sha, write=True)
        _git(co, "branch", "-q", "--set-upstream-to", f"{remote}/{branch}", write=True)
        hook = repo / _git(repo, "rev-parse", "--git-path", "hooks/pre-push")
        mine = co / _git(co, "rev-parse", "--git-path", "hooks/pre-push")
        if hook.is_file() and mine.resolve() != hook.resolve():  # a shared core.hooksPath is one
            mine.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(hook, mine)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
