"""The display-seam probe's case trees, written once per content and shared (GOH.FSCHURN3).

Split out of checks/_display_seam_probe.py (the file-length cap). Each probe wrote ~600 files and
dirs and removed them again, and one suite runs it ~50 times (each rule-drop calibration, in
tests/test_display_seam.py and tests/test_display_frames.py, is a whole probe): 4% of the suite's
file events. The checker only reads a case, so every listed-directly case is written ONCE per
content into `goh_tmp()` and read by every later probe.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil

from _goh_tmp import goh_tmp


def case_tree(files, policy, seam, base):
    """A case's files, as written: the seam file is present in every fixture unless a case says
    otherwise, because the policy names it and a policy naming a seam that does not exist is exit
    2 -- which would mask the rule the case is about."""
    tree = {"Core/DisplayPolicy.swift": seam, **files}
    merged = dict(base)
    merged.update(policy)
    tree["policy.json"] = json.dumps(merged)
    return tree


def write_tree(root, tree):
    for rel, body in tree.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
    return list(tree)


def shared_cases(cases, config_cases, quiet, seam, base):
    """(the shared directory, {case name: its listed files}) for every case but case 0 and every
    config case. The name is the trees' own hash and the tree is published by one rename, so a
    directory that exists is whole and right; under the suite it goes with the run's root,
    elsewhere with the backstop sweep of goh_tmp (lib/prune_kept.py)."""
    trees = {
        f"case{n:02d}": case_tree(f, p, seam, base) for n, (_, f, p, _) in enumerate(cases) if n
    }
    trees.update(
        (
            f"cfg{n:02d}",
            case_tree({"Core/quiet.swift": quiet, "UI/win.swift": quiet}, p, seam, base),
        )
        for n, (_, p) in enumerate(config_cases)
    )
    key = hashlib.sha256(json.dumps(trees, sort_keys=True).encode()).hexdigest()[:16]
    done = os.path.join(goh_tmp(), f"goh-display-seam-cases.{key}")
    if not os.path.isdir(done):
        stage = f"{done}.build-{os.getpid()}"
        shutil.rmtree(stage, ignore_errors=True)
        for name, tree in trees.items():
            write_tree(os.path.join(stage, name), tree)
        try:
            os.rename(stage, done)
        except OSError:  # another probe published it first: the same bytes
            shutil.rmtree(stage, ignore_errors=True)
    return done, {name: list(tree) for name, tree in trees.items()}
