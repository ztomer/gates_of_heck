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


def _git(*args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, env=env,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()  # fmt: skip


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: goh lint <path>...", file=sys.stderr)
        return 2
    try:
        root = _git("rev-parse", "--show-toplevel")
        index = _git("rev-parse", "--path-format=absolute", "--git-path", "index")
    except subprocess.CalledProcessError:
        print("✗ goh lint: not inside a git work tree", file=sys.stderr)
        return 2
    missing = [p for p in argv if not os.path.lexists(p)]
    if missing:
        print(f"✗ goh lint: no such path: {', '.join(missing)}", file=sys.stderr)
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
            print(f"✗ goh lint: git add refused: {error.stderr.strip()}", file=sys.stderr)
            return 2
        env["GOH_COMMIT_LINT"] = "1"
        return subprocess.run(
            ["bash", GATE], cwd=root, env=env, stdin=subprocess.DEVNULL, check=False
        ).returncode
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
