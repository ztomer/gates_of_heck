"""This checkout's own source, as a policy test over it must list it."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def own_source_files(*specs: str) -> list[str]:
    """This checkout's files for a policy test over its OWN source: tracked AND untracked (not
    ignored), as `structural --full` lists them. Plain `git ls-files` skipped a new, untracked
    script, so the suite passed locally and the push of its commit failed
    (`gates/sequencer_gate.sh` unguarded, 2026-10-08)."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--cached", "--others",
         "--exclude-standard", "--", *specs],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return sorted({f for f in out.split("\0") if f and (REPO_ROOT / f).is_file()})
