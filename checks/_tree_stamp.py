"""A tree's file identities, for a check that must leave the tree it was run over as it found it.

`check_probes_pass.py` runs every self-proof in parallel over the live tree; a probe that plants a
file and restores it keeps the bytes (and git status) but changes the inode and mtime a parallel
reader held (koffee_big, 2026-10-08). Stamped before and after, the change is seen.
"""

from __future__ import annotations

import os

from _gitutil import tree_files
from tui.lib import err, info


def stamp(root):
    """`(inode, mtime_ns, size)` of every file the tree holds. A probe that plants a file and
    restores it leaves the bytes -- and git status -- as they were, but not these."""
    out = {}
    for rel in tree_files(root):
        try:
            st = os.lstat(os.path.join(root, rel))
        except OSError:
            continue
        out[rel] = (st.st_ino, st.st_mtime_ns, st.st_size)
    return out


def touched(before, after):
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def who_touches(root, probes, run_one):
    """Each probe alone (`run_one(path, flag, cwd=root)`), the tree stamped around it: the parallel
    run cannot say which one."""
    found = []
    for path, flag in probes:
        mark = stamp(root)
        run_one(path, flag, cwd=root)
        if touched(mark, stamp(root)):
            found.append(path)
    return found


def tree_changed(root, before, probes, run_one):
    """Name what the probes changed in `root` since `before`, and which probe; True when any.

    The probes run TOGETHER over the live tree, so one that writes into it races every other
    probe reading it, and, killed mid-plant, leaves the owner's source deleted."""
    changed = touched(before, stamp(root))
    if not changed:
        return False
    for path in who_touches(root, probes, run_one):
        err(f"{os.path.relpath(path, root)} --probe changes the tree it was given")
    more = f" and {len(changed) - 5} more" if len(changed) > 5 else ""
    err(f"the self-proofs changed {len(changed)} file(s) in {root}: {', '.join(changed[:5])}{more}")
    info("A probe works on its own copy (a temp dir), never the tree it was run over: the probes")
    info("run in parallel, and a restore keeps the bytes but not the inode a reader held.")
    return True
