"""The display-seam probe's case trees, written once per content and shared (GOH.FSCHURN3).

Split out of checks/_display_seam_probe.py (the file-length cap). Each probe wrote ~600 files and
dirs and removed them again, and one suite runs it ~50 times (each rule-drop calibration, in
tests/test_display_seam.py and tests/test_display_frames.py, is a whole probe): 4% of the suite's
file events. The checker only reads a case, so inside a run that removes its own temp root (the
goh suite's: GATES_OF_HECK_SUITE_TMP is this TMPDIR, tests/suite_tmp.py) every listed-directly
case is written ONCE per content into `goh_tmp()` and read by every later probe. Anywhere else (a
real gate, a test with a TMPDIR of its own) they go in the probe's own directory and leave with it:
a gate leaves nothing in the temp directory (tests/test_gate_temp_leaks.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile

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


RUN_TMP = "GATES_OF_HECK_SUITE_TMP"  # tests/suite_tmp.py MARKER: the run's root, removed by it


def _run_owned() -> bool:
    run = os.environ.get(RUN_TMP, "")
    return bool(run) and os.path.realpath(run) == os.path.realpath(tempfile.gettempdir())


def shared_cases(cases, config_cases, quiet, seam, base, workdir):
    """(the cases' directory, {case name: its listed files}) for every case but case 0 and every
    config case: the run's shared copy (`_run_owned`), named for the trees' own hash and published
    by one rename, so a directory that exists is whole and right; else under `workdir`."""
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
    if not _run_owned():
        done = os.path.join(workdir, "cases")
        for name, tree in trees.items():
            write_tree(os.path.join(done, name), tree)
        return done, {name: list(tree) for name, tree in trees.items()}
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
