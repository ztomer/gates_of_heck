#!/usr/bin/env python3
"""lint.py -- `goh lint <path>...`: the commit gate's verdict on these files, BEFORE the commit.

GOH.CLASS.PARITY (owner ruling 2026-11-15, "Yes, close classes"): an agent ran plain `ruff check`,
saw green, and the commit gate refused the same file -- the hook judges with the house rule set,
per-file ceilings and the pinned ruff, and plain ruff knows none of them. A second implementation of
"what the commit runs" would drift the day the hook gains a step, so this has none: it builds the
index `git commit -- <path>...` would commit (the repository's index, plus these paths) in a
temporary index file, and runs gates/commit_gate.sh itself over it, the way git runs the hook for a
`commit <paths>` -- GIT_INDEX_FILE set to that temporary index, which the hook carries as the index
being committed (gates/_git_env.sh). Same program, same index, same verdict.

Under GOH_COMMIT_LINT=1 the gate skips only what is not a verdict on files: the repo-hold and
landed-only refusals (a lint on main is not a commit to main) and the seed settle (a lint writes no
private store; the journal goes with its temporary directory). Nothing is committed, and the
repository's own index is never written.

    goh lint gates/x.py tests/test_x.py     # exit: the commit gate's
    goh lint                                # every path changed against HEAD (GOH.LINTASGATE)

With no path it lints what a lane changed: every tracked file that differs from HEAD and every
untracked file git does not ignore, so the one entrypoint needs no list typed by hand. A finding the
file carried at HEAD is labelled `baseline` by the gate's own lint step: it is not the change's.

`goh lint <path>...`: the commit gate's verdict on these paths before the commit
(GOH.CLASS.PARITY). No second implementation: `gates/commit_gate.sh` itself, over the index `git
commit -- <path>...` would commit (a temporary index file, carried as the hook carries one), under
`GOH_COMMIT_LINT=1`, which skips only the hold and landed-only refusals and the seed settle. Pinned
by `test_goh_lint.py` (same verdict as the real commit on a planted E501, a planted SIM105 and a
clean file).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(os.path.dirname(HERE), "gates", "commit_gate.sh")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
from tmp_root import goh_tmp  # noqa: E402  # never Spotlight-indexed (GOH.TMPNOINDEX)

sys.path.insert(0, os.path.dirname(HERE))  # the goh root: tui.lib
from tui.lib import err, ok  # noqa: E402


def _git(*args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, env=env,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()  # fmt: skip


def changed(root: str) -> list[str]:
    """Every path that differs from HEAD (deletions aside) and every untracked, unignored file,
    as paths relative to the cwd."""
    diff = _git("-C", root, "diff", "--name-only", "-z", "--diff-filter=d", "HEAD")
    new = _git("-C", root, "ls-files", "-z", "--others", "--exclude-standard")
    rels = sorted({p for p in (diff + "\0" + new).split("\0") if p})
    return [os.path.relpath(os.path.join(root, p)) for p in rels]


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: goh lint [<path>...]  (none: every path changed vs HEAD)", file=sys.stderr)
        return 2
    try:
        root = _git("rev-parse", "--show-toplevel")
        index = _git("rev-parse", "--path-format=absolute", "--git-path", "index")
        if not argv:
            argv = changed(root)
    except subprocess.CalledProcessError:
        err("goh lint: not inside a git work tree")
        return 2
    if not argv:
        ok("goh lint: nothing changed against HEAD, so nothing to judge")
        return 0
    missing = [p for p in argv if not os.path.lexists(p)]
    if missing:
        err(f"goh lint: no such path: {', '.join(missing)}")
        return 2
    paths = [os.path.relpath(os.path.abspath(p), root) for p in argv]
    scratch = tempfile.mkdtemp(dir=goh_tmp(), prefix="goh-lint.")
    try:
        temp_index = os.path.join(scratch, "index")
        if os.path.exists(index):
            shutil.copyfile(index, temp_index)
        env = {**os.environ, "GIT_INDEX_FILE": temp_index}
        try:
            _git("-C", root, "add", "--", *paths, env=env)
        except subprocess.CalledProcessError as error:
            err(f"goh lint: git add refused: {error.stderr.strip()}")
            return 2
        env["GOH_COMMIT_LINT"] = "1"
        return subprocess.run(
            ["bash", GATE], cwd=root, env=env, stdin=subprocess.DEVNULL, check=False
        ).returncode
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
