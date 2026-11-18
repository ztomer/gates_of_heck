"""The fresh full instrumented run a ship stands on (GOH.COVCARRY, the owner's term 2).

Owner, 2026-11-17, the terms for carrying coverage profiles across trees (D-0011 option 4): "a
fresh full instrumented run happens on main's tip before every ship". The idle lander makes it
(lib/idle_suite.py INSTRUMENTED: the coverage gate's Rust mode, its external suite against the
instrumented binary); the suite's record says its run was instrumented (lib/reuse.py
record_suite); and `reuse.py full-suite` -- the release kit's check (tools/release_goh.sh) -- asks
`full_suite` here, which refuses a tip whose full record drove the plain binary. Split from
lib/reuse.py for its line cap; it reads the `carry` path's record through that module only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_identity  # lib/: the suite's tools key
import reuse  # lib/: the one store


def full_suite(repo: Path, sha: str) -> tuple[dict | None, str]:
    """A FULL suite verdict on sha's tree under today's tools: a run that judged every test there
    itself, against the INSTRUMENTED binary. The release kit refuses a tip without one
    (GOH_SHIP_FULL_SUITE): a land whose suite carried verdicts from another tree is no full run,
    however green, and a plain full run is not the fresh instrumented run every ship stands on
    (GOH.COVCARRY: the owner's term 2, made by lib/idle_suite.py; the shadow of the coverage
    carry, lib/prof_carry.py)."""
    tree = reuse._git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}")
    if not tree or not reuse.repo_id(repo):
        return None, f"{sha} names no commit tree in {repo}"
    rec, why = reuse.lookup_suite(repo, tree, gate_identity.suite_tools())
    if rec is None:
        return None, f"no suite run recorded on tree {tree[:12]} under these tools ({why})"
    if not rec.get("full"):
        carried = rec.get("carried") or 0
        how = f"{carried} tests carried from another tree" if carried else "a partial or red run"
        return None, f"the suite record of tree {tree[:12]} is no full run ({how})"
    if not rec.get("instrumented"):
        return None, (f"the full run of tree {tree[:12]} drove the plain binary: a ship needs a "
                      "fresh INSTRUMENTED full run (lib/idle_suite.py)")  # fmt: skip
    return rec, ""
