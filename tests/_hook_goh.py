"""A minimal gates_of_heck a stock hook can run: gates/hook.sh and its source closure (the HEAD
trampoline, the git-env helpers, the bash 5 guard, the house interpreter, tui/), copied by the one
builder that reads what it sources. Tests that fake a goh add their own gate scripts beside it
(GOH.CLASS.ROLLOUT)."""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import source_closure

sys.path.pop(0)


def hook_goh(root: Path) -> Path:
    # hook.sh RUNS the commit hold's check before the commit gate (GOH.HOLDSUBPROC): run, not
    # sourced, so the closure is told it by name.
    source_closure.copy_closure(root, "gates/hook.sh", extra=("lib/commit_hold.py", "tui/lib.py"))
    return root
