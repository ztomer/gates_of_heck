"""tools/profiling entry-point scripts run via DIRECT execv (no shell).

Red proof: all four had a comment header PREPENDED above their shebang (a
repo-canonicalization commit pasted a banner on top), so the interpreter
line was no longer byte offset 0 — direct execv failed with ENOEXEC
(python subprocess raises OSError: Exec format error). They only worked
when invoked through a shell, which hid the breakage. _target_root.sh is
sourced-only (not executable) and deliberately has no shebang.
"""

import os
import subprocess

import pytest

from conftest import REPO_ROOT

ENTRY_POINTS = [
    "tools/profiling/profile_cpu.sh",
    "tools/profiling/profile_memory.sh",
    "tools/profiling/quick_profile.sh",
    "tools/profiling/soak_profile.sh",
]


def test_shebang_is_on_line_one():
    for rel in ENTRY_POINTS:
        first = (REPO_ROOT / rel).read_text().splitlines()[0]
        assert first.startswith("#!"), f"{rel}: shebang not on line 1 ({first!r})"


@pytest.mark.parametrize("rel", ENTRY_POINTS)
def test_direct_execv_starts_the_interpreter(rel, tmp_path):
    # NO shell=True and no bash prefix: subprocess execv's the script itself,
    # so a missing line-1 shebang fails this test with an OSError instead of
    # a mere nonzero exit.
    env = dict(os.environ)
    # Empty target repo: no CadGoose present, every script takes its named
    # early-exit path instead of recording traces anywhere real.
    env["GOH_PROFILE_TARGET"] = str(tmp_path)
    # soak_profile.sh's "is CadGoose running" probe is pgrep -f, which matches
    # ANY stray machine process carrying the name in its argv — under one, the
    # script enters its sampling loop for DURATION_MINUTES (default 30) and
    # blows this test's timeout. Duration 0 keeps the execv proof while making
    # the run independent of unrelated machine state.
    args = ["0"] if rel.endswith("soak_profile.sh") else []
    r = subprocess.run(
        [str(REPO_ROOT / rel), *args],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode != 126, "found but not executable"
