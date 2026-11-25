"""The display-seam probe's case trees, in memory (GOH.FSCHURN3, GOH.FSEVQUIET).

Split out of checks/_display_seam_probe.py (the file-length cap). Each probe wrote ~500 files and
dirs and removed them again: 1,036 file events of every `structural.sh --full`, and a suite's ~50
probes (each rule-drop calibration, in tests/test_display_seam.py and tests/test_display_frames.py,
is a whole probe) shared one copy of them. The checker only reads a case, through four names of its
own: `held_in_memory` answers those for one tree, and nothing is written. One case per probe is
still a directory read through real git.

Lib of `_display_seam_probe.py` (GOH.FSCHURN3): the probe's case trees, which the checker's own
readers are handed from memory; one case is a directory, read through real git.
"""

from __future__ import annotations

import contextlib
import json
import os


def case_tree(files, policy, seam, base):
    """A case's files, as written: the seam file is present in every fixture unless a case says
    otherwise, because the policy names it and a policy naming a seam that does not exist is exit
    2 -- which would mask the rule the case is about."""
    tree = {"Core/DisplayPolicy.swift": seam, **files}
    merged = dict(base)
    merged.update(policy)
    tree["policy.json"] = json.dumps(merged)
    return tree


def config_tree(policy, quiet, seam, base):
    """A config case's: TWO clean files, so that with a config rule dropped the run still has
    something to examine. One file, when that file is the excluded one, made the zero-scan refusal
    produce the same exit 2 the case was asserting -- two rules, one verdict."""
    return case_tree({"Core/quiet.swift": quiet, "UI/win.swift": quiet}, policy, seam, base)


def write_tree(root, tree):
    for rel, body in tree.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)


@contextlib.contextmanager
def held_in_memory(checker, tree):
    """The checker's rules over `tree`, which is on no disk: its listing (what `ls-files --cached
    --others` lists for a fixture with no ignores), its reads and its policy are the tree's. Each
    case cost two git spawns and a `.git` copy -- 31 cases, and the rule-drop calibration runs the
    whole probe 24 more times: 44 s of the suite (tools/session_bench.py, 2026-10-06) -- and then
    its files. The listing is `_gitutil`'s, proven by its own tests; a file's absence is asked on a
    real disk by tests/test_display_seam.py."""
    blobs = {rel: body.encode("utf-8") for rel, body in tree.items()}
    stand_ins = {
        "repo_root": lambda: None,  # main() falls back to the working directory
        "listed_files": lambda root, staged=False: sorted(blobs),
        "content_bytes": lambda root, rel, staged=False: blobs.get(rel),
        "is_file": lambda root, rel: rel in blobs,
        "policy_text": lambda path: tree[path],
    }
    saved = {name: getattr(checker, name) for name in stand_ins}
    vars(checker).update(stand_ins)
    try:
        yield
    finally:
        vars(checker).update(saved)
