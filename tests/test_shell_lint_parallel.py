"""`goh shell-lint` runs its shellcheck calls side by side (GOH.COVDEDUPE).

One per file, one after another: a cold lint of goh's own 84 files held one core for 60 s, paid in
full by `structural --full` and by the self-hosted hook test (whose HOME, and so verdict cache, is
fresh), 181.7 s on the hosted runner for the same pass in another test. The calls are independent
-- each file reads what it sources, whoever shares the run -- so they run one thread per core.
Pinned behaviourally: a fake shellcheck that only passes when another call is running beside it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _tool_versions import floor
from conftest import commit_all, run_goh, write

SC_FLOOR = floor("shellcheck")

pytestmark = pytest.mark.skipif((os.cpu_count() or 1) < 2, reason="needs two cores")


def _overlap_shellcheck(tmp: Path) -> Path:
    """Passes a file only if a second call starts within 20 s of it; else an error line."""
    bin_ = tmp / "fake-sc"
    bin_.mkdir(parents=True)
    marks = tmp / "marks"
    marks.mkdir()
    sc = bin_ / "shellcheck"
    sc.write_text(
        "#!/bin/bash\n"
        f'[ "${{1:-}}" = --version ] && {{ printf "ShellCheck\\nversion: {SC_FLOOR}\\n"; exit; }}\n'
        f'marks="{marks}"; : > "$marks/$$"\n'
        "for _ in $(seq 200); do\n"
        '  [ "$(ls "$marks" | wc -l)" -ge 2 ] && exit 0\n'
        "  sleep 0.1\n"
        "done\n"
        'for f; do :; done; echo "$f:1:1: error: ran alone, no call beside it [SC1000]"\n'
    )
    sc.chmod(0o755)
    return bin_


def test_the_shellcheck_calls_of_one_lint_run_side_by_side(repo, tmp_path):
    for name in ("a.sh", "b.sh"):
        write(repo, name, "#!/usr/bin/env bash\necho hi\n")
    commit_all(repo)
    env = {k: v for k, v in os.environ.items() if k != "SHELLCHECK_OPTS"}
    env["GOH_VERDICT_CACHE"] = "0"
    # Two of a private pool's tokens: the machine's could all be held by another run's calls.
    env["GOH_SHELLCHECK_SLOTS"] = "2"
    env["GOH_SHELLCHECK_SLOT_DIR"] = str(tmp_path / "sc-slots")
    env["PATH"] = f"{_overlap_shellcheck(tmp_path)}{os.pathsep}{env['PATH']}"
    r = run_goh(repo, "shell-lint", env=env)
    out = r.stdout + r.stderr
    assert "ran alone" not in out, out
    assert r.returncode == 0, out
