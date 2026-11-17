"""A tree's own copy for a test that SWEEPS it: the sweep stamps the tree around its probes.

`check_probes_pass.py` stamps every file of the tree it is given (inode, mtime, size) before and
after it runs the self-proofs, and is red when anything moved -- by design, a probe that writes
into the tree races every other probe. Handed the LIVE checkout under `pytest -n auto`, it also
answers for every sibling worker that touches that checkout meanwhile (a worktree, a tool's
state file, an untracked artifact): `test_the_gate_agrees_with_the_reader_on_this_repo` failed
once under xdist and passed alone (GOH.CALIBXDIST). A test that judges the tree works on a COPY
(tests/_tree_guard.py): this makes it, once, from what the tree holds at that moment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init

ROOT = Path(__file__).resolve().parent.parent
IDENTITY = ("-c", "user.name=goh-test", "-c", "user.email=goh-test@example.invalid")


def snapshot(src: Path, dest: Path) -> Path:
    """`dest`: a git repository holding exactly the files `src` holds (tracked plus untracked
    and not ignored), as they are now. Nothing ignored (`target/`, `.git`) is read."""
    sys.path.insert(0, str(ROOT / "checks"))
    from _gitutil import tree_files

    for rel in tree_files(str(src)):
        source = src / rel
        if not source.is_file() and not source.is_symlink():
            continue  # a sibling removed it between the listing and the copy
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    fast_init(dest)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for argv in (("add", "-A"), (*IDENTITY, "commit", "-q", "--no-verify", "-m", "snapshot")):
        subprocess.run(
            ["git", "-C", str(dest), *argv],
            check=True,
            capture_output=True,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    return dest
